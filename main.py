"""
ScalperBot - Main Entry Point
Cryptocurrency momentum breakout trading bot
"""
import asyncio
import logging
import signal
import sys
from datetime import datetime, timezone

from config import settings
from db import TradeDB
from exchanges.adapter import MEXCAdapter
from datafeed.candle_store import CandleStore
from datafeed.orderbook import OrderBook
from datafeed.rest_poller import RESTPoller
from strategies.momentum_breakout import MomentumBreakoutStrategy
from exec.router import OrderRouter
from exec.position_manager import PositionManager
from ops.pos_size import PositionSizer
from risk.breaker import RiskBreaker
from notifications.telegram import TelegramNotifier

# Configure logging
logging.basicConfig(
    level=getattr(logging, settings.log_level.upper()),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(settings.log_file),
        logging.StreamHandler()
    ]
)

logger = logging.getLogger(__name__)


class ScalperBot:
    """
    Main trading bot class
    Orchestrates all components and runs trading loop
    """

    def __init__(self):
        logger.info("="*80)
        logger.info("ScalperBot Initializing...")
        logger.info(f"Mode: {'DRY_RUN' if settings.dry_run else 'LIVE'}")
        logger.info(f"Trading pairs: {settings.trading_pairs}")
        logger.info("="*80)

        # Initialize components
        self.db = TradeDB(settings.database_path)
        self.exchange = MEXCAdapter(
            settings.mexc_api_key,
            settings.mexc_api_secret,
            dry_run=settings.dry_run
        )
        self.candle_store = CandleStore()
        self.orderbook = OrderBook()
        self.poller = RESTPoller(
            self.exchange,
            self.candle_store,
            self.orderbook,
            settings.trading_pairs,
            settings.data_poll_interval
        )
        self.strategy = MomentumBreakoutStrategy(self.candle_store)
        self.router = OrderRouter(self.exchange, self.orderbook)
        self.position_sizer = PositionSizer()
        self.risk_breaker = RiskBreaker(self.db)

        # Position manager for exit logic (TP/SL)
        self.position_manager = PositionManager(
            db=self.db,
            exchange=self.exchange,
            take_profit_pct=getattr(settings, 'take_profit_pct', 1.5),
            stop_loss_pct=getattr(settings, 'stop_loss_pct', 1.0)
        )

        # Initialize Telegram notifier
        self.telegram = TelegramNotifier(
            bot_token=settings.telegram_bot_token,
            chat_id=settings.telegram_chat_id,
            status_interval_min=settings.status_interval_min,
            early_warn_pct=settings.early_warn_pct,
            early_warn_cooldown_min=settings.early_warn_cooldown_min,
            status_enabled=settings.status_enabled
        )

        if self.telegram.enabled:
            logger.info(f"Telegram notifications: ENABLED")
            logger.info(f"  Status reports every {settings.status_interval_min} min")
            logger.info(f"  Early warning at {settings.early_warn_pct}% from breakout")
        else:
            logger.info("Telegram notifications: DISABLED")

        # State
        self.running = False
        self.poller_task = None
        self.usdt_balance = 0.0

    async def initialize(self):
        """Initialize bot (fetch balance, set risk params, etc.)"""
        logger.info("Initializing bot components...")

        # Get starting balance for risk breaker
        try:
            balance = self.exchange.fetch_balance()
            self.usdt_balance = balance.get('USDT', {}).get('free', 0)
            self.risk_breaker.set_starting_balance(self.usdt_balance)
            logger.info(f"USDT Balance: ${self.usdt_balance:.2f}")
        except Exception as e:
            logger.warning(f"Could not fetch balance: {e}")
            self.usdt_balance = 1000.0
            self.risk_breaker.set_starting_balance(self.usdt_balance)

        # Sync open positions from database
        # In LIVE mode, verify positions actually exist on exchange to avoid phantom positions
        verify_on_exchange = not settings.dry_run
        if verify_on_exchange:
            logger.info("LIVE MODE: Verifying positions exist on exchange before loading...")
        self.position_manager.sync_from_db(verify_on_exchange=verify_on_exchange)
        logger.info(self.position_manager.get_position_summary())

        logger.info("Initialization complete")

        # Send Telegram startup notification
        mode = "DRY_RUN" if settings.dry_run else "LIVE"
        await self.telegram.notify_bot_started(mode, settings.trading_pairs, self.usdt_balance)

    async def trading_loop(self):
        """Main trading loop - runs strategy and executes trades"""
        logger.info(f"Trading loop started (interval: {settings.strategy_interval}s)")

        cycle = 0
        while self.running:
            cycle += 1
            now = datetime.now(timezone.utc)
            logger.info(f"\n{'='*80}")
            logger.info(f"Strategy Cycle #{cycle} - {now.isoformat()}")
            logger.info(f"{'='*80}")

            try:
                # Check risk breaker
                if not self.risk_breaker.can_trade():
                    logger.error("Risk breaker active - skipping trading")
                    pnl, pnl_pct = self.risk_breaker.get_daily_pnl()
                    await self.telegram.notify_risk_breaker(pnl, pnl_pct, settings.daily_loss_limit_pct)
                    await asyncio.sleep(settings.strategy_interval)
                    continue

                # Display data summary
                logger.info(self.candle_store.summary())
                logger.info(self.orderbook.summary())
                logger.info(self.position_manager.get_position_summary())

                # Get current prices for exit checking
                current_prices = self._get_current_prices()

                # Check and execute exits (TP/SL)
                await self._check_and_execute_exits(current_prices)

                # Collect breakout data for status/early warnings
                pairs_data = await self._collect_pairs_data()

                # Check for early warnings
                await self._check_early_warnings(pairs_data)

                # Send hourly status if due
                if self.telegram.should_send_status():
                    await self._send_status_report(pairs_data)

                # Run strategy for all symbols
                signals = self.strategy.run_for_all_symbols(settings.trading_pairs)

                # Execute signals (skip pairs with open positions)
                for signal in signals:
                    symbol = signal['symbol']

                    # Skip if already have open position
                    if self.position_manager.has_open_position(symbol):
                        logger.info(f"Skipping {symbol} signal - already have open position")
                        continue

                    # Notify signal via Telegram
                    await self.telegram.notify_signal(signal)
                    await self.execute_signal(signal)

            except Exception as e:
                logger.error(f"Error in trading loop: {e}", exc_info=True)

            # Sleep until next cycle
            await asyncio.sleep(settings.strategy_interval)

    async def _collect_pairs_data(self) -> list:
        """Collect current price and breakout data for all pairs"""
        pairs_data = []

        for symbol in settings.trading_pairs:
            try:
                df = self.candle_store.get_candles(symbol, '5m', limit=50)
                if df.empty or len(df) < settings.green4_breakout_period + 1:
                    continue

                current_price = df.iloc[-1]['close']

                # Calculate breakout level (same as strategy)
                lookback = df.iloc[-(settings.green4_breakout_period+1):-1]
                highest_high = lookback['high'].max()
                buffer = highest_high * (settings.green4_breakout_buffer_bps / 10000)
                breakout_level = highest_high + buffer

                # Calculate gap percentage
                gap_pct = abs(breakout_level - current_price) / breakout_level * 100

                # Check GREEN 1 and 2
                green1_pass = False
                green2_pass = False

                if len(df) >= 3:
                    green1_pass = current_price > df.iloc[-3]['close']

                if len(df) >= settings.green2_bb_period + 2:
                    bb_width_current = df['close'].rolling(settings.green2_bb_period).std().iloc[-1]
                    bb_width_prev = df['close'].rolling(settings.green2_bb_period).std().iloc[-2]
                    green2_pass = bb_width_current > bb_width_prev if bb_width_prev else False

                pairs_data.append({
                    'symbol': symbol,
                    'price': current_price,
                    'breakout_level': breakout_level,
                    'gap_pct': gap_pct,
                    'green1_pass': green1_pass,
                    'green2_pass': green2_pass
                })

            except Exception as e:
                logger.debug(f"Could not collect data for {symbol}: {e}")

        return pairs_data

    async def _check_early_warnings(self, pairs_data: list):
        """Check and send early warnings for pairs near breakout"""
        for p in pairs_data:
            should_warn, gap_pct = self.telegram.check_early_warning(
                p['symbol'],
                p['price'],
                p['breakout_level']
            )

            if should_warn:
                await self.telegram.send_early_warning(
                    symbol=p['symbol'],
                    price=p['price'],
                    breakout_level=p['breakout_level'],
                    gap_pct=gap_pct,
                    green1_pass=p['green1_pass'],
                    green2_pass=p['green2_pass']
                )

    async def _send_status_report(self, pairs_data: list):
        """Send hourly status report"""
        mode = "DRY_RUN" if settings.dry_run else "LIVE"
        _, pnl_pct = self.risk_breaker.get_daily_pnl()

        # Get exposure info from position sizer
        exposure_usd = self.position_sizer.current_positions * settings.position_size_usd
        max_exposure_usd = self.usdt_balance * 0.1  # 10% max exposure

        await self.telegram.send_hourly_status(
            mode=mode,
            pairs_data=pairs_data,
            open_positions=self.position_sizer.current_positions,
            exposure_usd=exposure_usd,
            max_exposure_usd=max_exposure_usd,
            pnl_pct=pnl_pct
        )

    def _get_current_prices(self) -> dict:
        """Get current prices for all trading pairs from candle store"""
        prices = {}
        for symbol in settings.trading_pairs:
            try:
                df = self.candle_store.get_candles(symbol, '5m', limit=1)
                if not df.empty:
                    prices[symbol] = df.iloc[-1]['close']
            except Exception as e:
                logger.debug(f"Could not get price for {symbol}: {e}")
        return prices

    async def _check_and_execute_exits(self, current_prices: dict):
        """Check open positions for exit conditions and execute SELL orders"""
        exits = self.position_manager.check_exits(current_prices)

        for exit_info in exits:
            symbol = exit_info['symbol']
            position = exit_info['position']
            current_price = exit_info['current_price']
            reason = exit_info['reason']
            pnl_pct = exit_info['pnl_pct']

            logger.info(f"\n{'*'*60}")
            logger.info(f"EXIT TRIGGERED: {symbol} - {reason}")
            logger.info(f"Entry: {position['entry_price']:.4f} -> Exit: {current_price:.4f}")
            logger.info(f"PnL: {pnl_pct:+.2f}%")
            logger.info(f"{'*'*60}")

            try:
                # Close position in position manager
                closed_pos = self.position_manager.close_position(symbol, current_price, reason)

                if settings.dry_run:
                    logger.info(f"[DRY_RUN] Would place SELL order for {position['quantity']:.6f} {symbol}")

                    # Update database
                    if position.get('trade_id'):
                        self.db.update_trade_status(position['trade_id'], 'CLOSED')

                    # Notify via Telegram
                    await self.telegram.notify_order_executed(
                        symbol=symbol,
                        side='sell',
                        price=current_price,
                        quantity=position['quantity'],
                        notional=current_price * position['quantity'],
                        dry_run=True,
                        pnl_pct=pnl_pct,
                        exit_reason=reason
                    )
                else:
                    # Place actual SELL order
                    order = self.router.place_market_order(symbol, 'sell', position['quantity'])

                    if order:
                        if position.get('trade_id'):
                            self.db.update_trade_status(position['trade_id'], 'CLOSED', order.get('id'))
                        self.position_sizer.decrement_positions()

                        await self.telegram.notify_order_executed(
                            symbol=symbol,
                            side='sell',
                            price=current_price,
                            quantity=position['quantity'],
                            notional=current_price * position['quantity'],
                            order_id=order.get('id'),
                            dry_run=False,
                            pnl_pct=pnl_pct,
                            exit_reason=reason
                        )
                    else:
                        logger.error(f"Failed to execute SELL order for {symbol}")

            except Exception as e:
                logger.error(f"Error executing exit for {symbol}: {e}", exc_info=True)

    async def execute_signal(self, signal: dict):
        """Execute a trading signal"""
        symbol = signal['symbol']
        action = signal['action']
        price = signal['price']

        logger.info(f"\n{'*'*60}")
        logger.info(f"EXECUTING SIGNAL: {action} {symbol} @ {price:.4f}")
        logger.info(f"{'*'*60}")

        try:
            # Calculate position size
            pos_size = self.position_sizer.calculate_size(symbol, price)

            if not pos_size['can_trade']:
                logger.warning(f"Cannot trade: {pos_size['reason']}")
                return

            quantity = pos_size['quantity']
            notional_usd = pos_size['notional_usd']

            logger.info(f"Position size: {quantity:.6f} {symbol.split('/')[0]} (${notional_usd:.2f})")

            # Log trade to database (NEW status)
            trade_id = self.db.log_trade(
                symbol=symbol,
                side='buy' if action == 'BUY' else 'sell',
                price=price,
                quantity=quantity,
                notional=notional_usd,
                signal_reason=signal.get('reason', ''),
                status='NEW'
            )

            logger.info(f"Trade logged to database: ID={trade_id}")

            if settings.dry_run:
                logger.info(f"[DRY_RUN] Would place {action} order for {quantity:.6f} {symbol}")
                self.db.update_trade_status(trade_id, 'DRY_RUN')

                # Record open position for exit monitoring
                if action == 'BUY':
                    self.position_manager.open_position(
                        symbol=symbol,
                        entry_price=price,
                        quantity=quantity,
                        trade_id=trade_id,
                        side='buy'
                    )
                    self.position_sizer.increment_positions()

                # Notify dry run order via Telegram
                await self.telegram.notify_order_executed(
                    symbol=symbol,
                    side=action.lower(),
                    price=price,
                    quantity=quantity,
                    notional=notional_usd,
                    dry_run=True
                )
                return

            # Place market order (for now - can switch to maker orders later)
            side = 'buy' if action == 'BUY' else 'sell'
            order = self.router.place_market_order(symbol, side, quantity)

            if order:
                order_id = order.get('id')
                self.db.update_trade_status(trade_id, 'FILLED', order_id)
                self.position_sizer.increment_positions()

                # Record open position for exit monitoring
                if action == 'BUY':
                    self.position_manager.open_position(
                        symbol=symbol,
                        entry_price=price,
                        quantity=quantity,
                        trade_id=trade_id,
                        side='buy'
                    )

                logger.info(f"Order executed successfully: {order_id}")

                # Notify successful order via Telegram
                await self.telegram.notify_order_executed(
                    symbol=symbol,
                    side=side,
                    price=price,
                    quantity=quantity,
                    notional=notional_usd,
                    order_id=order_id,
                    dry_run=False
                )
            else:
                self.db.update_trade_status(trade_id, 'FAILED')
                logger.error(f"Order execution failed")

        except Exception as e:
            logger.error(f"Error executing signal: {e}", exc_info=True)

    async def run(self):
        """Main run method"""
        self.running = True

        # Initialize
        await self.initialize()

        # Start data poller in background
        self.poller_task = asyncio.create_task(self.poller.run())

        # Start trading loop
        await self.trading_loop()

    async def shutdown(self):
        """Graceful shutdown"""
        logger.info("\nShutting down ScalperBot...")
        self.running = False

        # Send shutdown notification
        await self.telegram.notify_bot_stopped("Manual shutdown")

        if self.poller_task:
            self.poller.stop()

        self.db.close()
        logger.info("Shutdown complete")


async def main():
    """Main entry point"""
    bot = ScalperBot()

    # Handle shutdown signals
    def signal_handler(sig, frame):
        logger.info(f"\nReceived signal {sig}")
        asyncio.create_task(bot.shutdown())
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    try:
        await bot.run()
    except KeyboardInterrupt:
        logger.info("\nKeyboard interrupt received")
        await bot.shutdown()
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        await bot.shutdown()
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
