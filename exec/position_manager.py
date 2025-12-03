"""
Position Manager - Tracks open positions and handles exit logic (TP/SL)
"""
import logging
from typing import Dict, List, Optional
from datetime import datetime, timezone
from config import settings

logger = logging.getLogger(__name__)


class PositionManager:
    """
    Manages open positions and monitors for exit conditions:
    - Take profit (TP)
    - Stop loss (SL)
    - Trailing stop (optional)
    - Max hold time (optional)
    """

    def __init__(
        self,
        db,
        exchange,
        take_profit_pct: float = None,
        stop_loss_pct: float = None,
        enable_trailing_stop: bool = False,
        trailing_stop_pct: float = 0.5,
        max_hold_minutes: int = None
    ):
        self.db = db
        self.exchange = exchange
        self.take_profit_pct = take_profit_pct or getattr(settings, 'take_profit_pct', 1.5)
        self.stop_loss_pct = stop_loss_pct or getattr(settings, 'stop_loss_pct', 1.0)
        self.enable_trailing_stop = enable_trailing_stop
        self.trailing_stop_pct = trailing_stop_pct
        self.max_hold_minutes = max_hold_minutes

        # In-memory position tracking: {symbol: position_data}
        self.positions: Dict[str, Dict] = {}

        # High water marks for trailing stops
        self.high_water_marks: Dict[str, float] = {}

        logger.info(f"PositionManager initialized: TP={self.take_profit_pct}%, SL={self.stop_loss_pct}%")

    def has_open_position(self, symbol: str) -> bool:
        """Check if we have an open position for this symbol"""
        return symbol in self.positions

    def get_open_positions(self) -> Dict[str, Dict]:
        """Get all open positions"""
        return self.positions.copy()

    def open_position(
        self,
        symbol: str,
        entry_price: float,
        quantity: float,
        trade_id: int,
        side: str = 'buy'
    ):
        """Record a new open position"""
        self.positions[symbol] = {
            'symbol': symbol,
            'entry_price': entry_price,
            'quantity': quantity,
            'trade_id': trade_id,
            'side': side,
            'entry_time': datetime.now(timezone.utc),
            'high_price': entry_price  # For trailing stop
        }
        self.high_water_marks[symbol] = entry_price
        logger.info(f"Position opened: {symbol} @ {entry_price:.4f}, qty={quantity:.6f}")

    def close_position(self, symbol: str, exit_price: float, reason: str) -> Optional[Dict]:
        """
        Close a position and calculate PnL
        Returns position data with PnL info
        """
        if symbol not in self.positions:
            logger.warning(f"No open position for {symbol}")
            return None

        position = self.positions.pop(symbol)
        self.high_water_marks.pop(symbol, None)

        entry_price = position['entry_price']
        quantity = position['quantity']

        # Calculate PnL
        if position['side'] == 'buy':
            pnl = (exit_price - entry_price) * quantity
            pnl_pct = ((exit_price - entry_price) / entry_price) * 100
        else:
            pnl = (entry_price - exit_price) * quantity
            pnl_pct = ((entry_price - exit_price) / entry_price) * 100

        position['exit_price'] = exit_price
        position['exit_time'] = datetime.now(timezone.utc)
        position['pnl'] = pnl
        position['pnl_pct'] = pnl_pct
        position['exit_reason'] = reason

        logger.info(f"Position closed: {symbol} @ {exit_price:.4f}, PnL: ${pnl:.2f} ({pnl_pct:+.2f}%) - {reason}")

        return position

    def check_exits(self, current_prices: Dict[str, float]) -> List[Dict]:
        """
        Check all positions for exit conditions
        Returns list of positions that should be closed with reason
        """
        exits = []

        for symbol, position in list(self.positions.items()):
            if symbol not in current_prices:
                continue

            current_price = current_prices[symbol]
            entry_price = position['entry_price']

            # Update high water mark for trailing stop
            if current_price > self.high_water_marks.get(symbol, 0):
                self.high_water_marks[symbol] = current_price
                position['high_price'] = current_price

            # Calculate current PnL %
            pnl_pct = ((current_price - entry_price) / entry_price) * 100

            # Check take profit
            if pnl_pct >= self.take_profit_pct:
                exits.append({
                    'symbol': symbol,
                    'position': position,
                    'current_price': current_price,
                    'reason': f'TAKE_PROFIT ({pnl_pct:.2f}% >= {self.take_profit_pct}%)',
                    'pnl_pct': pnl_pct
                })
                continue

            # Check stop loss
            if pnl_pct <= -self.stop_loss_pct:
                exits.append({
                    'symbol': symbol,
                    'position': position,
                    'current_price': current_price,
                    'reason': f'STOP_LOSS ({pnl_pct:.2f}% <= -{self.stop_loss_pct}%)',
                    'pnl_pct': pnl_pct
                })
                continue

            # Check trailing stop
            if self.enable_trailing_stop and pnl_pct > 0:
                high_price = self.high_water_marks.get(symbol, entry_price)
                drop_from_high = ((high_price - current_price) / high_price) * 100

                if drop_from_high >= self.trailing_stop_pct:
                    exits.append({
                        'symbol': symbol,
                        'position': position,
                        'current_price': current_price,
                        'reason': f'TRAILING_STOP (dropped {drop_from_high:.2f}% from high)',
                        'pnl_pct': pnl_pct
                    })
                    continue

            # Check max hold time
            if self.max_hold_minutes:
                hold_time = (datetime.now(timezone.utc) - position['entry_time']).total_seconds() / 60
                if hold_time >= self.max_hold_minutes:
                    exits.append({
                        'symbol': symbol,
                        'position': position,
                        'current_price': current_price,
                        'reason': f'MAX_HOLD_TIME ({hold_time:.0f} min >= {self.max_hold_minutes} min)',
                        'pnl_pct': pnl_pct
                    })

        return exits

    def sync_from_db(self, verify_on_exchange: bool = False):
        """
        Sync positions from database on startup

        Args:
            verify_on_exchange: If True, verify positions exist on exchange (LIVE mode)
        """
        try:
            open_trades = self.db.get_open_positions()

            if not open_trades:
                logger.info("No open positions in database")
                return

            # Get actual exchange positions for verification
            exchange_positions = {}
            if verify_on_exchange:
                try:
                    balance = self.exchange.fetch_balance()
                    for symbol, info in balance.items():
                        if isinstance(info, dict) and info.get('free', 0) > 0:
                            exchange_positions[symbol] = info.get('free', 0)
                    logger.info(f"Exchange positions fetched: {list(exchange_positions.keys())}")
                except Exception as e:
                    logger.warning(f"Could not fetch exchange positions: {e}")

            synced_count = 0
            skipped_count = 0

            for trade in open_trades:
                symbol = trade['symbol']
                base_currency = symbol.split('/')[0] if '/' in symbol else symbol

                # In LIVE mode, verify position exists on exchange
                if verify_on_exchange:
                    exchange_qty = exchange_positions.get(base_currency, 0)
                    if exchange_qty < trade['quantity'] * 0.9:  # Allow 10% tolerance
                        logger.warning(
                            f"SKIPPING phantom position {symbol}: "
                            f"DB qty={trade['quantity']:.6f}, Exchange qty={exchange_qty:.6f}"
                        )
                        # Mark as CLOSED in database to prevent future loading
                        self.db.update_trade_status(trade['id'], 'PHANTOM_CLOSED')
                        skipped_count += 1
                        continue

                self.positions[symbol] = {
                    'symbol': symbol,
                    'entry_price': trade['price'],
                    'quantity': trade['quantity'],
                    'trade_id': trade['id'],
                    'side': trade['side'],
                    'entry_time': trade.get('created_at', datetime.now(timezone.utc)),
                    'high_price': trade['price']
                }
                self.high_water_marks[symbol] = trade['price']
                synced_count += 1

            logger.info(f"Synced {synced_count} open positions from database")
            if skipped_count > 0:
                logger.warning(f"Skipped {skipped_count} phantom positions (not found on exchange)")
        except Exception as e:
            logger.warning(f"Could not sync positions from DB: {e}")

    def get_position_summary(self) -> str:
        """Get summary of open positions"""
        if not self.positions:
            return "No open positions"

        lines = [f"Open Positions ({len(self.positions)}):"]
        for symbol, pos in self.positions.items():
            lines.append(f"  {symbol}: {pos['quantity']:.6f} @ {pos['entry_price']:.4f}")
        return "\n".join(lines)
