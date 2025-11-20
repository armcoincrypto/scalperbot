"""
Position Sizing Module
Calculates position sizes based on available capital and risk parameters
"""
import logging
from typing import Optional, Dict, Any
from scalperbot.config import settings

logger = logging.getLogger(__name__)


class PositionSizer:
    """
    Calculates position sizes for trades
    - Fixed notional sizing
    - Risk-based sizing (future)
    - Max position limits
    """

    def __init__(self, default_size_usd: float = None, max_positions: int = None):
        self.default_size_usd = default_size_usd or settings.position_size_usd
        self.max_positions = max_positions or settings.max_positions
        self.current_positions = 0

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
        """Check if we can open a new position"""
        return self.current_positions < self.max_positions
