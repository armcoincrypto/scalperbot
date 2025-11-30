"""
Dynamic Thresholds Module
Adaptive strategy parameters based on market conditions and time

Features:
- Session-based thresholds (Asian/EU/US)
- Volatility regime detection
- Time-since-signal relaxation
- Percentile-based BB expansion thresholds
"""
import logging
from datetime import datetime, timezone, timedelta
from typing import Dict, Optional, Tuple
import pandas as pd
import numpy as np

logger = logging.getLogger(__name__)


class DynamicThresholds:
    """
    Manages adaptive thresholds for the GREEN filter system

    Adjusts BB expansion threshold based on:
    1. Trading session (Asian/EU/US)
    2. Time since last signal (relaxes over time)
    3. Historical volatility percentile
    """

    # Session definitions (UTC hours)
    SESSIONS = {
        'asian': (0, 8),      # 00:00-08:00 UTC (lowest volatility)
        'european': (8, 14),  # 08:00-14:00 UTC (medium volatility)
        'american': (14, 22), # 14:00-22:00 UTC (highest volatility)
        'overnight': (22, 24) # 22:00-00:00 UTC (low volatility)
    }

    # Base BB expansion percentile thresholds by session
    SESSION_THRESHOLDS = {
        'asian': 5,       # Very relaxed (5th percentile) - low vol session
        'european': 15,   # Medium (15th percentile)
        'american': 25,   # Stricter (25th percentile) - high vol session
        'overnight': 8    # Relaxed (8th percentile)
    }

    def __init__(
        self,
        no_signal_relax_minutes: int = 30,
        max_relax_factor: float = 0.5,  # Can reduce threshold by up to 50%
        enable_session_adjustment: bool = True,
        enable_time_relaxation: bool = True
    ):
        self.no_signal_relax_minutes = no_signal_relax_minutes
        self.max_relax_factor = max_relax_factor
        self.enable_session_adjustment = enable_session_adjustment
        self.enable_time_relaxation = enable_time_relaxation

        # Track last signal time per symbol
        self._last_signal_time: Dict[str, datetime] = {}

        # Cache for BB width percentiles
        self._bb_width_history: Dict[str, list] = {}
        self._max_history_size = 500  # Keep last 500 BB widths

        logger.info(f"DynamicThresholds initialized: relax_minutes={no_signal_relax_minutes}, max_relax={max_relax_factor}")

    def get_current_session(self) -> str:
        """Determine current trading session based on UTC time"""
        now_utc = datetime.now(timezone.utc)
        hour = now_utc.hour

        for session, (start, end) in self.SESSIONS.items():
            if start <= hour < end:
                return session

        return 'overnight'  # Default fallback

    def get_session_threshold(self) -> int:
        """Get BB expansion threshold for current session"""
        if not self.enable_session_adjustment:
            return 15  # Default middle value

        session = self.get_current_session()
        threshold = self.SESSION_THRESHOLDS.get(session, 15)

        logger.debug(f"Current session: {session}, base threshold: {threshold}th percentile")
        return threshold

    def get_minutes_since_last_signal(self, symbol: str) -> float:
        """Get minutes since last signal for a symbol"""
        if symbol not in self._last_signal_time:
            return float('inf')  # No previous signal

        elapsed = datetime.now(timezone.utc) - self._last_signal_time[symbol]
        return elapsed.total_seconds() / 60

    def get_time_relaxation_factor(self, symbol: str) -> float:
        """
        Calculate relaxation factor based on time since last signal

        Returns a multiplier (1.0 = no relaxation, lower = more relaxed)
        """
        if not self.enable_time_relaxation:
            return 1.0

        minutes = self.get_minutes_since_last_signal(symbol)

        if minutes < self.no_signal_relax_minutes:
            # No relaxation within the cooldown period
            return 1.0

        # Linear relaxation: starts at 1.0, decreases to (1 - max_relax_factor)
        # over the next 60 minutes after cooldown
        excess_minutes = minutes - self.no_signal_relax_minutes
        relax_progress = min(excess_minutes / 60.0, 1.0)  # Cap at 60 minutes

        factor = 1.0 - (self.max_relax_factor * relax_progress)

        logger.debug(f"{symbol}: {minutes:.0f}min since signal, relax_factor={factor:.2f}")
        return factor

    def record_signal(self, symbol: str):
        """Record that a signal was generated (resets relaxation timer)"""
        self._last_signal_time[symbol] = datetime.now(timezone.utc)
        logger.info(f"Signal recorded for {symbol}, relaxation timer reset")

    def update_bb_width_history(self, symbol: str, bb_width: float):
        """Add BB width to history for percentile calculations"""
        if symbol not in self._bb_width_history:
            self._bb_width_history[symbol] = []

        self._bb_width_history[symbol].append(bb_width)

        # Keep only recent history
        if len(self._bb_width_history[symbol]) > self._max_history_size:
            self._bb_width_history[symbol] = self._bb_width_history[symbol][-self._max_history_size:]

    def get_bb_width_percentile(self, symbol: str, bb_width: float) -> float:
        """
        Calculate what percentile the current BB width is at

        Returns 0-100 (100 = widest ever seen)
        """
        if symbol not in self._bb_width_history or len(self._bb_width_history[symbol]) < 20:
            return 50.0  # Default to middle if not enough history

        history = self._bb_width_history[symbol]
        percentile = (sum(1 for x in history if x < bb_width) / len(history)) * 100

        return percentile

    def check_bb_expansion_dynamic(
        self,
        symbol: str,
        current_bb_width: float,
        prev_bb_width: float,
        df: Optional[pd.DataFrame] = None
    ) -> Tuple[bool, str, Dict]:
        """
        Dynamic BB expansion check

        Returns:
            (passed, message, details_dict)
        """
        # Update history
        self.update_bb_width_history(symbol, current_bb_width)

        # Get current percentile
        current_percentile = self.get_bb_width_percentile(symbol, current_bb_width)

        # Get session-based threshold
        base_threshold = self.get_session_threshold()

        # Apply time relaxation
        relax_factor = self.get_time_relaxation_factor(symbol)
        adjusted_threshold = base_threshold * relax_factor

        # Check if expanding
        is_expanding = current_bb_width > prev_bb_width

        # Check if above threshold percentile
        above_threshold = current_percentile >= adjusted_threshold

        # For a signal, we want EITHER:
        # 1. BB is expanding AND above threshold, OR
        # 2. BB is significantly above threshold (50th percentile+) even if contracting
        passed = (is_expanding and above_threshold) or (current_percentile >= 50)

        session = self.get_current_session()
        minutes_since = self.get_minutes_since_last_signal(symbol)

        details = {
            'session': session,
            'base_threshold': base_threshold,
            'adjusted_threshold': adjusted_threshold,
            'current_percentile': current_percentile,
            'is_expanding': is_expanding,
            'above_threshold': above_threshold,
            'relax_factor': relax_factor,
            'minutes_since_signal': minutes_since if minutes_since != float('inf') else None
        }

        status = "✅" if passed else "❌"
        msg = (
            f"GREEN 2 [DYNAMIC]: BB_pctl={current_percentile:.0f}%, "
            f"threshold={adjusted_threshold:.0f}% ({session}), "
            f"expanding={is_expanding}, passed={status}"
        )

        return passed, msg, details

    def get_adaptive_tp_sl(self, symbol: str, base_tp: float, base_sl: float) -> Tuple[float, float]:
        """
        Get adaptive TP/SL based on session volatility

        Higher volatility sessions can have wider TP/SL
        """
        session = self.get_current_session()

        # Volatility multipliers by session
        vol_multipliers = {
            'asian': 0.8,       # Tighter TP/SL
            'european': 1.0,    # Normal
            'american': 1.2,    # Wider TP/SL
            'overnight': 0.9
        }

        multiplier = vol_multipliers.get(session, 1.0)

        adaptive_tp = base_tp * multiplier
        adaptive_sl = base_sl * multiplier

        logger.debug(f"{symbol}: Session={session}, TP={adaptive_tp:.2f}%, SL={adaptive_sl:.2f}%")

        return adaptive_tp, adaptive_sl

    def summary(self) -> str:
        """Get summary of current thresholds"""
        session = self.get_current_session()
        threshold = self.get_session_threshold()

        lines = [
            f"📊 Dynamic Thresholds:",
            f"  Session: {session.upper()}",
            f"  Base BB threshold: {threshold}th percentile",
            f"  Session adjustment: {'ON' if self.enable_session_adjustment else 'OFF'}",
            f"  Time relaxation: {'ON' if self.enable_time_relaxation else 'OFF'}"
        ]

        if self._last_signal_time:
            lines.append("  Last signals:")
            for sym, t in self._last_signal_time.items():
                mins = self.get_minutes_since_last_signal(sym)
                lines.append(f"    {sym}: {mins:.0f}min ago")

        return "\n".join(lines)
