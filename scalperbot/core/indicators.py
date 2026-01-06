"""
Technical indicators for signal generation.
"""

from typing import List, Optional
from dataclasses import dataclass

from scalperbot.mexc.models import Kline
from scalperbot.log import get_logger

logger = get_logger(__name__)


@dataclass
class IndicatorResult:
    """Container for calculated indicators."""
    symbol: str
    momentum_2m: float  # 2-minute momentum %
    momentum_5m: float  # 5-minute momentum %
    volume_ratio: float  # Current volume / avg volume
    rsi: float  # Relative Strength Index
    atr: float  # Average True Range
    atr_pct: float  # ATR as % of price
    last_price: float
    avg_volume: float
    is_valid: bool = True


class Indicators:
    """
    Technical indicator calculator.
    Works with MEXC kline data.
    """

    def __init__(self, rsi_period: int = 14, atr_period: int = 14):
        self.rsi_period = rsi_period
        self.atr_period = atr_period

    def calculate(self, symbol: str, klines: List[Kline]) -> IndicatorResult:
        """
        Calculate all indicators from kline data.

        Args:
            symbol: Trading symbol
            klines: List of Kline objects (oldest first)

        Returns:
            IndicatorResult with all calculated values
        """
        if len(klines) < max(self.rsi_period, self.atr_period, 6):
            return IndicatorResult(
                symbol=symbol,
                momentum_2m=0,
                momentum_5m=0,
                volume_ratio=0,
                rsi=50,
                atr=0,
                atr_pct=0,
                last_price=0,
                avg_volume=0,
                is_valid=False
            )

        closes = [k.close for k in klines]
        highs = [k.high for k in klines]
        lows = [k.low for k in klines]
        volumes = [k.volume for k in klines]

        last_price = closes[-1]

        # Momentum calculations
        momentum_2m = self._calculate_momentum(closes, 2)
        momentum_5m = self._calculate_momentum(closes, 5)

        # Volume ratio
        avg_volume = sum(volumes[:-1]) / len(volumes[:-1]) if len(volumes) > 1 else 1
        volume_ratio = volumes[-1] / avg_volume if avg_volume > 0 else 0

        # RSI
        rsi = self._calculate_rsi(closes)

        # ATR
        atr = self._calculate_atr(highs, lows, closes)
        atr_pct = (atr / last_price * 100) if last_price > 0 else 0

        return IndicatorResult(
            symbol=symbol,
            momentum_2m=momentum_2m,
            momentum_5m=momentum_5m,
            volume_ratio=volume_ratio,
            rsi=rsi,
            atr=atr,
            atr_pct=atr_pct,
            last_price=last_price,
            avg_volume=avg_volume,
            is_valid=True
        )

    def _calculate_momentum(self, closes: List[float], periods: int) -> float:
        """Calculate price change % over N periods."""
        if len(closes) < periods + 1:
            return 0

        current = closes[-1]
        past = closes[-(periods + 1)]

        if past == 0:
            return 0

        return ((current - past) / past) * 100

    def _calculate_rsi(self, closes: List[float]) -> float:
        """Calculate RSI using Wilder's smoothing."""
        if len(closes) < self.rsi_period + 1:
            return 50

        # Calculate price changes
        changes = [closes[i] - closes[i-1] for i in range(1, len(closes))]

        # Separate gains and losses
        gains = [max(c, 0) for c in changes]
        losses = [abs(min(c, 0)) for c in changes]

        # Initial averages
        avg_gain = sum(gains[:self.rsi_period]) / self.rsi_period
        avg_loss = sum(losses[:self.rsi_period]) / self.rsi_period

        # Wilder's smoothing for remaining periods
        for i in range(self.rsi_period, len(gains)):
            avg_gain = (avg_gain * (self.rsi_period - 1) + gains[i]) / self.rsi_period
            avg_loss = (avg_loss * (self.rsi_period - 1) + losses[i]) / self.rsi_period

        if avg_loss == 0:
            return 100

        rs = avg_gain / avg_loss
        rsi = 100 - (100 / (1 + rs))

        return rsi

    def _calculate_atr(
        self,
        highs: List[float],
        lows: List[float],
        closes: List[float]
    ) -> float:
        """Calculate Average True Range."""
        if len(closes) < self.atr_period + 1:
            return 0

        true_ranges = []
        for i in range(1, len(closes)):
            tr = max(
                highs[i] - lows[i],
                abs(highs[i] - closes[i-1]),
                abs(lows[i] - closes[i-1])
            )
            true_ranges.append(tr)

        # Simple average of last N true ranges
        return sum(true_ranges[-self.atr_period:]) / self.atr_period

    def get_ema(self, values: List[float], period: int) -> float:
        """Calculate EMA of last value."""
        if len(values) < period:
            return values[-1] if values else 0

        multiplier = 2 / (period + 1)
        ema = sum(values[:period]) / period

        for price in values[period:]:
            ema = (price - ema) * multiplier + ema

        return ema
