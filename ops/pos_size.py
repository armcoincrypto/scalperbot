"""
Position Sizing Module
Calculates position sizes based on available capital and risk parameters
"""
import logging
from typing import Optional, Dict, Any
from config import settings

logger = logging.getLogger(__name__)


class PositionSizer:
    """
    Calculates position sizes for trades
    - Fixed notional sizing with per-symbol minimums
    - Avoids failed orders for expensive coins
    - Max position limits
    """

    def __init__(self, default_size_usd: float = None, max_positions: int = None):
        self.default_size_usd = default_size_usd or settings.position_size_usd
        self.max_positions = max_positions or settings.max_positions
        self.current_positions = 0

        # Per-symbol minimum notional to avoid failed orders
        self.per_symbol_min_notional = getattr(settings, 'per_symbol_min_notional', {
            "BTC/USDT": 200.0,
            "ETH/USDT": 100.0,
            "default": 25.0
        })

    def get_min_notional(self, symbol: str) -> float:
        """Get minimum notional for a symbol"""
        if symbol in self.per_symbol_min_notional:
            return self.per_symbol_min_notional[symbol]
        return self.per_symbol_min_notional.get('default', self.default_size_usd)

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

        # Get minimum notional for this symbol
        min_notional = self.get_min_notional(symbol)

        # Use larger of default size and minimum notional
        notional_usd = max(self.default_size_usd, min_notional)

        # If balance provided, limit to available capital
        if balance_usd is not None:
            if balance_usd < notional_usd:
                # Check if we can at least do the minimum
                if balance_usd >= min_notional:
                    notional_usd = min_notional
                    logger.info(f"Reduced position to minimum notional: ${notional_usd:.2f}")
                else:
                    logger.warning(f"⚠️ Insufficient balance: ${balance_usd:.2f} < ${min_notional:.2f} (min for {symbol})")
                    return {
                        'quantity': 0,
                        'notional_usd': 0,
                        'can_trade': False,
                        'reason': f'Insufficient balance (need ${min_notional:.2f} min for {symbol})'
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
