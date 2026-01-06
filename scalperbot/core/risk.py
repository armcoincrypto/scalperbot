"""
Risk management for position sizing and stop losses.
"""

from dataclasses import dataclass
from typing import Optional

from scalperbot.config import settings
from scalperbot.core.indicators import IndicatorResult
from scalperbot.log import get_logger

logger = get_logger(__name__)


@dataclass
class RiskParameters:
    """Calculated risk parameters for a trade."""
    stop_loss_price: float
    stop_loss_pct: float
    take_profit_price: float
    take_profit_pct: float
    position_size_usdt: float
    quantity: float
    risk_reward_ratio: float


class RiskManager:
    """
    Dynamic risk management.

    Features:
    - Dynamic stop loss: max(BASE_SL, 2*spread, 0.8*ATR)
    - Position sizing based on risk
    - Trailing stop calculation
    """

    def __init__(self):
        self.base_sl_pct = settings.base_sl_pct
        self.take_profit_pct = settings.take_profit_pct
        self.position_size = settings.position_size_usdt
        self.trailing_enabled = settings.trailing_enabled
        self.trailing_start_pct = settings.trailing_start_pct
        self.trailing_offset_pct = settings.trailing_offset_pct

    def calculate_risk_params(
        self,
        entry_price: float,
        indicators: IndicatorResult,
        spread_pct: float = 0.0
    ) -> RiskParameters:
        """
        Calculate risk parameters for a trade.

        Args:
            entry_price: Entry price
            indicators: Technical indicators
            spread_pct: Current spread %

        Returns:
            RiskParameters with SL, TP, and sizing
        """
        # Dynamic stop loss: max of multiple factors
        sl_from_base = self.base_sl_pct
        sl_from_spread = spread_pct * 2  # 2x spread
        sl_from_atr = indicators.atr_pct * 0.8 if indicators.atr_pct > 0 else 0

        stop_loss_pct = max(sl_from_base, sl_from_spread, sl_from_atr, 1.0)
        stop_loss_price = entry_price * (1 - stop_loss_pct / 100)

        # Take profit
        take_profit_price = entry_price * (1 + self.take_profit_pct / 100)

        # Calculate quantity
        quantity = self.position_size / entry_price

        # Risk/reward ratio
        risk = entry_price - stop_loss_price
        reward = take_profit_price - entry_price
        rr_ratio = reward / risk if risk > 0 else 0

        logger.debug(
            f"Risk params: SL={stop_loss_pct:.2f}% (base={sl_from_base:.2f}%, "
            f"spread={sl_from_spread:.2f}%, atr={sl_from_atr:.2f}%), "
            f"TP={self.take_profit_pct:.2f}%, R:R={rr_ratio:.2f}"
        )

        return RiskParameters(
            stop_loss_price=stop_loss_price,
            stop_loss_pct=stop_loss_pct,
            take_profit_price=take_profit_price,
            take_profit_pct=self.take_profit_pct,
            position_size_usdt=self.position_size,
            quantity=quantity,
            risk_reward_ratio=rr_ratio
        )

    def calculate_trailing_stop(
        self,
        entry_price: float,
        current_price: float,
        peak_price: float,
        current_trailing: Optional[float] = None
    ) -> Optional[float]:
        """
        Calculate trailing stop price.

        Args:
            entry_price: Original entry price
            current_price: Current market price
            peak_price: Highest price since entry
            current_trailing: Current trailing stop (if any)

        Returns:
            New trailing stop price, or None if not triggered
        """
        if not self.trailing_enabled:
            return None

        # Calculate profit from entry
        profit_pct = ((current_price - entry_price) / entry_price) * 100

        # Don't start trailing until minimum profit reached
        if profit_pct < self.trailing_start_pct:
            return current_trailing

        # Calculate new trailing stop
        new_trailing = peak_price * (1 - self.trailing_offset_pct / 100)

        # Only move trailing stop up, never down
        if current_trailing and new_trailing <= current_trailing:
            return current_trailing

        logger.debug(
            f"Trailing stop updated: {current_trailing} -> {new_trailing:.6f} "
            f"(profit: {profit_pct:.2f}%, peak: {peak_price:.6f})"
        )

        return new_trailing

    def should_reduce_size(
        self,
        open_positions: int,
        daily_pnl: float,
        consecutive_losses: int = 0
    ) -> float:
        """
        Calculate position size multiplier based on risk state.

        Args:
            open_positions: Number of open positions
            daily_pnl: Today's realized PnL
            consecutive_losses: Number of consecutive losing trades

        Returns:
            Multiplier for position size (0.5 to 1.0)
        """
        multiplier = 1.0

        # Reduce size if too many open positions
        if open_positions >= settings.max_open_positions - 1:
            multiplier *= 0.7

        # Reduce size if losing day
        if daily_pnl < -self.position_size:
            multiplier *= 0.5

        # Reduce size after consecutive losses
        if consecutive_losses >= 3:
            multiplier *= 0.5

        return max(multiplier, 0.25)  # Never go below 25%
