"""
Position Manager Module
Manages open positions and generates exit signals (Take Profit / Stop Loss)
"""
import logging
from datetime import datetime
from typing import List, Dict, Any, Optional
from db import TradeDB
from datafeed.orderbook import OrderBook
from config import settings

logger = logging.getLogger(__name__)


class PositionManager:
    """
    Manages open positions and generates exit signals

    Features:
    - Take Profit: Close position when profit reaches target %
    - Stop Loss: Close position when loss reaches limit %
    - Trailing Stop: (Optional) Lock in profits as price moves up
    - Time-based exit: Close position after max holding time
    """

    # MEXC fee rates (taker fees for market orders)
    TAKER_FEE_PCT = 0.1  # 0.1% taker fee on MEXC

    def __init__(
        self,
        db: TradeDB,
        orderbook: OrderBook,
        take_profit_pct: float = 1.5,
        stop_loss_pct: float = 1.0,
        max_hold_hours: int = 24,
        enable_trailing_stop: bool = False,
        trailing_stop_pct: float = 0.5
    ):
        self.db = db
        self.orderbook = orderbook

        # Exit parameters
        self.take_profit_pct = take_profit_pct
        self.stop_loss_pct = stop_loss_pct
        self.max_hold_hours = max_hold_hours

        # Trailing stop
        self.enable_trailing_stop = enable_trailing_stop
        self.trailing_stop_pct = trailing_stop_pct
        self.highest_prices: Dict[int, float] = {}  # trade_id -> highest price seen

        # Track pending exits to prevent race conditions
        self._pending_exits: set = set()

        logger.info(f"PositionManager initialized: TP={take_profit_pct}%, SL={stop_loss_pct}%, MaxHold={max_hold_hours}h")

    def get_open_positions(self) -> List[Dict[str, Any]]:
        """Get all open positions from database"""
        return self.db.get_open_positions()

    def get_current_price(self, symbol: str) -> Optional[float]:
        """Get current mid price for a symbol"""
        return self.orderbook.get_mid_price(symbol)

    def calculate_pnl_pct(self, entry_price: float, current_price: float, side: str, include_fees: bool = False) -> float:
        """
        Calculate PnL percentage for a position

        Args:
            entry_price: Entry price
            current_price: Current/exit price
            side: 'buy' or 'sell'
            include_fees: If True, deduct estimated taker fees (entry + exit)

        Returns:
            PnL percentage (negative for loss)
        """
        if side.lower() == 'buy':
            # Long position: profit when price goes up
            pnl_pct = ((current_price - entry_price) / entry_price) * 100
        else:
            # Short position: profit when price goes down
            pnl_pct = ((entry_price - current_price) / entry_price) * 100

        if include_fees:
            # Deduct fees: entry taker fee + exit taker fee
            total_fees = self.TAKER_FEE_PCT * 2  # 0.2% total
            pnl_pct -= total_fees

        return pnl_pct

    def check_take_profit(self, position: Dict, current_price: float) -> bool:
        """Check if position has reached take profit target"""
        entry_price = position['price']
        side = position['side']

        pnl_pct = self.calculate_pnl_pct(entry_price, current_price, side)

        if pnl_pct >= self.take_profit_pct:
            logger.info(f"🎯 Take Profit hit for {position['symbol']}: {pnl_pct:.2f}% >= {self.take_profit_pct}%")
            return True
        return False

    def check_stop_loss(self, position: Dict, current_price: float) -> bool:
        """Check if position has hit stop loss"""
        entry_price = position['price']
        side = position['side']

        pnl_pct = self.calculate_pnl_pct(entry_price, current_price, side)

        if pnl_pct <= -self.stop_loss_pct:
            logger.warning(f"🛑 Stop Loss hit for {position['symbol']}: {pnl_pct:.2f}% <= -{self.stop_loss_pct}%")
            return True
        return False

    def check_trailing_stop(self, position: Dict, current_price: float) -> bool:
        """Check if trailing stop has been triggered"""
        if not self.enable_trailing_stop:
            return False

        trade_id = position['id']
        entry_price = position['price']
        side = position['side']

        # Track highest price seen for this position
        if trade_id not in self.highest_prices:
            self.highest_prices[trade_id] = current_price

        if side.lower() == 'buy':
            # Long: track highest price
            if current_price > self.highest_prices[trade_id]:
                self.highest_prices[trade_id] = current_price

            # Check if price dropped from high by trailing_stop_pct
            high = self.highest_prices[trade_id]
            drop_pct = ((high - current_price) / high) * 100

            if drop_pct >= self.trailing_stop_pct and current_price > entry_price:
                logger.info(f"📉 Trailing Stop hit for {position['symbol']}: dropped {drop_pct:.2f}% from high")
                return True

        return False

    def check_max_hold_time(self, position: Dict) -> bool:
        """Check if position has exceeded max holding time"""
        try:
            entry_time = datetime.fromisoformat(position['timestamp'])
            now = datetime.utcnow()
            hours_held = (now - entry_time).total_seconds() / 3600

            if hours_held >= self.max_hold_hours:
                logger.info(f"⏰ Max hold time reached for {position['symbol']}: {hours_held:.1f}h >= {self.max_hold_hours}h")
                return True
        except Exception as e:
            logger.warning(f"Could not parse timestamp for position {position['id']}: {e}")

        return False

    def generate_exit_signals(self) -> List[Dict[str, Any]]:
        """
        Check all open positions and generate exit signals

        Returns list of exit signals with reason
        """
        exit_signals = []
        positions = self.get_open_positions()

        if not positions:
            return exit_signals

        logger.info(f"📊 Checking {len(positions)} open positions for exit conditions...")

        for position in positions:
            trade_id = position['id']
            symbol = position['symbol']

            # Skip if already pending exit (race condition protection)
            if trade_id in self._pending_exits:
                logger.debug(f"Skipping {symbol} (trade_id={trade_id}) - already pending exit")
                continue

            current_price = self.get_current_price(symbol)

            if current_price is None:
                logger.warning(f"⚠️ No price data for {symbol}, skipping exit check")
                continue

            entry_price = position['price']

            # Calculate PnL with fees for accurate reporting
            pnl_pct_raw = self.calculate_pnl_pct(entry_price, current_price, position['side'], include_fees=False)
            pnl_pct_net = self.calculate_pnl_pct(entry_price, current_price, position['side'], include_fees=True)

            logger.debug(f"{symbol}: Entry=${entry_price:.4f}, Current=${current_price:.4f}, PnL={pnl_pct_raw:.2f}% (net: {pnl_pct_net:.2f}%)")

            exit_reason = None

            # Check exit conditions in order of priority
            if self.check_stop_loss(position, current_price):
                exit_reason = 'STOP_LOSS'
            elif self.check_take_profit(position, current_price):
                exit_reason = 'TAKE_PROFIT'
            elif self.check_trailing_stop(position, current_price):
                exit_reason = 'TRAILING_STOP'
            elif self.check_max_hold_time(position):
                exit_reason = 'MAX_HOLD_TIME'

            if exit_reason:
                # Mark as pending to prevent duplicate exits
                self._pending_exits.add(trade_id)

                exit_signal = {
                    'symbol': symbol,
                    'action': 'SELL',
                    'price': current_price,
                    'entry_price': entry_price,
                    'quantity': position['quantity'],
                    'pnl_pct': pnl_pct_raw,  # Report raw PnL
                    'pnl_pct_net': pnl_pct_net,  # Net PnL after fees
                    'pnl_usd': position['notional'] * (pnl_pct_net / 100),  # Use net for USD
                    'reason': exit_reason,
                    'trade_id': trade_id,
                    'timestamp': datetime.utcnow().isoformat()
                }
                exit_signals.append(exit_signal)
                logger.info(f"🔴 EXIT SIGNAL: {symbol} - {exit_reason} - PnL: {pnl_pct_raw:.2f}% (net: {pnl_pct_net:.2f}%)")

        return exit_signals

    def close_position(self, trade_id: int, exit_price: float, pnl: float) -> bool:
        """
        Mark a position as closed in the database (atomic operation).

        Returns:
            True if position was closed, False if already closed (idempotent)
        """
        # Use atomic close to prevent duplicate exits on restart
        was_closed = self.db.close_position_atomic(trade_id, pnl)

        if not was_closed:
            logger.warning(f"Position {trade_id} already closed (idempotent skip)")
            # Clear from pending exits if it was there
            self._pending_exits.discard(trade_id)
            return False

        # Clean up tracking
        self._pending_exits.discard(trade_id)
        if trade_id in self.highest_prices:
            del self.highest_prices[trade_id]

        logger.info(f"Position {trade_id} closed. PnL: ${pnl:.2f}")
        return True

    def get_position_summary(self) -> str:
        """Get a summary of all open positions"""
        positions = self.get_open_positions()

        if not positions:
            return "No open positions"

        lines = [f"📊 Open Positions ({len(positions)}/{settings.max_positions}):"]

        total_pnl = 0.0
        for pos in positions:
            symbol = pos['symbol']
            current_price = self.get_current_price(symbol)

            if current_price:
                pnl_pct = self.calculate_pnl_pct(pos['price'], current_price, pos['side'])
                pnl_usd = pos['notional'] * (pnl_pct / 100)
                total_pnl += pnl_usd

                emoji = "🟢" if pnl_pct >= 0 else "🔴"
                lines.append(f"  {emoji} {symbol}: ${pos['price']:.4f} → ${current_price:.4f} ({pnl_pct:+.2f}%)")
            else:
                lines.append(f"  ⚪ {symbol}: ${pos['price']:.4f} (no current price)")

        lines.append(f"  Total Unrealized PnL: ${total_pnl:.2f}")

        return "\n".join(lines)
