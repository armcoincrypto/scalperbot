"""
Position Sizing Module
Calculates position sizes based on available capital and risk parameters
"""
import logging
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, Set
from config import settings

logger = logging.getLogger(__name__)


class PositionSizer:
    """
    Calculates position sizes for trades
    - Fixed notional sizing
    - Risk-based sizing (future)
    - Max position limits (total and per-symbol)
    - Trade cooldown per symbol
    """

    def __init__(self, default_size_usd: float = None, max_positions: int = None, db=None):
        self.default_size_usd = default_size_usd or settings.position_size_usd
        self.max_positions = max_positions or settings.max_positions
        self.max_positions_per_symbol = getattr(settings, 'max_positions_per_symbol', 1)
        self.trade_cooldown_seconds = getattr(settings, 'trade_cooldown_seconds', 300)
        self.max_exposure_pct = getattr(settings, 'max_exposure_pct', 10.0)
        self.per_symbol_max_notional_pct = getattr(settings, 'per_symbol_max_notional_pct', 5.0)
        self.current_positions = 0
        self.db = db

        # Account balance for exposure calculations (updated externally)
        self.account_balance_usd: float = 0.0

        # Track last trade time per symbol for cooldown
        self._last_trade_time: Dict[str, datetime] = {}

        # Sync positions from database if available
        if self.db:
            self.sync_from_database()

    def sync_from_database(self):
        """Sync position count from database open positions"""
        if not self.db:
            return

        try:
            open_positions = self.db.get_open_positions()
            self.current_positions = len(open_positions)
            logger.info(f"Synced position count from DB: {self.current_positions} open positions")
        except Exception as e:
            logger.warning(f"Could not sync positions from DB: {e}")

    def calculate_size(
        self,
        symbol: str,
        price: float,
        balance_usd: Optional[float] = None
    ) -> Dict[str, Any]:
        """
        Calculate position size in base currency

        Args:
            symbol: Trading pair (e.g., 'BTC/USDT')
            price: Current price
            balance_usd: Available balance (optional)

        Returns:
            Dict with 'quantity', 'notional_usd', 'can_trade'
        """
        # Check if we can open more positions
        if self.current_positions >= self.max_positions:
            logger.warning(f"⚠️ Max positions ({self.max_positions}) reached, cannot open new position")
            return {
                'quantity': 0,
                'notional_usd': 0,
                'can_trade': False,
                'reason': 'Max positions reached'
            }

        # Use default fixed size
        notional_usd = self.default_size_usd

        # If balance provided, limit to available capital
        if balance_usd is not None:
            if balance_usd < notional_usd:
                logger.warning(f"⚠️ Insufficient balance: ${balance_usd:.2f} < ${notional_usd:.2f}")
                return {
                    'quantity': 0,
                    'notional_usd': 0,
                    'can_trade': False,
                    'reason': 'Insufficient balance'
                }

        # Calculate quantity in base currency
        quantity = notional_usd / price

        logger.info(f"Position size for {symbol}: {quantity:.6f} (${notional_usd:.2f} @ {price:.4f})")

        return {
            'quantity': quantity,
            'notional_usd': notional_usd,
            'can_trade': True,
            'reason': 'OK'
        }

    def increment_positions(self):
        """Increment current position count"""
        self.current_positions += 1
        logger.info(f"Open positions: {self.current_positions}/{self.max_positions}")

    def decrement_positions(self):
        """Decrement current position count"""
        self.current_positions = max(0, self.current_positions - 1)
        logger.info(f"Open positions: {self.current_positions}/{self.max_positions}")

    def reset_positions(self):
        """Reset position counter"""
        self.current_positions = 0
        logger.info("Position counter reset to 0")

    def can_open_position(self) -> bool:
        """Check if we can open a new position (total limit only)"""
        return self.current_positions < self.max_positions

    def get_positions_for_symbol(self, symbol: str) -> int:
        """Get count of open positions for a specific symbol"""
        if not self.db:
            return 0
        try:
            open_positions = self.db.get_open_positions()
            return sum(1 for p in open_positions if p['symbol'] == symbol)
        except Exception as e:
            logger.warning(f"Could not count positions for {symbol}: {e}")
            return 0

    def is_on_cooldown(self, symbol: str) -> bool:
        """Check if symbol is on trade cooldown"""
        if symbol not in self._last_trade_time:
            return False

        elapsed = (datetime.utcnow() - self._last_trade_time[symbol]).total_seconds()
        if elapsed < self.trade_cooldown_seconds:
            remaining = self.trade_cooldown_seconds - elapsed
            logger.info(f"⏳ {symbol} on cooldown: {remaining:.0f}s remaining")
            return True
        return False

    def record_trade(self, symbol: str):
        """Record a trade for cooldown tracking"""
        self._last_trade_time[symbol] = datetime.utcnow()
        logger.debug(f"Trade recorded for {symbol}, cooldown started")

    def set_account_balance(self, balance_usd: float):
        """Set account balance for exposure calculations"""
        self.account_balance_usd = balance_usd
        logger.debug(f"Account balance set to ${balance_usd:.2f}")

    def get_total_exposure(self) -> float:
        """Get total notional exposure across all open positions"""
        if not self.db:
            return 0.0
        try:
            open_positions = self.db.get_open_positions()
            return sum(p.get('notional', 0) for p in open_positions)
        except Exception as e:
            logger.warning(f"Could not calculate total exposure: {e}")
            return 0.0

    def get_symbol_exposure(self, symbol: str) -> float:
        """Get total notional exposure for a specific symbol"""
        if not self.db:
            return 0.0
        try:
            open_positions = self.db.get_open_positions()
            return sum(p.get('notional', 0) for p in open_positions if p['symbol'] == symbol)
        except Exception as e:
            logger.warning(f"Could not calculate exposure for {symbol}: {e}")
            return 0.0

    def check_exposure_limits(self, symbol: str, new_notional: float) -> tuple[bool, str]:
        """
        Check if adding a new position would exceed exposure limits.

        Returns:
            (within_limits, reason) tuple
        """
        if self.account_balance_usd <= 0:
            # If no balance set, skip exposure checks
            return True, "OK (no balance set)"

        # Calculate current exposures
        total_exposure = self.get_total_exposure()
        symbol_exposure = self.get_symbol_exposure(symbol)

        # Check total exposure limit
        max_total = self.account_balance_usd * (self.max_exposure_pct / 100)
        if total_exposure + new_notional > max_total:
            return False, f"Total exposure ${total_exposure + new_notional:.2f} > max ${max_total:.2f} ({self.max_exposure_pct}%)"

        # Check per-symbol exposure limit
        max_symbol = self.account_balance_usd * (self.per_symbol_max_notional_pct / 100)
        if symbol_exposure + new_notional > max_symbol:
            return False, f"{symbol} exposure ${symbol_exposure + new_notional:.2f} > max ${max_symbol:.2f} ({self.per_symbol_max_notional_pct}%)"

        return True, "OK"

    def can_open_position_for_symbol(self, symbol: str) -> tuple[bool, str]:
        """
        Check if we can open a position for a specific symbol.

        Returns:
            (can_open, reason) tuple
        """
        # Check total position limit
        if self.current_positions >= self.max_positions:
            return False, f"Max total positions ({self.max_positions}) reached"

        # Check per-symbol limit
        symbol_positions = self.get_positions_for_symbol(symbol)
        if symbol_positions >= self.max_positions_per_symbol:
            return False, f"Max positions for {symbol} ({self.max_positions_per_symbol}) reached"

        # Check cooldown
        if self.is_on_cooldown(symbol):
            return False, f"{symbol} is on trade cooldown"

        # Check exposure limits
        within_limits, reason = self.check_exposure_limits(symbol, self.default_size_usd)
        if not within_limits:
            return False, reason

        return True, "OK"
