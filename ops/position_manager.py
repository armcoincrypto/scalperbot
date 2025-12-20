"""
Position Manager Module
Handles position monitoring, exit logic, trailing stops, and PnL calculation
"""
import logging
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List
from config import settings

logger = logging.getLogger(__name__)


class PositionManager:
    """
    Manages open positions with:
    - Take Profit / Stop Loss monitoring
    - Trailing stop logic
    - Time-based exits
    - PnL calculation and recording
    """

    def __init__(self, db, exchange, orderbook, router):
        self.db = db
        self.exchange = exchange
        self.orderbook = orderbook
        self.router = router

        # Config
        self.take_profit_pct = settings.take_profit_pct
        self.stop_loss_pct = settings.stop_loss_pct
        self.trailing_enabled = settings.trailing_enabled
        self.trail_start_pct = settings.trail_start_pct
        self.trail_offset_pct = settings.trail_offset_pct
        self.max_hold_hours = settings.max_hold_hours

    def calculate_tp_sl_prices(self, entry_price: float, side: str) -> Dict[str, float]:
        """Calculate take profit and stop loss prices"""
        if side == 'buy':
            # Long position: TP above, SL below
            tp_price = entry_price * (1 + self.take_profit_pct / 100)
            sl_price = entry_price * (1 - self.stop_loss_pct / 100)
        else:
            # Short position: TP below, SL above
            tp_price = entry_price * (1 - self.take_profit_pct / 100)
            sl_price = entry_price * (1 + self.stop_loss_pct / 100)

        return {
            'take_profit_price': tp_price,
            'stop_loss_price': sl_price
        }

    def check_exit_conditions(self, position: Dict[str, Any], current_price: float) -> Optional[Dict[str, Any]]:
        """
        Check if position should be exited

        Returns:
            None if no exit needed
            Dict with 'reason', 'exit_price' if exit triggered
        """
        entry_price = position['entry_price']
        side = position['side']
        tp_price = position['take_profit_price']
        sl_price = position['stop_loss_price']
        trailing_stop = position.get('trailing_stop_price')
        highest_price = position.get('highest_price', entry_price)
        opened_at = position['opened_at']

        # For long positions
        if side == 'buy':
            # Check Take Profit
            if current_price >= tp_price:
                return {
                    'reason': 'TAKE_PROFIT',
                    'exit_price': current_price,
                    'message': f'TP hit: {current_price:.4f} >= {tp_price:.4f}'
                }

            # Check Stop Loss
            if current_price <= sl_price:
                return {
                    'reason': 'STOP_LOSS',
                    'exit_price': current_price,
                    'message': f'SL hit: {current_price:.4f} <= {sl_price:.4f}'
                }

            # Check Trailing Stop (if activated)
            if trailing_stop and current_price <= trailing_stop:
                return {
                    'reason': 'TRAILING_STOP',
                    'exit_price': current_price,
                    'message': f'Trailing stop hit: {current_price:.4f} <= {trailing_stop:.4f}'
                }

        else:  # Short position
            # Check Take Profit (price drops)
            if current_price <= tp_price:
                return {
                    'reason': 'TAKE_PROFIT',
                    'exit_price': current_price,
                    'message': f'TP hit: {current_price:.4f} <= {tp_price:.4f}'
                }

            # Check Stop Loss (price rises)
            if current_price >= sl_price:
                return {
                    'reason': 'STOP_LOSS',
                    'exit_price': current_price,
                    'message': f'SL hit: {current_price:.4f} >= {sl_price:.4f}'
                }

            # Check Trailing Stop
            if trailing_stop and current_price >= trailing_stop:
                return {
                    'reason': 'TRAILING_STOP',
                    'exit_price': current_price,
                    'message': f'Trailing stop hit: {current_price:.4f} >= {trailing_stop:.4f}'
                }

        # Check Time-based exit
        if self.max_hold_hours > 0:
            opened_time = datetime.fromisoformat(opened_at)
            hold_duration = datetime.utcnow() - opened_time
            max_duration = timedelta(hours=self.max_hold_hours)

            if hold_duration >= max_duration:
                return {
                    'reason': 'TIME_EXIT',
                    'exit_price': current_price,
                    'message': f'Max hold time exceeded: {hold_duration.total_seconds()/3600:.1f}h >= {self.max_hold_hours}h'
                }

        return None

    def update_trailing_stop(self, position: Dict[str, Any], current_price: float) -> bool:
        """
        Update trailing stop if price moved favorably

        Returns:
            True if trailing stop was updated
        """
        if not self.trailing_enabled:
            return False

        entry_price = position['entry_price']
        side = position['side']
        highest_price = position.get('highest_price', entry_price)
        position_id = position['id']

        # Guard against invalid entry_price
        if not entry_price or entry_price <= 0:
            logger.warning(f"Invalid entry_price={entry_price} for position {position_id}")
            return False

        # Calculate current profit percentage
        if side == 'buy':
            profit_pct = ((current_price - entry_price) / entry_price) * 100
            new_high = current_price > highest_price
        else:
            profit_pct = ((entry_price - current_price) / entry_price) * 100
            new_high = current_price < highest_price

        # Check if we should activate/update trailing stop
        if profit_pct >= self.trail_start_pct:
            if side == 'buy':
                if new_high:
                    # Update highest price and trailing stop
                    new_highest = current_price
                    # Trail behind the high by offset percentage
                    new_trailing_stop = new_highest * (1 - self.trail_offset_pct / 100)

                    # Ensure trailing stop is at least break-even + small cushion
                    min_trailing = entry_price * (1 + 0.1 / 100)  # At least +0.1%
                    new_trailing_stop = max(new_trailing_stop, min_trailing)

                    self.db.update_position_trailing(position_id, new_highest, new_trailing_stop)
                    logger.info(f"🔄 Updated trailing stop: highest={new_highest:.4f}, trail_stop={new_trailing_stop:.4f}")
                    return True
            else:
                if new_high:  # For shorts, "new high" means new low price
                    new_lowest = current_price
                    new_trailing_stop = new_lowest * (1 + self.trail_offset_pct / 100)

                    # Ensure trailing stop is at least break-even
                    max_trailing = entry_price * (1 - 0.1 / 100)
                    new_trailing_stop = min(new_trailing_stop, max_trailing)

                    self.db.update_position_trailing(position_id, new_lowest, new_trailing_stop)
                    logger.info(f"🔄 Updated trailing stop (short): lowest={new_lowest:.4f}, trail_stop={new_trailing_stop:.4f}")
                    return True

        return False

    def calculate_pnl(self, position: Dict[str, Any], exit_price: float) -> Dict[str, float]:
        """Calculate PnL for a position"""
        entry_price = position['entry_price']
        quantity = position['quantity']
        side = position['side']
        notional = position['notional']

        # Guard against invalid entry_price
        if not entry_price or entry_price <= 0:
            logger.error(f"Invalid entry_price={entry_price} in PnL calculation")
            entry_price = exit_price  # Fallback to break-even

        if side == 'buy':
            # Long: profit if exit > entry
            price_diff = exit_price - entry_price
            pnl = price_diff * quantity
            pnl_pct = (price_diff / entry_price) * 100 if entry_price > 0 else 0
        else:
            # Short: profit if exit < entry
            price_diff = entry_price - exit_price
            pnl = price_diff * quantity
            pnl_pct = (price_diff / entry_price) * 100 if entry_price > 0 else 0

        # Estimate fees (MEXC is typically 0.1% taker)
        entry_fee = notional * 0.001
        exit_fee = (exit_price * quantity) * 0.001
        total_fee = entry_fee + exit_fee

        net_pnl = pnl - total_fee

        return {
            'gross_pnl': pnl,
            'fee': total_fee,
            'net_pnl': net_pnl,
            'pnl_pct': pnl_pct,
            'is_win': net_pnl > 0
        }

    async def close_position(self, position: Dict[str, Any], exit_price: float, reason: str) -> bool:
        """
        Close a position and record PnL

        Returns:
            True if position was closed successfully
        """
        symbol = position['symbol']
        quantity = position['quantity']
        position_id = position['id']
        trade_id = position['trade_id']

        # CRITICAL: Check if already closing (prevents duplicate close attempts)
        current_status = position.get('status', 'OPEN')
        if current_status == 'CLOSING':
            logger.debug(f"{symbol}: Already closing, skipping duplicate attempt")
            return False
        if current_status == 'CLOSED':
            logger.debug(f"{symbol}: Already closed, skipping")
            return False

        # Mark as CLOSING immediately to prevent concurrent close attempts
        self.db.update_position_status(position_id, 'CLOSING')
        logger.info(f"\n{'*'*60}")
        logger.info(f"🔻 CLOSING POSITION: {symbol}")
        logger.info(f"   Reason: {reason}")
        logger.info(f"   Exit price: {exit_price:.4f}")
        logger.info(f"{'*'*60}")

        try:
            # Place sell order (for long positions)
            side = 'sell' if position['side'] == 'buy' else 'buy'

            if not settings.dry_run:
                order = self.router.place_market_order(symbol, side, quantity)
                if not order:
                    logger.error(f"❌ Failed to place exit order for {symbol}")
                    self.db.update_position_status(position_id, 'OPEN')  # Reset to allow retry
                    return False

                # CRITICAL: Verify exit order actually filled
                order_id = order.get('id')
                order_status = (order.get('status') or '').lower()
                filled_qty = order.get('filled', 0) or 0

                if order_status in ['closed', 'filled'] and filled_qty > 0:
                    # Order filled successfully
                    filled_price = order.get('average') or order.get('price') or exit_price
                    exit_price = filled_price
                    logger.info(f"✅ Exit order verified FILLED: qty={filled_qty}, price={filled_price:.4f}")
                elif order_status == 'open':
                    # Order still open - shouldn't happen with market orders
                    logger.warning(f"⚠️ Exit order still OPEN - waiting for fill: {order_id}")
                    # Try to fetch updated status
                    fetched = self.router.get_order_status(order_id, symbol)
                    if fetched and (fetched.get('status') or '').lower() in ['closed', 'filled']:
                        filled_price = fetched.get('average') or fetched.get('price') or exit_price
                        exit_price = filled_price
                        logger.info(f"✅ Exit order confirmed filled after fetch")
                    else:
                        logger.error(f"❌ Exit order did not fill - position remains open")
                        self.db.update_position_status(position_id, 'OPEN')  # Reset to allow retry
                        return False
                elif order_status in ['canceled', 'cancelled', 'rejected', 'expired']:
                    logger.error(f"❌ Exit order {order_status.upper()}: {order_id}")
                    self.db.update_position_status(position_id, 'OPEN')  # Reset to allow retry
                    return False
                else:
                    # Unknown status - fetch to verify
                    logger.warning(f"⚠️ Unknown exit order status '{order_status}', verifying...")
                    fetched = self.router.get_order_status(order_id, symbol) if order_id else None
                    if fetched:
                        order_status = (fetched.get('status') or '').lower()
                        filled_qty = fetched.get('filled', 0) or 0
                        if order_status in ['closed', 'filled'] and filled_qty > 0:
                            filled_price = fetched.get('average') or fetched.get('price') or exit_price
                            exit_price = filled_price
                            logger.info(f"✅ Exit order verified after fetch: status={order_status}, filled={filled_qty}")
                        else:
                            logger.error(f"❌ Exit order not filled: status={order_status}, filled={filled_qty}")
                            self.db.update_position_status(position_id, 'OPEN')  # Reset to allow retry
                            return False
                    else:
                        logger.error(f"❌ Could not verify exit order status")
                        self.db.update_position_status(position_id, 'OPEN')  # Reset to allow retry
                        return False
            else:
                logger.info(f"🔶 [DRY_RUN] Would place {side} order for {quantity:.6f} {symbol}")

            # Calculate PnL
            pnl_result = self.calculate_pnl(position, exit_price)

            logger.info(f"   Gross PnL: ${pnl_result['gross_pnl']:.2f} ({pnl_result['pnl_pct']:.2f}%)")
            logger.info(f"   Fees: ${pnl_result['fee']:.2f}")
            logger.info(f"   Net PnL: ${pnl_result['net_pnl']:.2f}")
            logger.info(f"   Result: {'✅ WIN' if pnl_result['is_win'] else '❌ LOSS'}")

            # Update position in database
            self.db.close_position(
                position_id=position_id,
                exit_price=exit_price,
                exit_reason=reason,
                pnl=pnl_result['net_pnl'],
                fee=pnl_result['fee']
            )

            # Update trade PnL
            self.db.update_trade_pnl(trade_id, pnl_result['net_pnl'])

            # Update daily PnL with win/loss
            self.db.update_daily_pnl_with_result(
                pnl=pnl_result['net_pnl'],
                is_win=pnl_result['is_win']
            )

            # Update original trade status
            self.db.update_trade_status(trade_id, 'CLOSED')

            logger.info(f"✅ Position closed successfully")
            return True

        except Exception as e:
            logger.error(f"❌ Error closing position: {e}", exc_info=True)
            self.db.update_position_status(position_id, 'OPEN')  # Reset to allow retry
            return False

    async def check_all_positions(self) -> List[Dict[str, Any]]:
        """
        Check all open positions for exit conditions

        Returns:
            List of closed positions
        """
        positions = self.db.get_open_positions()
        closed_positions = []

        if not positions:
            return closed_positions

        logger.debug(f"Checking {len(positions)} open positions...")

        for position in positions:
            symbol = position['symbol']

            try:
                # Get current price
                current_price = self.orderbook.get_mid_price(symbol)
                if not current_price:
                    logger.warning(f"⚠️ No price data for {symbol}")
                    continue

                # Update trailing stop if needed
                self.update_trailing_stop(position, current_price)

                # Check exit conditions
                exit_signal = self.check_exit_conditions(position, current_price)

                if exit_signal:
                    logger.info(f"📢 Exit signal for {symbol}: {exit_signal['message']}")

                    # Close position
                    success = await self.close_position(
                        position,
                        exit_signal['exit_price'],
                        exit_signal['reason']
                    )

                    if success:
                        closed_positions.append({
                            'symbol': symbol,
                            'reason': exit_signal['reason'],
                            'exit_price': exit_signal['exit_price']
                        })

            except Exception as e:
                logger.error(f"❌ Error checking position {symbol}: {e}", exc_info=True)

        return closed_positions

    def get_positions_summary(self) -> str:
        """Get a summary of open positions"""
        positions = self.db.get_open_positions()

        if not positions:
            return "📊 No open positions"

        lines = [f"📊 Open Positions ({len(positions)}):"]
        for pos in positions:
            symbol = pos['symbol']
            entry = pos['entry_price']
            tp = pos['take_profit_price']
            sl = pos['stop_loss_price']
            trail = pos.get('trailing_stop_price', 'N/A')

            current_price = self.orderbook.get_mid_price(symbol) or entry
            pnl_pct = ((current_price - entry) / entry) * 100 if pos['side'] == 'buy' else ((entry - current_price) / entry) * 100

            lines.append(
                f"  {symbol}: Entry={entry:.4f} | Current={current_price:.4f} | "
                f"PnL={pnl_pct:+.2f}% | TP={tp:.4f} | SL={sl:.4f} | Trail={trail}"
            )

        return "\n".join(lines)
