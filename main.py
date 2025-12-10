"""
ScalperBot - Main Entry Point
Cryptocurrency momentum breakout trading bot with:
- Smart Breakout Strategy (HTF confirmation)
- ATR-based dynamic TP/SL/Trailing stops
- Risk-based position sizing
- Proper exit logic and PnL tracking
"""
import asyncio
import logging
import signal
import sys
from datetime import datetime
from typing import Optional, Dict

from config import settings
from db import TradeDB
from exchanges.adapter import MEXCAdapter
from datafeed.candle_store import CandleStore
from datafeed.orderbook import OrderBook
from datafeed.rest_poller import RESTPoller
from strategies.smart_breakout import SmartBreakoutStrategy, calculate_position_size
from exec.router import OrderRouter
from risk.breaker import RiskBreaker

# Configure logging - prevent duplicate handlers
logger = logging.getLogger(__name__)
if not logger.handlers:
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper()),
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[
            logging.FileHandler(settings.log_file),
            logging.StreamHandler()
        ]
    )


class ScalperBot:
    """
    Main trading bot class with proper entry/exit logic
    """

    def __init__(self):
        logger.info("="*80)
        logger.info("ScalperBot v2.0 - Smart Breakout Strategy")
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

        # New smart strategy with HTF confirmation
        self.strategy = SmartBreakoutStrategy(self.candle_store, self.exchange)
        self.router = OrderRouter(self.exchange, self.orderbook)
        self.risk_breaker = RiskBreaker(self.db)

        # State
        self.running = False
        self.poller_task = None
        self.equity = 1000.0  # Will be updated from exchange

        # Position tracking
        self.open_positions: Dict[str, Dict] = {}

        # Risk parameters
        self.risk_per_trade_pct = 1.5  # Risk 1.5% per trade (3x more aggressive)
        self.max_positions = settings.max_positions
        self.trade_cooldown = {}  # Symbol -> last trade time

    async def initialize(self):
        """Initialize bot"""
        logger.info("Initializing bot components...")

        # Get starting balance
        try:
            balance = self.exchange.fetch_balance()
            self.equity = balance.get('USDT', {}).get('free', 0)
            self.risk_breaker.set_starting_balance(self.equity)
            logger.info(f"USDT Balance: ${self.equity:.2f}")
        except Exception as e:
            logger.warning(f"Could not fetch balance: {e}")
            self.equity = 1000.0
            self.risk_breaker.set_starting_balance(self.equity)

        # Load open positions from database
        await self.load_open_positions()

        logger.info("Initialization complete")

    async def load_open_positions(self):
        """Load any open positions from database"""
        try:
            open_trades = self.db.get_open_positions()
            for trade in open_trades:
                symbol = trade['symbol']
                if symbol not in self.open_positions:
                    # Reconstruct position
                    entry_price = trade['price']
                    atr = entry_price * 0.01  # Fallback ATR estimate

                    self.strategy.register_position(
                        symbol=symbol,
                        entry_price=entry_price,
                        atr=atr,
                        trade_id=trade['id']
                    )
                    self.open_positions[symbol] = trade
                    logger.info(f"Loaded open position: {symbol} @ {entry_price}")
        except Exception as e:
            logger.error(f"Error loading positions: {e}")

    def get_current_price(self, symbol: str) -> Optional[float]:
        """Get current price for a symbol"""
        try:
            # Try orderbook first (faster)
            mid_price = self.orderbook.get_mid_price(symbol)
            if mid_price and mid_price > 0:
                return mid_price

            # Fallback to ticker
            ticker = self.exchange.fetch_ticker(symbol)
            return ticker.get('last') or ticker.get('close')
        except Exception as e:
            logger.error(f"Error getting price for {symbol}: {e}")
            return None

    async def check_exits(self):
        """Check all open positions for exit conditions"""
        exits = self.strategy.check_all_positions(self.get_current_price)

        for exit_signal in exits:
            await self.execute_exit(exit_signal)

    async def execute_exit(self, signal: Dict):
        """Execute an exit (sell) order"""
        symbol = signal['symbol']
        exit_price = signal['price']
        reason = signal['reason']
        trade_id = signal.get('trade_id')
        entry_price = signal.get('entry_price', exit_price)

        logger.info(f"\n{'*'*60}")
        logger.info(f"EXECUTING EXIT: {symbol} @ {exit_price:.4f}")
        logger.info(f"Reason: {reason}")
        logger.info(f"{'*'*60}")

        try:
            # Get position info
            pos_info = self.strategy.get_position_info(symbol)
            if not pos_info and symbol in self.open_positions:
                pos_info = self.open_positions[symbol]

            # Calculate quantity to sell
            quantity = 0
            if pos_info:
                quantity = pos_info.get('quantity', 0)
                if quantity == 0 and 'notional' in pos_info:
                    quantity = pos_info['notional'] / entry_price

            if quantity <= 0:
                logger.warning(f"No quantity to sell for {symbol} - cleaning up ghost position")
                # Still clean up position tracking to prevent infinite loops
                self.strategy.close_position(symbol)
                if symbol in self.open_positions:
                    del self.open_positions[symbol]
                if trade_id:
                    self.db.update_trade_status(trade_id, 'FAILED')
                return

            # Calculate PnL
            pnl_pct = (exit_price - entry_price) / entry_price * 100
            pnl_usd = (exit_price - entry_price) * quantity

            logger.info(f"Selling {quantity:.6f} {symbol.split('/')[0]}")
            logger.info(f"Entry: {entry_price:.4f}, Exit: {exit_price:.4f}")
            logger.info(f"PnL: {pnl_pct:+.2f}% (${pnl_usd:+.2f})")

            if settings.dry_run:
                logger.info(f"[DRY_RUN] Would sell {quantity:.6f} {symbol}")
                filled_price = exit_price
                order_success = True
            else:
                # Place sell order
                order = self.router.place_market_order(symbol, 'sell', quantity)

                if order:
                    # Safe order parsing with fallbacks
                    filled_price = self._get_filled_price(order, exit_price)
                    order_id = order.get('id', 'unknown')
                    logger.info(f"Order filled: {order_id} @ {filled_price:.4f}")
                    order_success = True
                else:
                    logger.error("Sell order failed - cleaning up position anyway")
                    filled_price = exit_price
                    order_success = False

            # Update database with PnL (even on failure, to close the position)
            actual_pnl = (filled_price - entry_price) * quantity

            if trade_id:
                self.db.update_trade_pnl(trade_id, actual_pnl if order_success else 0)
                self.db.update_trade_status(trade_id, 'CLOSED' if order_success else 'FAILED')
                if order_success:
                    self.db.update_daily_pnl(actual_pnl)
                logger.info(f"Trade {trade_id} closed, PnL: ${actual_pnl:+.2f}" if order_success else f"Trade {trade_id} marked FAILED")

            # Log exit trade (only if order succeeded)
            if order_success:
                self.db.log_trade(
                    symbol=symbol,
                    side='sell',
                    price=filled_price,
                    quantity=quantity,
                    notional=filled_price * quantity,
                    signal_reason=f"EXIT: {reason}",
                    status='CLOSED' if not settings.dry_run else 'DRY_RUN'
                )

            # ALWAYS clean up position tracking (even on order failure)
            # This prevents infinite exit signal loops
            self.strategy.close_position(symbol)
            if symbol in self.open_positions:
                del self.open_positions[symbol]
            logger.info(f"Position tracking cleaned up for {symbol}")

            # Update equity only if order succeeded
            if order_success:
                self.equity += actual_pnl

        except Exception as e:
            logger.error(f"Error executing exit: {e}", exc_info=True)
            # Still try to clean up position to prevent infinite loops
            try:
                self.strategy.close_position(symbol)
                if symbol in self.open_positions:
                    del self.open_positions[symbol]
                logger.info(f"Position cleaned up after error for {symbol}")
            except:
                pass

    async def execute_entry(self, signal: Dict):
        """Execute an entry (buy) order"""
        symbol = signal['symbol']
        price = signal['price']
        reason = signal['reason']

        logger.info(f"\n{'*'*60}")
        logger.info(f"EXECUTING ENTRY: BUY {symbol} @ {price:.4f}")
        logger.info(f"Reason: {reason}")
        logger.info(f"{'*'*60}")

        try:
            # Check position limits
            if len(self.open_positions) >= self.max_positions:
                logger.warning(f"Max positions ({self.max_positions}) reached")
                return

            if symbol in self.open_positions:
                logger.warning(f"Already have position in {symbol}")
                return

            # Check cooldown
            if symbol in self.trade_cooldown:
                elapsed = (datetime.utcnow() - self.trade_cooldown[symbol]).total_seconds()
                if elapsed < 300:  # 5 min cooldown
                    logger.debug(f"{symbol}: Trade cooldown ({300-elapsed:.0f}s remaining)")
                    return

            # Calculate position size based on risk
            stop_loss = signal.get('stop_loss', price * 0.99)
            atr = signal.get('atr', price * 0.01)

            sizing = calculate_position_size(
                equity=self.equity,
                entry_price=price,
                stop_loss=stop_loss,
                risk_pct=self.risk_per_trade_pct,
                max_position_pct=10.0
            )

            if not sizing['can_trade']:
                logger.warning(f"Cannot trade: {sizing['reason']}")
                return

            quantity = sizing['quantity']
            notional_usd = sizing['notional_usd']

            logger.info(f"Position size: {quantity:.6f} {symbol.split('/')[0]} (${notional_usd:.2f})")
            logger.info(f"Risk: ${sizing['risk_amount']:.2f} ({sizing['risk_pct']}%)")
            logger.info(f"Stop loss: {stop_loss:.4f} ({sizing['stop_loss_pct']:.2f}%)")

            # Log trade to database
            trade_id = self.db.log_trade(
                symbol=symbol,
                side='buy',
                price=price,
                quantity=quantity,
                notional=notional_usd,
                signal_reason=reason,
                status='NEW'
            )

            if settings.dry_run:
                logger.info(f"[DRY_RUN] Would buy {quantity:.6f} {symbol}")
                self.db.update_trade_status(trade_id, 'DRY_RUN')
                filled_price = price
            else:
                # Place market order
                order = self.router.place_market_order(symbol, 'buy', quantity)

                if order:
                    filled_price = self._get_filled_price(order, price)
                    order_id = order.get('id', 'unknown')
                    self.db.update_trade_status(trade_id, 'FILLED', order_id)
                    logger.info(f"Order filled: {order_id} @ {filled_price:.4f}")
                else:
                    self.db.update_trade_status(trade_id, 'FAILED')
                    logger.error("Buy order failed")
                    return

            # Register position for exit management
            self.strategy.register_position(
                symbol=symbol,
                entry_price=filled_price,
                atr=atr,
                trade_id=trade_id
            )

            # Track open position
            self.open_positions[symbol] = {
                'trade_id': trade_id,
                'entry_price': filled_price,
                'quantity': quantity,
                'notional': notional_usd,
                'stop_loss': stop_loss,
                'take_profit': signal.get('take_profit', price * 1.02)
            }

            # Set cooldown
            self.trade_cooldown[symbol] = datetime.utcnow()

            logger.info(f"Position opened for {symbol}")

        except Exception as e:
            logger.error(f"Error executing entry: {e}", exc_info=True)

    def _get_filled_price(self, order: Dict, fallback_price: float) -> float:
        """Safely extract filled price from order with fallbacks"""
        # Try multiple fields in order of preference
        for field in ['average', 'price', 'avgPrice', 'filled_price']:
            price = order.get(field)
            if price is not None and price > 0:
                return float(price)

        # Try nested structures
        if 'info' in order:
            info = order['info']
            for field in ['avgPrice', 'price', 'average']:
                price = info.get(field)
                if price is not None and price > 0:
                    return float(price)

        logger.warning(f"Could not extract filled price from order, using fallback: {fallback_price}")
        return fallback_price

    async def update_htf_data(self):
        """Fetch and update HTF (1H) data for all symbols"""
        for symbol in settings.trading_pairs:
            try:
                # Fetch 1H candles
                df_1h = self.candle_store.get_candles(symbol, '1h', limit=50)
                if df_1h is not None and not df_1h.empty:
                    self.strategy.update_htf_data(symbol, df_1h)
            except Exception as e:
                logger.error(f"Error updating HTF data for {symbol}: {e}")

    async def trading_loop(self):
        """Main trading loop"""
        logger.info(f"Trading loop started (interval: {settings.strategy_interval}s)")

        cycle = 0
        while self.running:
            cycle += 1
            logger.info(f"\n{'='*80}")
            logger.info(f"Cycle #{cycle} - {datetime.utcnow().isoformat()}")
            logger.info(f"Open positions: {len(self.open_positions)}/{self.max_positions}")
            logger.info(f"{'='*80}")

            try:
                # Check risk breaker
                if not self.risk_breaker.can_trade():
                    logger.error("Risk breaker active - skipping trading")
                    await asyncio.sleep(settings.strategy_interval)
                    continue

                # Update HTF data
                await self.update_htf_data()

                # Check exits FIRST (more important than entries)
                await self.check_exits()

                # Display data summary
                logger.info(self.candle_store.summary())

                # Check for new entry signals
                signals = self.strategy.run_for_all_symbols(settings.trading_pairs)

                for signal in signals:
                    await self.execute_entry(signal)

            except Exception as e:
                logger.error(f"Error in trading loop: {e}", exc_info=True)

            await asyncio.sleep(settings.strategy_interval)

    async def run(self):
        """Main run method"""
        self.running = True

        await self.initialize()

        # Start data poller
        self.poller_task = asyncio.create_task(self.poller.run())

        # Wait for initial data
        logger.info("Waiting 30s for initial data...")
        await asyncio.sleep(30)

        # Start trading loop
        await self.trading_loop()

    def shutdown(self):
        """Graceful shutdown"""
        logger.info("\nShutting down ScalperBot...")
        self.running = False

        if self.poller_task:
            self.poller.stop()

        self.db.close()
        logger.info("Shutdown complete")


async def main():
    """Main entry point"""
    bot = ScalperBot()

    def signal_handler(sig, frame):
        logger.info(f"\nReceived signal {sig}")
        bot.shutdown()
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    try:
        await bot.run()
    except KeyboardInterrupt:
        logger.info("\nKeyboard interrupt")
        bot.shutdown()
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        bot.shutdown()
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
