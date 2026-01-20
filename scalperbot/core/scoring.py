"""
Signal scoring system.
Combines multiple factors into a single score.
"""

from dataclasses import dataclass
from typing import Optional

from scalperbot.core.indicators import IndicatorResult
from scalperbot.config import settings
from scalperbot.log import get_logger

logger = get_logger(__name__)


@dataclass
class SignalScore:
    """Detailed signal score breakdown."""
    symbol: str
    total_score: float
    momentum_score: float
    volume_score: float
    rsi_score: float
    external_signal: float
    is_buy_signal: bool
    is_sell_signal: bool
    reasons: list
    # NEW: Rejection reasons for diagnostics
    rejection_reason: str = ""
    passed_filters: int = 0  # Count of filters passed (max 5)


class SignalScorer:
    """
    Calculate trading signal scores based on multiple factors.

    Score components:
    - Momentum: Positive for upward moves
    - Volume: Bonus for volume spikes
    - RSI: Penalty for overbought, bonus for oversold
    - External signal: Weight from Telegram /signal command
    """

    def __init__(self):
        # Scoring weights
        self.momentum_weight = 1.0
        self.volume_weight = 0.5
        self.rsi_weight = 0.5
        self.external_weight = 1.0

        # RSI thresholds
        self.rsi_oversold = 30
        self.rsi_overbought = 70

    def calculate_score(
        self,
        indicators: IndicatorResult,
        external_signal: float = 0.0,
        spread_pct: float = 0.0
    ) -> SignalScore:
        """
        Calculate composite signal score.

        Args:
            indicators: Calculated indicator values
            external_signal: External signal weight (from Telegram)
            spread_pct: Current spread percentage

        Returns:
            SignalScore with breakdown and signals
        """
        reasons = []

        # === Momentum Score ===
        # Use 2m momentum for quick scalps
        momentum_score = 0.0
        if indicators.momentum_2m > 0.3:
            momentum_score = min(indicators.momentum_2m, 2.0)
            reasons.append(f"Momentum +{indicators.momentum_2m:.2f}%")
        elif indicators.momentum_2m < -0.3:
            momentum_score = max(indicators.momentum_2m, -2.0)
            reasons.append(f"Momentum {indicators.momentum_2m:.2f}%")

        # === Volume Score ===
        volume_score = 0.0
        if indicators.volume_ratio > 2.0:
            volume_score = 1.0
            reasons.append(f"Volume spike {indicators.volume_ratio:.1f}x")
        elif indicators.volume_ratio > 1.5:
            volume_score = 0.5
            reasons.append(f"High volume {indicators.volume_ratio:.1f}x")
        elif indicators.volume_ratio < 0.5:
            volume_score = -0.5
            reasons.append("Low volume")

        # === RSI Score ===
        rsi_score = 0.0
        if indicators.rsi < self.rsi_oversold:
            rsi_score = 1.0
            reasons.append(f"RSI oversold ({indicators.rsi:.0f})")
        elif indicators.rsi > self.rsi_overbought:
            rsi_score = -1.0
            reasons.append(f"RSI overbought ({indicators.rsi:.0f})")

        # === External Signal ===
        if external_signal != 0:
            reasons.append(f"External signal: {external_signal:+.1f}")

        # === Calculate Total Score ===
        total_score = (
            momentum_score * self.momentum_weight +
            volume_score * self.volume_weight +
            rsi_score * self.rsi_weight +
            external_signal * self.external_weight
        )

        # === Entry Confirmation Checks (ENHANCED) ===
        # Track filter passes for diagnostics
        passed_filters = 0
        rejection_reason = ""

        # Filter 1: Close-confirmed breakout (not just wick)
        has_close_breakout = indicators.is_close_confirmed_breakout
        if has_close_breakout:
            passed_filters += 1
            reasons.append(f"Close breakout above {indicators.recent_high:.6f}")

        # Filter 2: Volume spike must persist for 2 candles
        has_volume_spike = indicators.volume_spike_confirmed
        if has_volume_spike:
            passed_filters += 1
            reasons.append(f"Vol spike {indicators.volume_spike_ratio:.1f}x (sustained)")

        # Filter 3: Not a late entry (didn't already move too much)
        is_not_late = not indicators.is_late_entry
        if is_not_late:
            passed_filters += 1
        else:
            reasons.append(f"Late entry ({indicators.late_move_pct:.1f}% in 10m)")

        # Filter 4: Trend filter (price above EMA)
        has_trend = indicators.is_trend_up
        if has_trend:
            passed_filters += 1
            reasons.append("Above EMA10")

        # Filter 5: Sustained momentum (both 2m and 5m positive)
        has_sustained_momentum = indicators.momentum_sustained
        if has_sustained_momentum:
            passed_filters += 1
            reasons.append("Sustained momentum")

        # === Determine Signals (STRICT) ===
        # Require ALL filters to pass for a buy signal
        is_buy = (
            total_score >= settings.buy_score_min and
            indicators.momentum_2m >= settings.buy_pct_trigger and
            indicators.rsi < 70 and  # Don't buy overbought
            has_close_breakout and  # Close-confirmed breakout
            has_volume_spike and  # Volume spike persists
            is_not_late and  # Not entering too late
            has_trend and  # Price above EMA (trend up)
            has_sustained_momentum  # Both 2m and 5m momentum positive
        )

        # Build rejection reason if not a buy
        if not is_buy and total_score >= settings.buy_score_min:
            rejections = []
            if not has_close_breakout:
                rejections.append("no_close_breakout")
            if not has_volume_spike:
                rejections.append("no_vol_spike")
            if not is_not_late:
                rejections.append("late_entry")
            if not has_trend:
                rejections.append("no_trend")
            if not has_sustained_momentum:
                rejections.append("weak_momentum")
            if indicators.rsi >= 70:
                rejections.append("overbought")
            rejection_reason = ",".join(rejections)

        is_sell = (
            total_score <= settings.sell_score_max or
            indicators.momentum_2m <= settings.sell_pct_trigger
        )

        return SignalScore(
            symbol=indicators.symbol,
            total_score=round(total_score, 2),
            momentum_score=round(momentum_score, 2),
            volume_score=round(volume_score, 2),
            rsi_score=round(rsi_score, 2),
            external_signal=external_signal,
            is_buy_signal=is_buy,
            is_sell_signal=is_sell,
            reasons=reasons,
            rejection_reason=rejection_reason,
            passed_filters=passed_filters
        )

    def should_exit(
        self,
        entry_price: float,
        current_price: float,
        stop_loss: float,
        take_profit: float,
        trailing_stop: Optional[float],
        indicators: IndicatorResult
    ) -> tuple[bool, str]:
        """
        Check if position should be exited.

        Returns:
            (should_exit, reason)
        """
        # Stop loss hit
        if current_price <= stop_loss:
            return True, "STOP_LOSS"

        # Take profit hit
        if current_price >= take_profit:
            return True, "TAKE_PROFIT"

        # Trailing stop hit
        if trailing_stop and current_price <= trailing_stop:
            return True, "TRAILING_STOP"

        # Momentum reversal (optional)
        if indicators.momentum_5m < -1.0 and indicators.rsi > 60:
            pnl_pct = ((current_price - entry_price) / entry_price) * 100
            if pnl_pct > 0.5:  # Only if in profit
                return True, "MOMENTUM_REVERSAL"

        return False, ""
