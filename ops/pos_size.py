"""
Position Sizing Module
Calculates position sizes based on available capital and risk parameters
Uses database for position tracking instead of simple counter
"""
import logging
from typing import Optional, Dict, Any
from config import settings

logger = logging.getLogger(__name__)


class PositionSizer:
    """
    Calculates position sizes for trades
    - Fixed notional sizing with per-symbol minimums
    - Database-backed position tracking
    - Max position limits
    """

    def __init__(self, db=None, default_size_usd: float = None, max_positions: int = None):
        self.db = db  # TradeDB instance for position tracking
        self.default_size_usd = default_size_usd or settings.position_size_usd
        self.max_positions = max_positions or settings.max_positions

    def get_current_position_count(self) -> int:
        """Get current position count from database"""
        if self.db:
            return self.db.get_position_count()
        return 0

    def has_position(self, symbol: str) -> bool:
        """Check if we already have an open position for this symbol"""
        if self.db:
            position = self.db.get_position_by_symbol(symbol)
            return position is not None
        return False

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
        current_positions = self.get_current_position_count()

        # Check if we can open more positions
        if current_positions >= self.max_positions:
            logger.warning(f"⚠️ Max positions ({self.max_positions}) reached, cannot open new position")
            return {
                'quantity': 0,
                'notional_usd': 0,
                'can_trade': False,
                'reason': 'Max positions reached'
            }

        # Check if we already have a position for this symbol
        if self.has_position(symbol):
            logger.warning(f"⚠️ Already have an open position for {symbol}")
            return {
                'quantity': 0,
                'notional_usd': 0,
                'can_trade': False,
                'reason': f'Already have position in {symbol}'
            }

        # Get per-symbol minimum notional (or default)
        min_notional = settings.get_min_notional(symbol)
        notional_usd = max(self.default_size_usd, min_notional)

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

    def can_open_position(self, symbol: str = None) -> bool:
        """Check if we can open a new position"""
        current_positions = self.get_current_position_count()

        if current_positions >= self.max_positions:
            return False

        if symbol and self.has_position(symbol):
            return False

        return True

    def get_status(self) -> Dict[str, Any]:
        """Get position sizer status"""
        current_positions = self.get_current_position_count()
        return {
            'current_positions': current_positions,
            'max_positions': self.max_positions,
            'can_open': current_positions < self.max_positions,
            'default_size_usd': self.default_size_usd
        }
