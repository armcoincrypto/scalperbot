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
    # Entry confirmation filters
    is_breakout: bool = False  # Price above recent high (5-10 min)
    recent_high: float = 0.0  # The high we need to break
    volume_spike_confirmed: bool = False  # Volume >= 2x median
    volume_spike_ratio: float = 0.0  # How much above median
    # NEW: Enhanced filters for signal quality
    is_close_confirmed_breakout: bool = False  # Close (not wick) above recent high
    is_late_entry: bool = False  # Already moved too much in last 10-15 min
    late_move_pct: float = 0.0  # How much it moved in last N candles
    is_trend_up: bool = False  # Price above short EMA (trend filter)
    ema_10: float = 0.0  # 10-period EMA for trend
    momentum_sustained: bool = False  # Both 2m and 5m momentum positive


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

        # Breakout confirmation: price above last 5-10 candles high
        # Use last 7 candles (excluding current) for recent high
        lookback = min(7, len(highs) - 1)
        recent_high = max(highs[-(lookback + 1):-1]) if lookback > 0 else last_price
        is_breakout = last_price > recent_high

        # NEW: Close-confirmed breakout (not just wick)
        # Check if current candle CLOSED above recent high AND is green (close > open)
        current_candle = klines[-1]
        is_green_candle = current_candle.close > current_candle.open
        is_close_confirmed_breakout = (
            current_candle.close > recent_high and
            is_green_candle
        )

        # Volume spike confirmation: last 2 candles avg vs median of last 30
        # Require >= 2x median for confirmation
        if len(volumes) >= 3:
            recent_vol = sum(volumes[-2:]) / 2  # Avg of last 2 candles
            sorted_vols = sorted(volumes[:-2]) if len(volumes) > 2 else volumes
            median_vol = sorted_vols[len(sorted_vols) // 2] if sorted_vols else 1
            volume_spike_ratio = recent_vol / median_vol if median_vol > 0 else 0
            # Require BOTH last candles to have elevated volume (persistence)
            vol_candle_1 = volumes[-1] / median_vol if median_vol > 0 else 0
            vol_candle_2 = volumes[-2] / median_vol if median_vol > 0 else 0
            volume_spike_confirmed = (
                volume_spike_ratio >= 2.0 and
                vol_candle_1 >= 1.5 and  # Both candles must be elevated
                vol_candle_2 >= 1.5
            )
        else:
            volume_spike_ratio = 0
            volume_spike_confirmed = False

        # NEW: Late entry filter - reject if already moved too much in last 10-15 min
        # Calculate move in last 10 candles (10 minutes on 1m timeframe)
        late_lookback = min(10, len(closes) - 1)
        if late_lookback > 0:
            late_move_pct = ((closes[-1] - closes[-(late_lookback + 1)]) /
                            closes[-(late_lookback + 1)]) * 100
        else:
            late_move_pct = 0
        # Consider "late" if already moved more than 3% in last 10 min
        is_late_entry = late_move_pct > 3.0

        # NEW: Trend filter - price above 10-period EMA
        ema_10 = self.get_ema(closes, 10)
        is_trend_up = last_price > ema_10

        # NEW: Sustained momentum - both 2m AND 5m must be positive
        momentum_sustained = momentum_2m > 0.3 and momentum_5m > 0.3

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
            is_valid=True,
            is_breakout=is_breakout,
            recent_high=recent_high,
            volume_spike_confirmed=volume_spike_confirmed,
            volume_spike_ratio=volume_spike_ratio,
            # NEW fields
            is_close_confirmed_breakout=is_close_confirmed_breakout,
            is_late_entry=is_late_entry,
            late_move_pct=late_move_pct,
            is_trend_up=is_trend_up,
            ema_10=ema_10,
            momentum_sustained=momentum_sustained
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
