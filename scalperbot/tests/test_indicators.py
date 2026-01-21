"""
Tests for indicators module, specifically volume spike threshold.
"""

import pytest
from scalperbot.core.indicators import Indicators
from scalperbot.mexc.models import Kline


def make_klines(volumes: list, base_price: float = 100.0) -> list:
    """Create mock klines with given volumes."""
    klines = []
    for i, vol in enumerate(volumes):
        klines.append(Kline(
            timestamp=1000000 + i * 60000,
            open=base_price,
            high=base_price * 1.01,
            low=base_price * 0.99,
            close=base_price * 1.005,  # Slight uptrend
            volume=vol,
            close_time=1000000 + (i + 1) * 60000 - 1,
            quote_volume=vol * base_price
        ))
    return klines


class TestVolumeSpikeThreshold:
    """Test volume spike confirmation threshold (1.7x)."""

    def setup_method(self):
        self.indicators = Indicators()

    def test_volume_spike_at_169x_should_not_confirm(self):
        """Volume at 1.69x median should NOT confirm as spike."""
        # Create 30 candles with volume=100, then 2 candles with volume=169
        # This gives ratio = 169/100 = 1.69x (below 1.7 threshold)
        base_volumes = [100.0] * 28
        spike_volumes = [169.0, 169.0]  # Both elevated but ratio = 1.69x
        klines = make_klines(base_volumes + spike_volumes)

        result = self.indicators.calculate("TEST", klines)

        # 1.69x is below 1.7 threshold, so should NOT be confirmed
        assert result.volume_spike_confirmed is False, \
            f"Expected False at 1.69x, got True (ratio={result.volume_spike_ratio:.2f})"

    def test_volume_spike_at_170x_should_confirm(self):
        """Volume at 1.70x median should confirm as spike."""
        # Create 30 candles with volume=100, then 2 candles with volume=170
        # This gives ratio = 170/100 = 1.70x (at threshold)
        base_volumes = [100.0] * 28
        spike_volumes = [170.0, 170.0]  # Both elevated, ratio = 1.70x
        klines = make_klines(base_volumes + spike_volumes)

        result = self.indicators.calculate("TEST", klines)

        # 1.70x meets 1.7 threshold, AND both candles are >= 1.5x
        assert result.volume_spike_confirmed is True, \
            f"Expected True at 1.70x, got False (ratio={result.volume_spike_ratio:.2f})"

    def test_volume_spike_requires_both_candles_elevated(self):
        """Even with high ratio, both candles must be >= 1.5x median."""
        # High average but one candle is low
        base_volumes = [100.0] * 28
        # Candle 1: 200 (2.0x), Candle 2: 140 (1.4x < 1.5x threshold)
        # Average = 170 (1.7x) but candle 2 fails persistence check
        spike_volumes = [140.0, 200.0]
        klines = make_klines(base_volumes + spike_volumes)

        result = self.indicators.calculate("TEST", klines)

        # Ratio is 1.7x but one candle is below 1.5x persistence threshold
        assert result.volume_spike_confirmed is False, \
            "Volume spike should fail when one candle is below 1.5x"

    def test_volume_spike_at_200x_should_confirm(self):
        """Volume at 2.0x median should definitely confirm."""
        base_volumes = [100.0] * 28
        spike_volumes = [200.0, 200.0]
        klines = make_klines(base_volumes + spike_volumes)

        result = self.indicators.calculate("TEST", klines)

        assert result.volume_spike_confirmed is True
        assert result.volume_spike_ratio >= 2.0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
