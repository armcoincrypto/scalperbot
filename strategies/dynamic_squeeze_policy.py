"""
Dynamic Squeeze Policy Manager

Adaptively relaxes BB squeeze thresholds when no signals are found,
while maintaining quality through:
- Time-based fallback (relax after N minutes without signals)
- Pair-specific thresholds (volatile coins get different treatment)
- Dry-run monitoring with auto-revert on excessive losses
"""
import time
import logging
from typing import Dict, Tuple, Optional
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from config import settings

logger = logging.getLogger(__name__)


@dataclass
class SqueezeMode:
    """Represents a squeeze detection mode"""
    name: str
    percentile_threshold: float  # e.g., 0.30 = bottom 30%
    expansion_threshold: float   # e.g., 0.20 = 20% expansion required
    description: str


@dataclass
class PairPolicy:
    """Per-pair policy configuration"""
    symbol: str
    volatility_class: str  # 'high', 'medium', 'low'
    strict_mode: SqueezeMode
    permissive_mode: SqueezeMode
    relax_after_minutes: int  # Minutes without signal before relaxing


@dataclass
class PolicyState:
    """Tracks the current state of the policy for a pair"""
    current_mode: SqueezeMode
    last_signal_time: Optional[float] = None
    relaxed_at: Optional[float] = None
    relaxed_until: Optional[float] = None
    signal_count: int = 0
    false_positive_count: int = 0
    simulated_pnl: float = 0.0


class DynamicSqueezePolicy:
    """
    Manages adaptive squeeze thresholds across all trading pairs

    Strategy:
    - Start strict (bottom 30% + 20% expansion)
    - If no signals for N minutes, relax to permissive mode
    - Stay permissive for M minutes, then revert to strict
    - Volatile pairs (SOL) get shorter relax times
    - Monitor simulated PnL in dry-run, auto-revert if losses exceed threshold
    """

    # Define squeeze modes
    STRICT_MODE = SqueezeMode(
        name="strict",
        percentile_threshold=0.30,
        expansion_threshold=0.20,
        description="Bottom 30% squeeze + 20% expansion"
    )

    MODERATE_MODE = SqueezeMode(
        name="moderate",
        percentile_threshold=0.50,
        expansion_threshold=0.15,
        description="Bottom 50% squeeze + 15% expansion"
    )

    PERMISSIVE_MODE = SqueezeMode(
        name="permissive",
        percentile_threshold=0.70,
        expansion_threshold=0.10,
        description="Bottom 70% squeeze + 10% expansion"
    )

    # Pair volatility classifications
    PAIR_VOLATILITY = {
        'SOL/USDT': 'high',
        'BTC/USDT': 'low',
        'ETH/USDT': 'medium',
        'XRP/USDT': 'medium',
    }

    def __init__(
        self,
        no_signal_timeout_minutes: int = 30,
        permissive_duration_minutes: int = 15,
        dry_run_loss_breaker_pct: float = 2.0,
        enable_dry_run_monitoring: bool = True
    ):
        """
        Initialize dynamic squeeze policy

        Args:
            no_signal_timeout_minutes: Relax after this many minutes without signals
            permissive_duration_minutes: Stay permissive for this long before reverting
            dry_run_loss_breaker_pct: Auto-revert if simulated losses exceed this %
            enable_dry_run_monitoring: Enable safety monitoring (set False to disable)
        """
        self.no_signal_timeout = no_signal_timeout_minutes * 60  # Convert to seconds
        self.permissive_duration = permissive_duration_minutes * 60
        self.loss_breaker_pct = dry_run_loss_breaker_pct
        self.monitoring_enabled = enable_dry_run_monitoring

        # Initialize per-pair policies
        self.pair_policies: Dict[str, PairPolicy] = {}
        self.pair_states: Dict[str, PolicyState] = {}

        # Global tracking
        self.total_signals = 0
        self.total_false_positives = 0
        self.global_simulated_pnl = 0.0
        self.policy_start_time = time.time()

        logger.info("🎯 Dynamic Squeeze Policy initialized")
        logger.info(f"  No-signal timeout: {no_signal_timeout_minutes}m")
        logger.info(f"  Permissive duration: {permissive_duration_minutes}m")
        logger.info(f"  Loss breaker: {dry_run_loss_breaker_pct}%")
        logger.info(f"  Monitoring: {'ENABLED' if enable_dry_run_monitoring else 'DISABLED'}")

    def register_pair(self, symbol: str):
        """Register a trading pair with the policy manager"""
        if symbol in self.pair_policies:
            return

        volatility = self.PAIR_VOLATILITY.get(symbol, 'medium')

        # Volatile pairs relax faster
        relax_time = {
            'high': int(self.no_signal_timeout * 0.7),  # SOL relaxes at 70% of normal time
            'medium': self.no_signal_timeout,
            'low': int(self.no_signal_timeout * 1.3)    # BTC takes 30% longer to relax
        }[volatility]

        # Volatile pairs use moderate instead of permissive
        permissive = self.MODERATE_MODE if volatility == 'high' else self.PERMISSIVE_MODE

        policy = PairPolicy(
            symbol=symbol,
            volatility_class=volatility,
            strict_mode=self.STRICT_MODE,
            permissive_mode=permissive,
            relax_after_minutes=relax_time // 60
        )

        state = PolicyState(current_mode=self.STRICT_MODE)

        self.pair_policies[symbol] = policy
        self.pair_states[symbol] = state

        logger.info(f"📝 Registered {symbol} | Volatility: {volatility} | Relax after: {relax_time//60}m")

    def get_squeeze_thresholds(self, symbol: str) -> Tuple[float, float]:
        """
        Get current squeeze thresholds for a symbol

        Returns:
            (percentile_threshold, expansion_threshold)
        """
        # Register if not already registered
        if symbol not in self.pair_policies:
            self.register_pair(symbol)

        state = self.pair_states[symbol]
        policy = self.pair_policies[symbol]

        current_time = time.time()

        # Check if we should relax the policy
        should_relax = False

        if state.last_signal_time is None:
            # No signal ever - check time since policy start
            time_without_signal = current_time - self.policy_start_time
            should_relax = time_without_signal > policy.relax_after_minutes * 60
        elif state.relaxed_until is None:
            # Have signals before, not currently relaxed
            time_without_signal = current_time - state.last_signal_time
            should_relax = time_without_signal > policy.relax_after_minutes * 60
        else:
            # Currently in permissive mode - check if we should revert
            if current_time > state.relaxed_until:
                # Permissive period expired, revert to strict
                state.current_mode = policy.strict_mode
                state.relaxed_at = None
                state.relaxed_until = None
                logger.info(f"🔒 {symbol}: Reverting to STRICT mode (permissive period expired)")
            else:
                # Still in permissive mode
                should_relax = True

        # Apply relaxation if needed
        if should_relax and state.relaxed_until is None:
            state.current_mode = policy.permissive_mode
            state.relaxed_at = current_time
            state.relaxed_until = current_time + self.permissive_duration

            time_without = (current_time - state.last_signal_time) / 60 if state.last_signal_time else (current_time - self.policy_start_time) / 60
            logger.warning(f"🔓 {symbol}: RELAXING to {state.current_mode.name.upper()} mode")
            logger.warning(f"   Reason: No signals for {time_without:.1f} minutes")
            logger.warning(f"   Will revert to strict in {self.permissive_duration//60} minutes")

        # Check dry-run breaker
        if self.monitoring_enabled and settings.dry_run:
            if state.simulated_pnl < -(self.loss_breaker_pct / 100) * 1000:  # Assume $1000 start capital
                if state.current_mode != policy.strict_mode:
                    logger.error(f"🚨 {symbol}: BREAKER TRIGGERED! Simulated loss: {state.simulated_pnl:.2f}")
                    logger.error(f"   Auto-reverting to STRICT mode")
                    state.current_mode = policy.strict_mode
                    state.relaxed_at = None
                    state.relaxed_until = None

        return (state.current_mode.percentile_threshold, state.current_mode.expansion_threshold)

    def record_signal(
        self,
        symbol: str,
        simulated_pnl: Optional[float] = None,
        was_false_positive: bool = False
    ):
        """
        Record that a signal was generated for this symbol

        Args:
            symbol: Trading pair
            simulated_pnl: Simulated P&L if in dry-run mode
            was_false_positive: True if signal was a losing trade
        """
        if symbol not in self.pair_states:
            self.register_pair(symbol)

        state = self.pair_states[symbol]
        state.last_signal_time = time.time()
        state.signal_count += 1
        self.total_signals += 1

        if was_false_positive:
            state.false_positive_count += 1
            self.total_false_positives += 1

        if simulated_pnl is not None:
            state.simulated_pnl += simulated_pnl
            self.global_simulated_pnl += simulated_pnl

        mode = state.current_mode.name.upper()
        logger.info(f"✅ Signal recorded for {symbol} | Mode: {mode} | PnL: {simulated_pnl or 0:.2f}")

        # If we got a signal while in permissive mode, revert to strict earlier
        if state.relaxed_until is not None:
            logger.info(f"🔒 {symbol}: Got signal in permissive mode, reverting to STRICT early")
            state.current_mode = self.pair_policies[symbol].strict_mode
            state.relaxed_at = None
            state.relaxed_until = None

    def get_status_report(self) -> Dict:
        """Get detailed status report of the policy manager"""
        current_time = time.time()
        uptime_minutes = (current_time - self.policy_start_time) / 60

        pair_statuses = {}
        for symbol, state in self.pair_states.items():
            policy = self.pair_policies[symbol]

            time_since_signal = None
            if state.last_signal_time:
                time_since_signal = (current_time - state.last_signal_time) / 60

            time_in_mode = None
            if state.relaxed_at:
                time_in_mode = (current_time - state.relaxed_at) / 60

            pair_statuses[symbol] = {
                'mode': state.current_mode.name,
                'volatility': policy.volatility_class,
                'signals': state.signal_count,
                'false_positives': state.false_positive_count,
                'simulated_pnl': state.simulated_pnl,
                'time_since_signal_min': time_since_signal,
                'is_relaxed': state.relaxed_until is not None,
                'time_in_current_mode_min': time_in_mode,
            }

        return {
            'uptime_minutes': uptime_minutes,
            'total_signals': self.total_signals,
            'total_false_positives': self.total_false_positives,
            'global_simulated_pnl': self.global_simulated_pnl,
            'false_positive_rate': self.total_false_positives / max(self.total_signals, 1),
            'pairs': pair_statuses
        }

    def print_status(self):
        """Print formatted status report"""
        report = self.get_status_report()

        logger.info("\n" + "="*70)
        logger.info("📊 DYNAMIC SQUEEZE POLICY STATUS")
        logger.info("="*70)
        logger.info(f"Uptime: {report['uptime_minutes']:.1f} minutes")
        logger.info(f"Total signals: {report['total_signals']}")
        logger.info(f"False positives: {report['total_false_positives']} ({report['false_positive_rate']*100:.1f}%)")
        logger.info(f"Simulated PnL: ${report['global_simulated_pnl']:.2f}")
        logger.info("")

        for symbol, status in report['pairs'].items():
            mode_emoji = "🔒" if status['mode'] == 'strict' else "🔓"
            logger.info(f"{mode_emoji} {symbol} | Mode: {status['mode'].upper()} | Vol: {status['volatility']}")
            logger.info(f"   Signals: {status['signals']} | FP: {status['false_positives']} | PnL: ${status['simulated_pnl']:.2f}")

            if status['time_since_signal_min'] is not None:
                logger.info(f"   Last signal: {status['time_since_signal_min']:.1f}m ago")
            else:
                logger.info(f"   Last signal: Never")

            if status['is_relaxed'] and status['time_in_current_mode_min']:
                logger.info(f"   In permissive mode for: {status['time_in_current_mode_min']:.1f}m")

        logger.info("="*70 + "\n")
