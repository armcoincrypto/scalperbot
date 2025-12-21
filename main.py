"""
ScalperBot - Main Entry Point
Cryptocurrency momentum breakout trading bot with proper position management
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
from strategies.momentum_breakout import MomentumBreakoutStrategy
from strategies.smart_breakout import SmartBreakoutStrategy
from exec.router import OrderRouter
from ops.pos_size import PositionSizer
from ops.position_manager import PositionManager
from risk.breaker import RiskBreaker

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
    Orchestrates all components and runs trading loop with position management
    """

    def __init__(self):
        logger.info("="*80)
        logger.info("ScalperBot v2.1 Initializing...")
        logger.info(f"Mode: {'DRY_RUN' if settings.dry_run else 'LIVE'}")
        logger.info(f"Strategy: {settings.strategy_type.upper()}")
        logger.info(f"Trading pairs: {settings.trading_pairs}")
        logger.info(f"TP: {settings.take_profit_pct}% | SL: {settings.stop_loss_pct}%")
        logger.info(f"Trailing: {'Enabled' if settings.trailing_enabled else 'Disabled'}")
        logger.info(f"Max Hold: {settings.max_hold_hours}h")
        if settings.strategy_type == "momentum_breakout":
            logger.info(f"Volume Filter: {'Enabled' if settings.green3_enabled else 'Disabled'}")
        else:
            logger.info(f"HTF RSI Limit: {settings.smart_htf_rsi_limit}")
            logger.info(f"Dynamic TP/SL: {'ATR-based' if settings.smart_use_dynamic_targets else 'Fixed'}")
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

        # Select strategy based on config
        if settings.strategy_type == "smart_breakout":
            self.strategy = SmartBreakoutStrategy(self.candle_store)
            logger.info("Using SMART BREAKOUT strategy (improved with HTF confirmation)")
        else:
            self.strategy = MomentumBreakoutStrategy(self.candle_store)
            logger.info("Using MOMENTUM BREAKOUT strategy (original 4-filter GREEN)")
        self.router = OrderRouter(self.exchange, self.orderbook)

        # Position management with database tracking
        # Pass exchange for balance queries (volatility-based sizing)
        self.position_sizer = PositionSizer(db=self.db, exchange=self.exchange)
        self.position_manager = PositionManager(
            self.db,
            self.exchange,
            self.orderbook,
            self.router
        )
        self.risk_breaker = RiskBreaker(self.db)

        # Pullback tracking for better entries
        self.pending_signals = {}  # symbol -> {signal, breakout_price, candles_waited}

        # State
        self.running = False
        self.poller_task = None
        self.position_monitor_task = None

    async def initialize(self):
        """Initialize bot (fetch balance, set risk params, etc.)"""
        logger.info("Initializing bot components...")

        # Get starting balance for risk breaker
        try:
            balance = self.exchange.fetch_balance()
            usdt_balance = balance.get('USDT', {}).get('free', 0)
            self.risk_breaker.set_starting_balance(usdt_balance)
            logger.info(f"USDT Balance: ${usdt_balance:.2f}")
        except Exception as e:
            logger.warning(f"Could not fetch balance: {e}")
            self.risk_breaker.set_starting_balance(1000.0)  # Default

        # Show daily stats
        stats = self.db.get_daily_stats()
        logger.info(f"Today's stats: PnL=${stats['pnl']:.2f} | Trades={stats['num_trades']} | "
                   f"Wins={stats['win_count']} | Losses={stats['loss_count']} | "
                   f"Win Rate={stats['win_rate']:.1f}%")

        logger.info("Initialization complete")

    async def position_monitor_loop(self):
        """Background task to monitor positions and execute exits"""
        logger.info(f"Position monitor started (interval: {settings.position_check_interval}s)")

        while self.running:
            try:
                # Check all open positions for exit conditions
                closed = await self.position_manager.check_all_positions()

                if closed:
                    logger.info(f"Closed {len(closed)} positions this cycle")

                    # Record cooldown for closed positions
                    for pos in closed:
                        self.position_sizer.record_position_close(pos['symbol'])

                    # Show updated stats
                    stats = self.db.get_daily_stats()
                    logger.info(f"Daily stats: PnL=${stats['pnl']:.2f} | Win Rate={stats['win_rate']:.1f}%")

            except Exception as e:
                logger.error(f"Error in position monitor: {e}", exc_info=True)

            await asyncio.sleep(settings.position_check_interval)

    async def trading_loop(self):
        """Main trading loop - runs strategy and executes trades"""
        logger.info(f"Trading loop started (interval: {settings.strategy_interval}s)")

        cycle = 0
        while self.running:
            cycle += 1
            logger.info(f"\n{'='*80}")
            logger.info(f"Strategy Cycle #{cycle} - {datetime.utcnow().isoformat()}")
            logger.info(f"{'='*80}")

            try:
                # Check risk breaker
                if not self.risk_breaker.can_trade():
                    logger.error("Risk breaker active - skipping trading")
                    await asyncio.sleep(settings.strategy_interval)
                    continue

                # Check trading hours filter (data shows 05:00-09:00 UTC = 92% WR)
                if settings.trading_hours_enabled:
                    current_hour = datetime.utcnow().hour
                    if not (settings.trading_hours_start <= current_hour < settings.trading_hours_end):
                        logger.info(f"⏰ Outside trading hours ({settings.trading_hours_start}:00-{settings.trading_hours_end}:00 UTC). Current: {current_hour}:00 - monitoring only")
                        # Still display data and monitor positions, just don't take new trades
                        logger.info(self.candle_store.summary())
                        logger.info(self.position_manager.get_positions_summary())
                        await asyncio.sleep(settings.strategy_interval)
                        continue

                # Display data summary
                logger.info(self.candle_store.summary())
                logger.info(self.orderbook.summary())

                # Display position status
                logger.info(self.position_manager.get_positions_summary())

                # Check pending pullback entries
                await self.check_pending_entries()

                # Run strategy for all symbols
                signals = self.strategy.run_for_all_symbols(settings.trading_pairs)

                # Process signals
                for signal in signals:
                    await self.process_signal(signal)

            except Exception as e:
                logger.error(f"Error in trading loop: {e}", exc_info=True)

            # Sleep until next cycle
            await asyncio.sleep(settings.strategy_interval)

    async def process_signal(self, signal: dict):
        """Process a trading signal - either execute or queue for pullback"""
        symbol = signal['symbol']

        # Skip if we already have a pending entry for this symbol
        if symbol in self.pending_signals:
            logger.debug(f"{symbol}: Already have pending signal, skipping")
            return

        # Skip if we already have an open position
        if self.position_sizer.has_position(symbol):
            logger.debug(f"{symbol}: Already have open position, skipping")
            return

        if settings.pullback_entry_enabled:
            # Queue signal and wait for pullback
            self.pending_signals[symbol] = {
                'signal': signal,
                'breakout_price': signal['price'],
                'candles_waited': 0
            }
            logger.info(f"Queued {symbol} for pullback entry (breakout @ {signal['price']:.4f})")
        else:
            # Execute immediately
            await self.execute_signal(signal)

    async def check_pending_entries(self):
        """Check pending signals for pullback entry conditions"""
        if not self.pending_signals:
            return

        symbols_to_remove = []

        for symbol, pending in self.pending_signals.items():
            signal = pending['signal']
            breakout_price = pending['breakout_price']
            candles_waited = pending['candles_waited']

            # Get current price
            current_price = self.orderbook.get_mid_price(symbol)
            if not current_price:
                continue

            # Calculate pullback percentage (guard against division by zero)
            if breakout_price <= 0:
                logger.warning(f"{symbol}: Invalid breakout_price={breakout_price}, skipping")
                symbols_to_remove.append(symbol)
                continue
            pullback_pct = ((breakout_price - current_price) / breakout_price) * 100

            logger.debug(f"{symbol}: Waiting for pullback. Current pullback: {pullback_pct:.2f}%")

            # Check if pullback is sufficient
            if pullback_pct >= settings.pullback_entry_pct:
                logger.info(f"{symbol}: Pullback condition met ({pullback_pct:.2f}% >= {settings.pullback_entry_pct}%)")
                # Update signal with better entry price
                signal['price'] = current_price
                signal['reason'] += f" | Pullback entry ({pullback_pct:.2f}%)"
                await self.execute_signal(signal)
                symbols_to_remove.append(symbol)

            elif candles_waited >= settings.pullback_max_candles:
                # Max wait time exceeded, execute at current price
                logger.info(f"{symbol}: Max candles waited ({candles_waited}), entering at current price")
                signal['price'] = current_price
                signal['reason'] += f" | Timeout entry (waited {candles_waited} candles)"
                await self.execute_signal(signal)
                symbols_to_remove.append(symbol)

            else:
                # Still waiting
                pending['candles_waited'] += 1

        # Remove processed signals
        for symbol in symbols_to_remove:
            del self.pending_signals[symbol]

    async def execute_signal(self, signal: dict):
        """Execute a trading signal with proper position management"""
        symbol = signal['symbol']
        action = signal['action']
        price = signal['price']

        # CRITICAL: Check if we can trade this symbol (no position + not in cooldown)
        can_trade, reason = self.position_sizer.can_trade_symbol(symbol)
        if not can_trade:
            logger.debug(f"{symbol}: Cannot trade - {reason}")
            return

        try:
            # Determine stop loss for position sizing
            # Use dynamic ATR-based SL if available, otherwise use config default
            if 'targets' in signal and settings.smart_use_dynamic_targets:
                stop_loss_pct = signal['targets']['stop_loss_pct']
            else:
                stop_loss_pct = settings.stop_loss_pct

            # Calculate position size with volatility-based sizing
            pos_size = self.position_sizer.calculate_size(
                symbol,
                price,
                stop_loss_pct=stop_loss_pct
            )

            if not pos_size['can_trade']:
                logger.warning(f"Cannot trade: {pos_size['reason']}")
                return

            quantity = pos_size['quantity']
            notional_usd = pos_size['notional_usd']

            # Log AFTER all checks pass - this means we ARE executing
            logger.info(f"\n{'*'*60}")
            logger.info(f"EXECUTING SIGNAL: {action} {symbol} @ {price:.4f}")
            logger.info(f"{'*'*60}")
            logger.info(f"Position size: {quantity:.6f} {symbol.split('/')[0]} (${notional_usd:.2f}) [{pos_size['sizing_method']}]")

            # Calculate TP/SL prices
            side = 'buy' if action == 'BUY' else 'sell'

            # Use dynamic targets from smart breakout if available
            if 'targets' in signal and settings.smart_use_dynamic_targets:
                targets = signal['targets']
                tp_price = targets['take_profit_price']
                sl_price = targets['stop_loss_price']
                logger.info(f"Using DYNAMIC ATR-based targets (R:R={targets['risk_reward']:.1f})")
                logger.info(f"TP: {tp_price:.4f} (+{targets['take_profit_pct']:.2f}%) | SL: {sl_price:.4f} (-{targets['stop_loss_pct']:.2f}%)")
            else:
                tp_sl = self.position_manager.calculate_tp_sl_prices(price, side)
                tp_price = tp_sl['take_profit_price']
                sl_price = tp_sl['stop_loss_price']
                logger.info(f"TP: {tp_price:.4f} (+{settings.take_profit_pct}%) | SL: {sl_price:.4f} (-{settings.stop_loss_pct}%)")

            # Log trade to database (NEW status)
            trade_id = self.db.log_trade(
                symbol=symbol,
                side=side,
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

                # Still open position in DB for tracking (DRY_RUN mode)
                position_id = self.db.open_position(
                    trade_id=trade_id,
                    symbol=symbol,
                    side=side,
                    entry_price=price,
                    quantity=quantity,
                    notional=notional_usd,
                    take_profit_price=tp_price,
                    stop_loss_price=sl_price
                )
                logger.info(f"Position opened (DRY_RUN): ID={position_id}")
                return

            # Place market order
            order = self.router.place_market_order(symbol, side, quantity)

            if order:
                order_id = order.get('id')

                # CRITICAL: Verify order actually filled before marking as FILLED
                # Market orders should fill immediately, but we must verify
                # Note: MEXC may return status=None for market orders, so use 'or' to handle None
                order_status = (order.get('status') or '').lower()
                filled_qty = order.get('filled', 0) or 0

                # Check if this is a dry run order (always considered filled)
                is_dry_run = order.get('dry_run', False)

                if is_dry_run:
                    # Dry run orders are simulated - mark as filled
                    filled_price = order.get('price') or price
                    filled_qty = order.get('amount') or quantity
                    logger.info(f"[DRY_RUN] Order simulated as filled")
                    self.db.update_trade_status(trade_id, 'FILLED', order_id)
                elif order_status in ['closed', 'filled'] and filled_qty > 0:
                    # Order actually filled - proceed
                    filled_price = order.get('average') or order.get('price') or price
                    logger.info(f"Order verified FILLED: status={order_status}, filled_qty={filled_qty}")
                    self.db.update_trade_status(trade_id, 'FILLED', order_id)
                elif order_status == 'open':
                    # Order still open (shouldn't happen for market orders)
                    logger.warning(f"Market order still OPEN - waiting for fill: {order_id}")
                    self.db.update_trade_status(trade_id, 'PENDING', order_id)
                    # Don't open position yet - will need separate handling
                    return
                elif order_status in ['canceled', 'cancelled', 'rejected', 'expired']:
                    # Order failed
                    logger.error(f"Order {order_status.upper()}: {order_id}")
                    self.db.update_trade_status(trade_id, 'FAILED', order_id)
                    return
                else:
                    # Unknown status - fetch order to verify
                    logger.warning(f"Unknown order status '{order_status}', fetching order details...")
                    fetched_order = self.router.get_order_status(order_id, symbol)
                    if fetched_order:
                        order_status = (fetched_order.get('status') or '').lower()
                        filled_qty = fetched_order.get('filled', 0) or 0
                        if order_status in ['closed', 'filled'] and filled_qty > 0:
                            filled_price = fetched_order.get('average') or fetched_order.get('price') or price
                            logger.info(f"Order verified after fetch: status={order_status}, filled={filled_qty}")
                            self.db.update_trade_status(trade_id, 'FILLED', order_id)
                        elif order_status in ['canceled', 'cancelled', 'rejected', 'expired']:
                            # Order was explicitly rejected
                            logger.error(f"Order {order_status.upper()} after fetch: {order_id}")
                            self.db.update_trade_status(trade_id, 'FAILED', order_id)
                            return
                        else:
                            # MEXC market orders fill instantly - if we got an order ID, assume filled
                            # This handles the case where MEXC returns status=None
                            logger.warning(f"Order status still unknown after fetch (status='{order_status}', filled={filled_qty})")
                            logger.warning(f"MEXC market orders fill instantly - assuming filled at requested price")
                            filled_price = fetched_order.get('average') or fetched_order.get('price') or price
                            filled_qty = fetched_order.get('amount') or quantity
                            self.db.update_trade_status(trade_id, 'FILLED', order_id)
                    else:
                        # Could not fetch but order was accepted - assume filled for market orders
                        logger.warning(f"Could not fetch order {order_id}, assuming market order filled")
                        filled_price = price
                        filled_qty = quantity
                        self.db.update_trade_status(trade_id, 'FILLED', order_id)

                # Recalculate TP/SL with actual fill price
                if 'targets' in signal and settings.smart_use_dynamic_targets:
                    # For smart breakout, recalculate based on ATR ratio
                    targets = signal['targets']
                    # Safe access with fallback to static TP/SL calculation
                    target_tp = targets.get('take_profit_price')
                    target_sl = targets.get('stop_loss_price')
                    signal_price = signal.get('price', filled_price)

                    if target_tp and target_sl and signal_price > 0:
                        price_diff_tp = target_tp - signal_price
                        price_diff_sl = signal_price - target_sl
                        tp_price = filled_price + price_diff_tp
                        sl_price = filled_price - price_diff_sl
                    else:
                        # Fallback to static calculation
                        logger.warning(f"Invalid targets in signal, using static TP/SL")
                        tp_sl = self.position_manager.calculate_tp_sl_prices(filled_price, side)
                        tp_price = tp_sl['take_profit_price']
                        sl_price = tp_sl['stop_loss_price']
                else:
                    tp_sl = self.position_manager.calculate_tp_sl_prices(filled_price, side)
                    tp_price = tp_sl['take_profit_price']
                    sl_price = tp_sl['stop_loss_price']

                # Open position in database
                position_id = self.db.open_position(
                    trade_id=trade_id,
                    symbol=symbol,
                    side=side,
                    entry_price=filled_price,
                    quantity=filled_qty,
                    notional=filled_price * filled_qty,
                    take_profit_price=tp_price,
                    stop_loss_price=sl_price
                )

                logger.info(f"Order executed: {order_id}")
                logger.info(f"Position opened: ID={position_id} | Entry={filled_price:.4f}")
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

        # Start position monitor in background
        self.position_monitor_task = asyncio.create_task(self.position_monitor_loop())

        # Start trading loop
        await self.trading_loop()

    def shutdown(self):
        """Graceful shutdown"""
        logger.info("\nShutting down ScalperBot...")
        self.running = False

        if self.poller_task:
            self.poller.stop()

        if self.position_monitor_task:
            self.position_monitor_task.cancel()

        # Show final stats
        stats = self.db.get_daily_stats()
        logger.info(f"Final stats: PnL=${stats['pnl']:.2f} | Trades={stats['num_trades']} | "
                   f"Wins={stats['win_count']} | Losses={stats['loss_count']} | "
                   f"Win Rate={stats['win_rate']:.1f}%")

        self.db.close()
        logger.info("Shutdown complete")


async def main():
    """Main entry point"""
    bot = ScalperBot()

    # Handle shutdown signals
    def signal_handler(sig, frame):
        logger.info(f"\nReceived signal {sig}")
        bot.shutdown()
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    try:
        await bot.run()
    except KeyboardInterrupt:
        logger.info("\nKeyboard interrupt received")
        bot.shutdown()
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        bot.shutdown()
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
