"""
ScalperBot - Main Entry Point
Cryptocurrency momentum breakout trading bot
"""
import asyncio
import logging
import signal
import sys
from datetime import datetime

from config import settings
from db import TradeDB
from exchanges.adapter import MEXCAdapter
from datafeed.candle_store import CandleStore
from datafeed.orderbook import OrderBook
from datafeed.rest_poller import RESTPoller
from datafeed.websocket_feed import HybridDataFeed, WEBSOCKETS_AVAILABLE
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
        logger.info("🚀 ScalperBot Initializing...")
        logger.info(f"Mode: {'🔶 DRY_RUN' if settings.dry_run else '🟢 LIVE'}")
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

        # Choose data feed mode: WebSocket (real-time) or REST (polling)
        self.use_websocket = settings.use_websocket and WEBSOCKETS_AVAILABLE
        if self.use_websocket:
            logger.info("📡 Using WebSocket + REST hybrid data feed (real-time)")
            self.data_feed = HybridDataFeed(
                self.exchange,
                self.candle_store,
                self.orderbook,
                settings.trading_pairs,
                settings.data_poll_interval
            )
            self.poller = None  # Not used in WebSocket mode
        else:
            logger.info("📡 Using REST-only data feed (polling)")
            self.poller = RESTPoller(
                self.exchange,
                self.candle_store,
                self.orderbook,
                settings.trading_pairs,
                settings.data_poll_interval
            )
            self.data_feed = None

        self.strategy = MomentumBreakoutStrategy(self.candle_store)
        self.router = OrderRouter(self.exchange, self.orderbook)
        self.position_sizer = PositionSizer(db=self.db)
        self.risk_breaker = RiskBreaker(self.db)

        # Initialize position manager for TP/SL
        self.position_manager = PositionManager(
            db=self.db,
            orderbook=self.orderbook,
            take_profit_pct=getattr(settings, 'take_profit_pct', 1.5),
            stop_loss_pct=getattr(settings, 'stop_loss_pct', 1.0),
            max_hold_hours=getattr(settings, 'max_hold_hours', 24)
        )

        # Initialize Telegram notifier
        self.telegram = TelegramNotifier(
            settings.telegram_bot_token,
            settings.telegram_chat_id
        )
        if self.telegram.enabled:
            logger.info("Telegram notifications: ENABLED")
        else:
            logger.warning("Telegram notifications: DISABLED (check config)")

        # State
        self.running = False
        self.poller_task = None

    async def initialize(self):
        """Initialize bot (fetch balance, set risk params, etc.)"""
        logger.info("🔧 Initializing bot components...")

        # Get starting balance for risk breaker and exposure calculations
        usdt_balance = 0.0
        try:
            balance = self.exchange.fetch_balance()
            usdt_balance = balance.get('USDT', {}).get('free', 0)
            self.risk_breaker.set_starting_balance(usdt_balance)
            self.position_sizer.set_account_balance(usdt_balance)  # For exposure limits
            logger.info(f"💰 USDT Balance: ${usdt_balance:.2f}")
        except Exception as e:
            logger.warning(f"⚠️ Could not fetch balance: {e}")
            self.risk_breaker.set_starting_balance(1000.0)  # Default
            self.position_sizer.set_account_balance(1000.0)  # Default
            usdt_balance = 1000.0

        logger.info("✅ Initialization complete")

        # Send Telegram startup notification
        mode = "DRY_RUN" if settings.dry_run else "LIVE"
        await self.telegram.notify_bot_started(mode, settings.trading_pairs, usdt_balance)

    async def trading_loop(self):
        """Main trading loop - runs strategy and executes trades"""
        logger.info(f"🔄 Trading loop started (interval: {settings.strategy_interval}s)")

        cycle = 0
        while self.running:
            cycle += 1
            logger.info(f"\n{'='*80}")
            logger.info(f"🔄 Strategy Cycle #{cycle} - {datetime.utcnow().isoformat()}")
            logger.info(f"{'='*80}")

            try:
                # Check risk breaker
                if not self.risk_breaker.can_trade():
                    logger.error("⛔ Risk breaker active - skipping trading")
                    await self.telegram.notify_risk_breaker(
                        self.risk_breaker.get_daily_pnl(),
                        settings.daily_loss_limit_pct
                    )
                    await asyncio.sleep(settings.strategy_interval)
                    continue

                # Display data summary
                logger.info(self.candle_store.summary())
                logger.info(self.orderbook.summary())

                # Check open positions for exits (TP/SL)
                exit_signals = self.position_manager.generate_exit_signals()
                for exit_signal in exit_signals:
                    await self.execute_exit_signal(exit_signal)

                # Log position summary
                logger.info(self.position_manager.get_position_summary())

                # Log exposure status
                total_exposure = self.position_sizer.get_total_exposure()
                max_exposure = self.position_sizer.account_balance_usd * (self.position_sizer.max_exposure_pct / 100)
                if self.position_sizer.account_balance_usd > 0:
                    exposure_pct = (total_exposure / self.position_sizer.account_balance_usd) * 100
                    logger.info(f"💰 Exposure: ${total_exposure:.2f}/{max_exposure:.2f} ({exposure_pct:.1f}%/{self.position_sizer.max_exposure_pct}%)")

                # Run strategy for all symbols (only if we can open more positions)
                if self.position_sizer.can_open_position():
                    signals = self.strategy.run_for_all_symbols(settings.trading_pairs)

                    # Execute signals
                    for signal in signals:
                        await self.execute_signal(signal)
                else:
                    logger.info(f"⚠️ Max positions reached ({settings.max_positions}), waiting for exits...")

            except Exception as e:
                logger.error(f"❌ Error in trading loop: {e}", exc_info=True)

            # Sleep until next cycle
            await asyncio.sleep(settings.strategy_interval)

    async def execute_signal(self, signal: dict):
        """Execute a trading signal"""
        symbol = signal['symbol']
        action = signal['action']
        price = signal['price']

        # Check per-symbol position limit and cooldown BEFORE executing
        can_open, reason = self.position_sizer.can_open_position_for_symbol(symbol)
        if not can_open:
            logger.info(f"⏭️ Skipping {symbol} signal: {reason}")
            return

        logger.info(f"\n{'*'*60}")
        logger.info(f"📢 EXECUTING SIGNAL: {action} {symbol} @ {price:.4f}")
        logger.info(f"{'*'*60}")

        # Notify signal via Telegram
        await self.telegram.notify_signal(signal)

        try:
            # Calculate position size
            pos_size = self.position_sizer.calculate_size(symbol, price)

            if not pos_size['can_trade']:
                logger.warning(f"⚠️ Cannot trade: {pos_size['reason']}")
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
                logger.info(f"🔶 [DRY_RUN] Would place {action} order for {quantity:.6f} {symbol}")
                self.db.update_trade_status(trade_id, 'DRY_RUN')
                # Notify dry run order via Telegram
                await self.telegram.notify_order_executed(
                    symbol=symbol,
                    side='buy' if action == 'BUY' else 'sell',
                    quantity=quantity,
                    price=price,
                    notional=notional_usd,
                    order_id="DRY_RUN",
                    is_dry_run=True
                )
                self.position_sizer.increment_positions()
                self.position_sizer.record_trade(symbol)  # Start cooldown
                return

            # Place market order (for now - can switch to maker orders later)
            side = 'buy' if action == 'BUY' else 'sell'
            order = self.router.place_market_order(symbol, side, quantity)

            if order:
                order_id = order.get('id')
                self.db.update_trade_status(trade_id, 'FILLED', order_id)
                self.position_sizer.increment_positions()
                self.position_sizer.record_trade(symbol)  # Start cooldown
                logger.info(f"✅ Order executed successfully: {order_id}")
                # Notify successful order via Telegram
                await self.telegram.notify_order_executed(
                    symbol=symbol,
                    side=side,
                    quantity=quantity,
                    price=price,
                    notional=notional_usd,
                    order_id=str(order_id),
                    is_dry_run=False
                )
            else:
                self.db.update_trade_status(trade_id, 'FAILED')
                logger.error(f"❌ Order execution failed")
                # Notify failed order via Telegram
                await self.telegram.notify_order_failed(symbol, side, "Order returned None")

        except Exception as e:
            logger.error(f"❌ Error executing signal: {e}", exc_info=True)
            await self.telegram.notify_error(str(e), f"Executing signal for {symbol}")

    async def execute_exit_signal(self, exit_signal: dict):
        """Execute an exit signal (SELL to close position, full or partial)"""
        symbol = exit_signal['symbol']
        quantity = exit_signal['quantity']
        original_quantity = exit_signal.get('original_quantity', quantity)
        exit_price = exit_signal['price']
        entry_price = exit_signal['entry_price']
        pnl_pct = exit_signal['pnl_pct']
        pnl_usd = exit_signal['pnl_usd']
        reason = exit_signal['reason']
        trade_id = exit_signal['trade_id']
        is_partial = exit_signal.get('is_partial', False)

        partial_tag = " (PARTIAL)" if is_partial else ""
        logger.info(f"\n{'*'*60}")
        logger.info(f"🔴 EXECUTING EXIT{partial_tag}: SELL {quantity:.6f} {symbol} @ {exit_price:.4f}")
        logger.info(f"   Reason: {reason} | PnL: {pnl_pct:+.2f}% (${pnl_usd:+.2f})")
        logger.info(f"{'*'*60}")

        try:
            if settings.dry_run:
                logger.info(f"🔶 [DRY_RUN] Would place SELL order for {quantity:.6f} {symbol}")

                if is_partial:
                    # Partial exit - update position quantity
                    remaining_quantity = original_quantity - quantity
                    remaining_notional = remaining_quantity * entry_price
                    self.position_manager.partial_close_position(
                        trade_id, quantity, remaining_quantity, remaining_notional, pnl_usd
                    )
                    # Don't decrement position count for partial exits
                else:
                    # Full exit - close position
                    was_closed = self.position_manager.close_position(trade_id, exit_price, pnl_usd)
                    if not was_closed:
                        logger.info(f"Position {trade_id} already closed - skipping notification")
                        return
                    self.position_sizer.decrement_positions()

                # Notify via Telegram
                await self.telegram.notify_position_closed(
                    symbol=symbol,
                    entry_price=entry_price,
                    exit_price=exit_price,
                    quantity=quantity,
                    pnl_pct=pnl_pct,
                    pnl_usd=pnl_usd,
                    reason=reason + partial_tag,
                    is_dry_run=True
                )
                return

            # Place market sell order
            order = self.router.place_market_order(symbol, 'sell', quantity)

            if order:
                order_id = order.get('id')
                logger.info(f"✅ Exit order executed: {order_id}")

                if is_partial:
                    # Partial exit - update position quantity
                    remaining_quantity = original_quantity - quantity
                    remaining_notional = remaining_quantity * entry_price
                    self.position_manager.partial_close_position(
                        trade_id, quantity, remaining_quantity, remaining_notional, pnl_usd
                    )
                    # Don't decrement position count for partial exits
                else:
                    # Full exit - close position
                    was_closed = self.position_manager.close_position(trade_id, exit_price, pnl_usd)
                    if not was_closed:
                        logger.warning(f"Position {trade_id} already closed - order executed but DB unchanged")
                        return
                    self.position_sizer.decrement_positions()

                # Notify via Telegram
                await self.telegram.notify_position_closed(
                    symbol=symbol,
                    entry_price=entry_price,
                    exit_price=exit_price,
                    quantity=quantity,
                    pnl_pct=pnl_pct,
                    pnl_usd=pnl_usd,
                    reason=reason + partial_tag,
                    is_dry_run=False
                )
            else:
                logger.error(f"❌ Exit order failed for {symbol}")
                await self.telegram.notify_order_failed(symbol, 'sell', f"Exit failed: {reason}")

        except Exception as e:
            logger.error(f"❌ Error executing exit signal: {e}", exc_info=True)
            await self.telegram.notify_error(str(e), f"Executing exit for {symbol}")

    async def run(self):
        """Main run method"""
        self.running = True

        # Initialize
        await self.initialize()

        # Start data feed in background (WebSocket or REST)
        if self.use_websocket and self.data_feed:
            self.poller_task = asyncio.create_task(self.data_feed.run())
            logger.info("🚀 WebSocket data feed started")
        elif self.poller:
            self.poller_task = asyncio.create_task(self.poller.run())
            logger.info("🚀 REST data poller started")

        # Start trading loop
        await self.trading_loop()

    async def shutdown(self, reason: str = "Manual shutdown"):
        """Graceful shutdown"""
        logger.info("\n🛑 Shutting down ScalperBot...")
        self.running = False

        # Send Telegram shutdown notification
        await self.telegram.notify_bot_stopped(reason)

        # Stop data feed
        if self.use_websocket and self.data_feed:
            self.data_feed.stop()
        elif self.poller:
            self.poller.stop()

        self.db.close()
        logger.info("✅ Shutdown complete")


async def main():
    """Main entry point"""
    bot = ScalperBot()

    # Handle shutdown signals
    def signal_handler(sig, frame):
        logger.info(f"\n⚠️ Received signal {sig}")
        # Schedule async shutdown
        asyncio.create_task(bot.shutdown(f"Signal {sig}"))
        bot.running = False

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    try:
        await bot.run()
    except KeyboardInterrupt:
        logger.info("\n⚠️ Keyboard interrupt received")
        await bot.shutdown("Keyboard interrupt")
    except Exception as e:
        logger.error(f"❌ Fatal error: {e}", exc_info=True)
        await bot.shutdown(f"Fatal error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
