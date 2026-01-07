"""
Circuit Breakers - Professional safety guards for LIVE trading.

Implements:
1. Max daily loss limit → stop trading
2. Max trades per day → prevent churn
3. API error storm detection → pause on repeated errors
4. Spread shock → pause symbol temporarily
5. Kill switch → emergency stop
"""

import asyncio
from datetime import datetime, timezone, timedelta
from typing import Dict, Optional, List
from dataclasses import dataclass, field

from scalperbot.config import settings
from scalperbot.log import get_logger

logger = get_logger(__name__)


@dataclass
class CircuitState:
    """Current state of circuit breakers."""
    # Daily limits
    daily_pnl: float = 0.0
    daily_trades: int = 0
    daily_losses: int = 0
    daily_wins: int = 0

    # API health
    api_errors_1m: int = 0
    api_errors_5m: int = 0
    last_api_error: Optional[datetime] = None

    # Per-symbol spread tracking
    spread_shocks: Dict[str, int] = field(default_factory=dict)
    symbol_paused_until: Dict[str, datetime] = field(default_factory=dict)

    # Breaker states
    is_killed: bool = False
    is_daily_loss_breaker: bool = False
    is_max_trades_breaker: bool = False
    is_api_storm_breaker: bool = False

    # Reset date
    last_reset_date: str = ""


class CircuitBreaker:
    """
    Professional circuit breaker system.

    Guards against:
    - Excessive losses in a single day
    - Trading too frequently (churn)
    - API instability
    - Sudden spread widening
    """

    def __init__(self):
        self.state = CircuitState()

        # Thresholds (can be overridden from settings)
        self.max_daily_loss_pct = getattr(settings, 'max_daily_loss_pct', 2.0)  # -2% of position size
        self.max_daily_trades = getattr(settings, 'max_daily_trades', 50)
        self.max_consecutive_losses = getattr(settings, 'max_consecutive_losses', 5)
        self.api_error_threshold_1m = 5  # Errors in 1 minute
        self.api_error_threshold_5m = 20  # Errors in 5 minutes
        self.spread_shock_threshold = 3  # Times spread exceeds 2x normal

        # Calculate max daily loss in USDT
        self.max_daily_loss_usdt = (
            settings.position_size_usdt *
            settings.max_open_positions *
            self.max_daily_loss_pct / 100
        )

        # API error tracking
        self._error_timestamps: List[datetime] = []

        # Callbacks
        self.on_breaker_triggered = None

    def reset_daily(self):
        """Reset daily counters (call at midnight UTC)."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        if self.state.last_reset_date != today:
            logger.info("Resetting daily circuit breaker counters")
            self.state.daily_pnl = 0.0
            self.state.daily_trades = 0
            self.state.daily_losses = 0
            self.state.daily_wins = 0
            self.state.is_daily_loss_breaker = False
            self.state.is_max_trades_breaker = False
            self.state.last_reset_date = today

    def record_trade(self, pnl: float):
        """Record a completed trade and check breakers."""
        self.reset_daily()

        self.state.daily_pnl += pnl
        self.state.daily_trades += 1

        if pnl > 0:
            self.state.daily_wins += 1
            self.state.daily_losses = 0  # Reset consecutive losses
        else:
            self.state.daily_losses += 1

        # Check daily loss breaker
        if self.state.daily_pnl <= -self.max_daily_loss_usdt:
            self._trigger_breaker("DAILY_LOSS", f"Daily loss ${self.state.daily_pnl:.2f} exceeds limit ${-self.max_daily_loss_usdt:.2f}")

        # Check max trades breaker
        if self.state.daily_trades >= self.max_daily_trades:
            self._trigger_breaker("MAX_TRADES", f"Daily trades {self.state.daily_trades} reached limit {self.max_daily_trades}")

        # Check consecutive losses
        if self.state.daily_losses >= self.max_consecutive_losses:
            self._trigger_breaker("CONSECUTIVE_LOSSES", f"{self.state.daily_losses} consecutive losses")

    def record_api_error(self, error_code: int = 0):
        """Record an API error and check for storm."""
        now = datetime.now(timezone.utc)
        self._error_timestamps.append(now)
        self.state.last_api_error = now

        # Clean old timestamps
        cutoff_1m = now - timedelta(minutes=1)
        cutoff_5m = now - timedelta(minutes=5)
        self._error_timestamps = [t for t in self._error_timestamps if t > cutoff_5m]

        # Count recent errors
        self.state.api_errors_1m = sum(1 for t in self._error_timestamps if t > cutoff_1m)
        self.state.api_errors_5m = len(self._error_timestamps)

        # Check thresholds
        if self.state.api_errors_1m >= self.api_error_threshold_1m:
            self._trigger_breaker("API_STORM", f"{self.state.api_errors_1m} API errors in 1 minute")
        elif self.state.api_errors_5m >= self.api_error_threshold_5m:
            self._trigger_breaker("API_STORM", f"{self.state.api_errors_5m} API errors in 5 minutes")

    def record_spread_shock(self, symbol: str, spread_pct: float, normal_spread_pct: float = 0.1):
        """Record abnormal spread and potentially pause symbol."""
        if spread_pct > normal_spread_pct * 2:
            self.state.spread_shocks[symbol] = self.state.spread_shocks.get(symbol, 0) + 1

            if self.state.spread_shocks[symbol] >= self.spread_shock_threshold:
                # Pause symbol for 5 minutes
                pause_until = datetime.now(timezone.utc) + timedelta(minutes=5)
                self.state.symbol_paused_until[symbol] = pause_until
                logger.warning(f"SPREAD SHOCK: {symbol} paused until {pause_until} (spread {spread_pct:.3f}%)")
                self.state.spread_shocks[symbol] = 0  # Reset counter
        else:
            # Reset shock counter if spread is normal
            self.state.spread_shocks[symbol] = 0

    def kill_switch(self, reason: str = "Manual kill switch"):
        """Emergency stop - prevents all trading."""
        self.state.is_killed = True
        logger.critical(f"KILL SWITCH ACTIVATED: {reason}")

        if self.on_breaker_triggered:
            asyncio.create_task(self.on_breaker_triggered("KILL_SWITCH", reason))

    def reset_kill_switch(self):
        """Reset kill switch (requires manual reset)."""
        self.state.is_killed = False
        logger.info("Kill switch reset")

    def can_trade(self) -> tuple[bool, str]:
        """
        Check if trading is allowed.

        Returns:
            (can_trade, reason)
        """
        self.reset_daily()

        if self.state.is_killed:
            return False, "KILL_SWITCH active"

        if self.state.is_daily_loss_breaker:
            return False, f"Daily loss limit reached (${self.state.daily_pnl:.2f})"

        if self.state.is_max_trades_breaker:
            return False, f"Max daily trades reached ({self.state.daily_trades})"

        if self.state.is_api_storm_breaker:
            # Auto-reset after 5 minutes of no errors
            if self.state.last_api_error:
                time_since_error = datetime.now(timezone.utc) - self.state.last_api_error
                if time_since_error > timedelta(minutes=5):
                    self.state.is_api_storm_breaker = False
                    logger.info("API storm breaker auto-reset")
                else:
                    return False, "API error storm - paused"

        return True, ""

    def can_trade_symbol(self, symbol: str) -> tuple[bool, str]:
        """
        Check if specific symbol can be traded.

        Returns:
            (can_trade, reason)
        """
        # First check global breakers
        can_trade, reason = self.can_trade()
        if not can_trade:
            return False, reason

        # Check symbol-specific pause
        if symbol in self.state.symbol_paused_until:
            pause_until = self.state.symbol_paused_until[symbol]
            if datetime.now(timezone.utc) < pause_until:
                return False, f"Symbol paused (spread shock) until {pause_until.strftime('%H:%M:%S')}"
            else:
                # Pause expired
                del self.state.symbol_paused_until[symbol]

        return True, ""

    def _trigger_breaker(self, breaker_type: str, reason: str):
        """Trigger a circuit breaker."""
        logger.warning(f"CIRCUIT BREAKER TRIGGERED: {breaker_type} - {reason}")

        if breaker_type == "DAILY_LOSS":
            self.state.is_daily_loss_breaker = True
        elif breaker_type == "MAX_TRADES":
            self.state.is_max_trades_breaker = True
        elif breaker_type in ("API_STORM", "CONSECUTIVE_LOSSES"):
            self.state.is_api_storm_breaker = True

        if self.on_breaker_triggered:
            asyncio.create_task(self.on_breaker_triggered(breaker_type, reason))

    def get_status(self) -> Dict:
        """Get current circuit breaker status."""
        can_trade, reason = self.can_trade()

        return {
            "can_trade": can_trade,
            "blocked_reason": reason if not can_trade else None,
            "daily_pnl": self.state.daily_pnl,
            "daily_trades": self.state.daily_trades,
            "daily_wins": self.state.daily_wins,
            "daily_losses": self.state.daily_losses,
            "win_rate": (
                self.state.daily_wins / self.state.daily_trades * 100
                if self.state.daily_trades > 0 else 0
            ),
            "api_errors_1m": self.state.api_errors_1m,
            "api_errors_5m": self.state.api_errors_5m,
            "is_killed": self.state.is_killed,
            "breakers_active": {
                "daily_loss": self.state.is_daily_loss_breaker,
                "max_trades": self.state.is_max_trades_breaker,
                "api_storm": self.state.is_api_storm_breaker,
            },
            "paused_symbols": list(self.state.symbol_paused_until.keys()),
            "limits": {
                "max_daily_loss": self.max_daily_loss_usdt,
                "max_daily_trades": self.max_daily_trades,
                "max_consecutive_losses": self.max_consecutive_losses,
            }
        }


# Singleton instance
_circuit_breaker: Optional[CircuitBreaker] = None


def get_circuit_breaker() -> CircuitBreaker:
    """Get the circuit breaker singleton."""
    global _circuit_breaker
    if _circuit_breaker is None:
        _circuit_breaker = CircuitBreaker()
    return _circuit_breaker
