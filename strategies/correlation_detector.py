"""
Multi-Asset Correlation Detector

Detects when multiple pairs trigger signals simultaneously, indicating
market-wide breakouts and higher conviction setups.

Usage:
    detector = CorrelationDetector(window_seconds=60)
    is_correlated, pair_count = detector.check_correlation(symbol, timestamp)
    multiplier = detector.get_size_multiplier(pair_count)
"""
import time
from typing import Dict, Tuple, List
from dataclasses import dataclass, field
import logging

logger = logging.getLogger(__name__)


@dataclass
class SignalEvent:
    """Represents a signal event"""
    symbol: str
    timestamp: float
    price: float
    mode: str  # STRICT, MODERATE, PERMISSIVE


class CorrelationDetector:
    """
    Detects multi-asset correlation in signals

    Strategy:
    - Track signals within a time window (default 60 seconds)
    - Flag when 2+ pairs trigger simultaneously
    - Suggest position size multipliers based on correlation strength
    """

    def __init__(self, window_seconds: int = 60):
        """
        Initialize correlation detector

        Args:
            window_seconds: Time window to consider signals correlated
        """
        self.window_seconds = window_seconds
        self.recent_signals: List[SignalEvent] = []

        # Size multipliers based on correlation strength
        self.multipliers = {
            1: 1.0,    # Single signal
            2: 1.25,   # 2 correlated signals
            3: 1.5,    # 3+ correlated signals (cap here)
        }

        logger.info(f"🔗 Correlation Detector initialized (window: {window_seconds}s)")

    def add_signal(self, symbol: str, price: float, mode: str = "UNKNOWN") -> Tuple[bool, int, List[str]]:
        """
        Add a new signal and check for correlation

        Args:
            symbol: Trading pair
            price: Signal price
            mode: Policy mode (STRICT/MODERATE/PERMISSIVE)

        Returns:
            (is_correlated, pair_count, correlated_symbols)
        """
        current_time = time.time()

        # Add new signal
        signal = SignalEvent(
            symbol=symbol,
            timestamp=current_time,
            price=price,
            mode=mode
        )
        self.recent_signals.append(signal)

        # Clean up old signals outside the window
        self._cleanup_old_signals(current_time)

        # Check for correlation
        correlated_symbols = self._get_correlated_symbols(current_time)
        is_correlated = len(correlated_symbols) > 1

        if is_correlated:
            logger.warning(f"🔗 CORRELATION DETECTED: {len(correlated_symbols)} pairs triggered within {self.window_seconds}s")
            logger.warning(f"   Pairs: {', '.join(correlated_symbols)}")

        return is_correlated, len(correlated_symbols), correlated_symbols

    def get_size_multiplier(self, pair_count: int) -> float:
        """
        Get position size multiplier based on correlation strength

        Args:
            pair_count: Number of correlated pairs

        Returns:
            Size multiplier (1.0 to 1.5)
        """
        # Cap at 3+ pairs
        count = min(pair_count, 3)
        return self.multipliers.get(count, 1.0)

    def _cleanup_old_signals(self, current_time: float):
        """Remove signals outside the correlation window"""
        cutoff_time = current_time - self.window_seconds
        self.recent_signals = [s for s in self.recent_signals if s.timestamp >= cutoff_time]

    def _get_correlated_symbols(self, current_time: float) -> List[str]:
        """Get list of symbols that triggered within the window"""
        cutoff_time = current_time - self.window_seconds
        return [s.symbol for s in self.recent_signals if s.timestamp >= cutoff_time]

    def get_recent_signals(self) -> List[SignalEvent]:
        """Get all signals in the current correlation window"""
        current_time = time.time()
        self._cleanup_old_signals(current_time)
        return self.recent_signals.copy()

    def print_status(self):
        """Print current correlation status"""
        signals = self.get_recent_signals()

        if not signals:
            logger.info("🔗 No recent signals in correlation window")
            return

        logger.info("\n" + "="*70)
        logger.info("🔗 CORRELATION STATUS")
        logger.info("="*70)
        logger.info(f"Signals in window ({self.window_seconds}s): {len(signals)}")

        for sig in signals:
            age = time.time() - sig.timestamp
            logger.info(f"  {sig.symbol}: {age:.0f}s ago | Mode: {sig.mode} | Price: {sig.price:.4f}")

        if len(signals) > 1:
            multiplier = self.get_size_multiplier(len(signals))
            logger.info(f"\n📊 Suggested size multiplier: {multiplier}x")

        logger.info("="*70 + "\n")
