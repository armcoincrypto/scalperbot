"""
Candidate selection for best trade opportunities.
Instead of trading every signal, select the best candidate.
"""

from dataclasses import dataclass
from typing import List, Optional

from scalperbot.core.scoring import SignalScore
from scalperbot.core.filters import FilterResult
from scalperbot.log import get_logger

logger = get_logger(__name__)


@dataclass
class TradeCandidate:
    """A potential trade opportunity."""
    symbol: str
    score: SignalScore
    filter_result: FilterResult
    priority: float  # Combined ranking score
    entry_price: float
    tick_id: Optional[int] = None  # For research/analytics tracking


class CandidateSelector:
    """
    Select best trade candidate from multiple opportunities.

    Ranking criteria:
    1. Signal score (higher = better)
    2. Spread (lower = better)
    3. Volume (higher = better)
    4. Safety filters must pass
    """

    def __init__(self):
        # Weights for ranking
        self.score_weight = 1.0
        self.spread_weight = -0.5  # Negative because lower is better
        self.volume_weight = 0.1

    def select_best(
        self,
        candidates: List[TradeCandidate],
        max_positions: int = 1
    ) -> List[TradeCandidate]:
        """
        Select best candidates for trading.

        Args:
            candidates: List of potential trades
            max_positions: Maximum positions to open

        Returns:
            List of selected candidates (sorted by priority)
        """
        if not candidates:
            return []

        # Filter out candidates that didn't pass safety filters
        valid_candidates = [
            c for c in candidates
            if c.filter_result.passed and c.score.is_buy_signal
        ]

        if not valid_candidates:
            logger.debug("No valid candidates after filtering")
            return []

        # Calculate priority scores
        for candidate in valid_candidates:
            candidate.priority = self._calculate_priority(candidate)

        # Sort by priority (highest first)
        sorted_candidates = sorted(
            valid_candidates,
            key=lambda c: c.priority,
            reverse=True
        )

        # Return top N
        selected = sorted_candidates[:max_positions]

        if selected:
            logger.info(
                f"Selected {len(selected)} candidate(s): "
                f"{', '.join(c.symbol for c in selected)}"
            )
            for c in selected:
                logger.debug(
                    f"  {c.symbol}: score={c.score.total_score:.2f}, "
                    f"spread={c.filter_result.spread_pct:.3f}%, "
                    f"priority={c.priority:.2f}"
                )

        return selected

    def _calculate_priority(self, candidate: TradeCandidate) -> float:
        """Calculate priority score for ranking."""
        score_component = candidate.score.total_score * self.score_weight

        # Normalize spread (0.1% = 1.0, 0.5% = 0.2)
        spread_normalized = 0.1 / max(candidate.filter_result.spread_pct, 0.01)
        spread_component = spread_normalized * self.spread_weight

        # Normalize volume (log scale)
        import math
        volume_log = math.log10(max(candidate.filter_result.volume_24h, 1000))
        volume_component = volume_log * self.volume_weight

        return score_component + spread_component + volume_component

    def should_skip(
        self,
        symbol: str,
        has_position: bool,
        in_cooldown: bool,
        open_count: int,
        max_positions: int
    ) -> tuple[bool, str]:
        """
        Check if symbol should be skipped.

        Returns:
            (should_skip, reason)
        """
        if has_position:
            return True, "already_has_position"

        if in_cooldown:
            return True, "in_cooldown"

        if open_count >= max_positions:
            return True, "max_positions_reached"

        return False, ""
