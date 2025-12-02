"""
Recommendation Engine for ScalperBot
Analyzes metrics and generates parameter adjustment recommendations
"""
from dataclasses import dataclass, field
from typing import Dict, List, Any, Optional
from enum import Enum


class RecommendationTag(str, Enum):
    AGGRESSIVE_CANDIDATE = "AGGRESSIVE_CANDIDATE"
    CONSERVATIVE = "CONSERVATIVE"
    NO_CHANGE = "NO_CHANGE"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


@dataclass
class ParameterSuggestion:
    """A specific parameter change suggestion"""
    parameter_name: str
    current_value: Any
    suggested_value: Any
    scope: str  # 'global', 'per_symbol', 'per_session'
    reasoning: str
    confidence: str  # 'low', 'medium', 'high'


@dataclass
class SymbolRecommendation:
    """Recommendation for a specific symbol"""
    symbol: str
    tag: RecommendationTag
    suggestions: List[ParameterSuggestion] = field(default_factory=list)
    rationale: str = ""
    metrics_summary: Dict[str, Any] = field(default_factory=dict)


@dataclass
class AnalysisConfig:
    """Configuration for the recommendation engine"""
    min_trades: int = 20
    aggressive_nearmiss_pct: float = 15.0
    aggressive_min_winrate: float = 55.0
    aggressive_min_pf: float = 1.15
    max_slippage_pct: float = 0.3
    conservative_winrate_threshold: float = 45.0
    conservative_pf_threshold: float = 1.0
    conservative_slippage_threshold: float = 1.0


class RecommendationEngine:
    """Generate strategy recommendations based on analysis data"""

    def __init__(self, config: Optional[AnalysisConfig] = None):
        self.config = config or AnalysisConfig()

    def analyze(
        self,
        log_data: Dict[str, Any],
        trade_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Analyze log and trade data to generate recommendations

        Args:
            log_data: Output from LogParser.parse_file()
            trade_data: Output from DBReader.read_trades()

        Returns:
            Dict with recommendations per symbol and overall summary
        """

        recommendations: Dict[str, SymbolRecommendation] = {}
        aggressive_candidates: List[str] = []
        conservative_symbols: List[str] = []

        # Get all symbols from both sources
        log_symbols = set(log_data.get('symbols', {}).keys())
        trade_symbols = set(trade_data.get('symbols', {}).keys())
        all_symbols = log_symbols | trade_symbols

        for symbol in all_symbols:
            log_metrics = log_data.get('symbols', {}).get(symbol, {})
            trade_metrics = trade_data.get('symbols', {}).get(symbol, {})

            rec = self._analyze_symbol(symbol, log_metrics, trade_metrics)
            recommendations[symbol] = rec

            if rec.tag == RecommendationTag.AGGRESSIVE_CANDIDATE:
                aggressive_candidates.append(symbol)
            elif rec.tag == RecommendationTag.CONSERVATIVE:
                conservative_symbols.append(symbol)

        # Build overall summary
        all_suggestions = []
        for rec in recommendations.values():
            for sug in rec.suggestions:
                all_suggestions.append({
                    'symbol': rec.symbol,
                    'parameter': sug.parameter_name,
                    'current': sug.current_value,
                    'suggested': sug.suggested_value,
                    'scope': sug.scope,
                    'reasoning': sug.reasoning,
                    'confidence': sug.confidence,
                })

        return {
            'symbols': {
                sym: self._recommendation_to_dict(rec)
                for sym, rec in recommendations.items()
            },
            'summary': {
                'total_symbols_analyzed': len(all_symbols),
                'aggressive_candidates': aggressive_candidates,
                'conservative_symbols': conservative_symbols,
                'no_change_symbols': [
                    s for s, r in recommendations.items()
                    if r.tag == RecommendationTag.NO_CHANGE
                ],
                'insufficient_data_symbols': [
                    s for s, r in recommendations.items()
                    if r.tag == RecommendationTag.INSUFFICIENT_DATA
                ],
            },
            'all_suggestions': all_suggestions,
            'action_items': self._generate_action_items(recommendations),
        }

    def _analyze_symbol(
        self,
        symbol: str,
        log_metrics: Dict[str, Any],
        trade_metrics: Dict[str, Any]
    ) -> SymbolRecommendation:
        """Analyze a single symbol and generate recommendation"""

        rec = SymbolRecommendation(symbol=symbol, tag=RecommendationTag.NO_CHANGE)

        # Check for sufficient data
        total_trades = trade_metrics.get('total_trades', 0)
        total_cycles = log_metrics.get('total_cycles', 0)

        if total_trades < self.config.min_trades and total_cycles < 100:
            rec.tag = RecommendationTag.INSUFFICIENT_DATA
            rec.rationale = f"Insufficient data: {total_trades} trades, {total_cycles} cycles"
            return rec

        # Extract key metrics
        near_miss_rate = log_metrics.get('near_miss', {}).get('rate_pct', 0)
        win_rate = trade_metrics.get('win_rate', 0)
        profit_factor = trade_metrics.get('profit_factor', 0)
        if profit_factor == 'inf':
            profit_factor = 100  # Cap for comparison
        avg_slippage = trade_metrics.get('avg_slippage_pct', 0)

        # Store metrics summary
        rec.metrics_summary = {
            'near_miss_rate': near_miss_rate,
            'win_rate': win_rate,
            'profit_factor': profit_factor,
            'avg_slippage': avg_slippage,
            'total_trades': total_trades,
            'total_cycles': total_cycles,
            'signal_count': log_metrics.get('signal_count', 0),
        }

        # Check for CONSERVATIVE tag first
        if self._is_conservative(win_rate, profit_factor, avg_slippage):
            rec.tag = RecommendationTag.CONSERVATIVE
            rec.rationale = self._build_conservative_rationale(win_rate, profit_factor, avg_slippage)
            rec.suggestions = self._generate_conservative_suggestions(symbol, log_metrics, trade_metrics)
            return rec

        # Check for AGGRESSIVE_CANDIDATE
        if self._is_aggressive_candidate(near_miss_rate, win_rate, profit_factor, avg_slippage):
            rec.tag = RecommendationTag.AGGRESSIVE_CANDIDATE
            rec.rationale = self._build_aggressive_rationale(near_miss_rate, win_rate, profit_factor)
            rec.suggestions = self._generate_aggressive_suggestions(
                symbol, log_metrics, trade_metrics, near_miss_rate
            )
            return rec

        # Default: NO_CHANGE
        rec.tag = RecommendationTag.NO_CHANGE
        rec.rationale = "Metrics within acceptable range; no changes recommended"

        return rec

    def _is_conservative(self, win_rate: float, pf: float, slippage: float) -> bool:
        """Check if symbol should be marked conservative"""
        return (
            win_rate < self.config.conservative_winrate_threshold or
            pf < self.config.conservative_pf_threshold or
            slippage > self.config.conservative_slippage_threshold
        )

    def _is_aggressive_candidate(
        self,
        near_miss_rate: float,
        win_rate: float,
        pf: float,
        slippage: float
    ) -> bool:
        """Check if symbol is an aggressive candidate"""
        return (
            near_miss_rate >= self.config.aggressive_nearmiss_pct and
            win_rate >= self.config.aggressive_min_winrate and
            pf >= self.config.aggressive_min_pf and
            slippage <= self.config.max_slippage_pct
        )

    def _build_conservative_rationale(
        self,
        win_rate: float,
        pf: float,
        slippage: float
    ) -> str:
        """Build rationale for conservative recommendation"""
        reasons = []

        if win_rate < self.config.conservative_winrate_threshold:
            reasons.append(f"low win rate ({win_rate:.1f}% < {self.config.conservative_winrate_threshold}%)")

        if pf < self.config.conservative_pf_threshold:
            reasons.append(f"low profit factor ({pf:.2f} < {self.config.conservative_pf_threshold})")

        if slippage > self.config.conservative_slippage_threshold:
            reasons.append(f"high slippage ({slippage:.2f}% > {self.config.conservative_slippage_threshold}%)")

        return f"Conservative due to: {'; '.join(reasons)}"

    def _build_aggressive_rationale(
        self,
        near_miss_rate: float,
        win_rate: float,
        pf: float
    ) -> str:
        """Build rationale for aggressive candidate"""
        return (
            f"High near-miss rate ({near_miss_rate:.1f}%) with strong performance "
            f"(win rate: {win_rate:.1f}%, PF: {pf:.2f}) suggests loosening breakout threshold"
        )

    def _generate_conservative_suggestions(
        self,
        symbol: str,
        log_metrics: Dict[str, Any],
        trade_metrics: Dict[str, Any]
    ) -> List[ParameterSuggestion]:
        """Generate suggestions for conservative symbols"""

        suggestions = []

        # Suggest tightening breakout buffer
        suggestions.append(ParameterSuggestion(
            parameter_name="green4_breakout_buffer_bps",
            current_value=10,  # Default
            suggested_value=15,
            scope="per_symbol",
            reasoning="Increase breakout buffer to reduce false signals",
            confidence="medium"
        ))

        # If win rate is very low, consider removing
        win_rate = trade_metrics.get('win_rate', 50)
        if win_rate < 35:
            suggestions.append(ParameterSuggestion(
                parameter_name="trading_pairs",
                current_value=f"includes {symbol}",
                suggested_value=f"consider removing {symbol}",
                scope="global",
                reasoning=f"Very low win rate ({win_rate:.1f}%) suggests pair may not be suitable",
                confidence="low"
            ))

        return suggestions

    def _generate_aggressive_suggestions(
        self,
        symbol: str,
        log_metrics: Dict[str, Any],
        trade_metrics: Dict[str, Any],
        near_miss_rate: float
    ) -> List[ParameterSuggestion]:
        """Generate suggestions for aggressive candidates"""

        suggestions = []

        # Calculate breakout buffer reduction
        # Rule: delta = min((near_miss_rate - threshold) * 0.02, 0.1)
        delta = min((near_miss_rate - self.config.aggressive_nearmiss_pct) * 0.02, 0.1)
        current_buffer = 10  # Default in basis points (0.1%)
        new_buffer = max(5, current_buffer - int(delta * 100))  # Don't go below 5 bps

        suggestions.append(ParameterSuggestion(
            parameter_name="green4_breakout_buffer_bps",
            current_value=current_buffer,
            suggested_value=new_buffer,
            scope="per_symbol",
            reasoning=f"High near-miss rate ({near_miss_rate:.1f}%) suggests buffer is too tight; "
                      f"reduce from {current_buffer/100:.2f}% to {new_buffer/100:.2f}%",
            confidence="high" if near_miss_rate > 20 else "medium"
        ))

        # Check breakout gap distribution
        gap_stats = log_metrics.get('breakout_gap_stats', {})
        if gap_stats:
            median_gap = gap_stats.get('median', 0)
            if median_gap < 0.15:  # Very close misses
                suggestions.append(ParameterSuggestion(
                    parameter_name="green4_breakout_buffer_bps",
                    current_value=current_buffer,
                    suggested_value=max(3, new_buffer - 2),
                    scope="per_symbol",
                    reasoning=f"Median breakout gap is very small ({median_gap:.3f}%); "
                              "consider more aggressive reduction",
                    confidence="medium"
                ))

        # Check blocking filter distribution
        blocking = log_metrics.get('near_miss', {}).get('blocking_filter', {})
        if blocking:
            most_blocking = max(blocking.items(), key=lambda x: x[1])
            if most_blocking[0] == 'GREEN4' and most_blocking[1] > 50:
                suggestions.append(ParameterSuggestion(
                    parameter_name="green4_breakout_period",
                    current_value=10,
                    suggested_value=8,
                    scope="per_symbol",
                    reasoning=f"GREEN4 is blocking {most_blocking[1]} times; "
                              "shorter lookback may help catch breakouts faster",
                    confidence="low"
                ))

        return suggestions

    def _generate_action_items(
        self,
        recommendations: Dict[str, SymbolRecommendation]
    ) -> List[str]:
        """Generate prioritized action items"""

        actions = []

        # Priority 1: Aggressive candidates
        aggressive = [r for r in recommendations.values()
                     if r.tag == RecommendationTag.AGGRESSIVE_CANDIDATE]
        if aggressive:
            symbols = [r.symbol for r in aggressive]
            actions.append(
                f"PRIORITY 1: Review aggressive candidates ({', '.join(symbols)}) - "
                "these have high near-miss rates with good performance. "
                "Consider loosening breakout thresholds after 48h testing."
            )

        # Priority 2: Conservative symbols
        conservative = [r for r in recommendations.values()
                       if r.tag == RecommendationTag.CONSERVATIVE]
        if conservative:
            symbols = [r.symbol for r in conservative]
            actions.append(
                f"PRIORITY 2: Monitor conservative symbols ({', '.join(symbols)}) - "
                "consider tightening thresholds or reducing position sizes."
            )

        # Priority 3: Data collection
        insufficient = [r for r in recommendations.values()
                       if r.tag == RecommendationTag.INSUFFICIENT_DATA]
        if insufficient:
            symbols = [r.symbol for r in insufficient]
            actions.append(
                f"PRIORITY 3: Collect more data for ({', '.join(symbols)}) - "
                f"need {self.config.min_trades}+ trades for reliable analysis."
            )

        return actions

    def _recommendation_to_dict(self, rec: SymbolRecommendation) -> Dict[str, Any]:
        """Convert recommendation to dictionary"""
        return {
            'tag': rec.tag.value,
            'rationale': rec.rationale,
            'metrics_summary': rec.metrics_summary,
            'suggestions': [
                {
                    'parameter': s.parameter_name,
                    'current': s.current_value,
                    'suggested': s.suggested_value,
                    'scope': s.scope,
                    'reasoning': s.reasoning,
                    'confidence': s.confidence,
                }
                for s in rec.suggestions
            ],
        }
