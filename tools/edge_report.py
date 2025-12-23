#!/usr/bin/env python3
"""
PHASE 5: EDGE DISCOVERY REPORT
===============================
Answers the fundamental question: "Is there an edge?"

This is NOT about making the bot profitable.
This is about discovering MARKET TRUTH.

If no edge exists - WE STOP TRADING.

Reports:
1. After displacement → what retrace % gives best continuation?
2. Which regime gives positive expectancy?
3. Which direction fails most often?
4. What happens AFTER stop sweeps?
5. What conditions precede large wins?

Each finding includes:
- Win rate (%)
- Expectancy (average $ per trade)
- R-multiple (reward/risk ratio achieved)
- Sample size (N)
- Confidence level (statistical significance)
"""
import sys
sys.path.insert(0, '/home/user/scalperbot')

import pandas as pd
import numpy as np
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Tuple, Optional
from scipy import stats
import json
import os

from analysis.research_db import get_research_db

# Minimum sample sizes for statistical validity
MIN_SAMPLE_WEAK = 20      # Weak evidence
MIN_SAMPLE_MODERATE = 50  # Moderate evidence
MIN_SAMPLE_STRONG = 100   # Strong evidence

# Edge thresholds
EDGE_WIN_RATE_THRESHOLD = 0.55  # 55% win rate required
EDGE_EXPECTANCY_THRESHOLD = 0.1  # 0.1% average profit per trade
EDGE_PROFIT_FACTOR_THRESHOLD = 1.3  # Wins must be 1.3x losses


def calculate_confidence_level(sample_size: int, win_rate: float) -> Tuple[str, float]:
    """
    Calculate statistical confidence level.

    Returns:
        - Confidence level: HIGH, MEDIUM, LOW, INSUFFICIENT
        - P-value from binomial test (null hypothesis: win_rate = 50%)
    """
    if sample_size < MIN_SAMPLE_WEAK:
        return 'INSUFFICIENT', 1.0

    # Binomial test: is win rate significantly different from 50%?
    wins = int(sample_size * win_rate)
    # One-sided test: is win rate > 50%?
    p_value = stats.binom_test(wins, sample_size, 0.5, alternative='greater')

    if sample_size >= MIN_SAMPLE_STRONG and p_value < 0.05:
        return 'HIGH', p_value
    elif sample_size >= MIN_SAMPLE_MODERATE and p_value < 0.1:
        return 'MEDIUM', p_value
    elif sample_size >= MIN_SAMPLE_WEAK and p_value < 0.2:
        return 'LOW', p_value
    else:
        return 'INSUFFICIENT', p_value


def calculate_edge_metrics(outcomes: List[Dict]) -> Dict:
    """
    Calculate edge metrics from a list of trade outcomes.

    Each outcome should have:
    - pnl_pct: Profit/loss percentage
    - outcome: 'WIN' or 'LOSS'
    """
    if not outcomes:
        return {'has_edge': False, 'reason': 'No data'}

    wins = [o for o in outcomes if o.get('outcome') == 'WIN']
    losses = [o for o in outcomes if o.get('outcome') == 'LOSS']

    n = len(outcomes)
    n_wins = len(wins)
    n_losses = len(losses)

    if n == 0:
        return {'has_edge': False, 'reason': 'No completed trades'}

    win_rate = n_wins / n

    # Average win and loss sizes
    avg_win = np.mean([o['pnl_pct'] for o in wins]) if wins else 0
    avg_loss = abs(np.mean([o['pnl_pct'] for o in losses])) if losses else 0

    # Expectancy = (Win% × Avg Win) - (Loss% × Avg Loss)
    loss_rate = 1 - win_rate
    expectancy = (win_rate * avg_win) - (loss_rate * avg_loss)

    # Profit factor = Total Wins / Total Losses
    total_wins = sum(o['pnl_pct'] for o in wins) if wins else 0
    total_losses = abs(sum(o['pnl_pct'] for o in losses)) if losses else 0
    profit_factor = total_wins / total_losses if total_losses > 0 else float('inf')

    # R-multiple = Avg Win / Avg Loss
    r_multiple = avg_win / avg_loss if avg_loss > 0 else float('inf')

    # Confidence level
    confidence, p_value = calculate_confidence_level(n, win_rate)

    # Does edge exist?
    has_edge = (
        win_rate >= EDGE_WIN_RATE_THRESHOLD and
        expectancy >= EDGE_EXPECTANCY_THRESHOLD and
        profit_factor >= EDGE_PROFIT_FACTOR_THRESHOLD and
        confidence in ['HIGH', 'MEDIUM']
    )

    return {
        'sample_size': n,
        'wins': n_wins,
        'losses': n_losses,
        'win_rate': round(win_rate * 100, 2),
        'avg_win_pct': round(avg_win, 3),
        'avg_loss_pct': round(avg_loss, 3),
        'expectancy': round(expectancy, 3),
        'profit_factor': round(profit_factor, 3) if profit_factor != float('inf') else 'INF',
        'r_multiple': round(r_multiple, 3) if r_multiple != float('inf') else 'INF',
        'confidence_level': confidence,
        'p_value': round(p_value, 4),
        'has_edge': has_edge,
        'edge_reason': _get_edge_reason(win_rate, expectancy, profit_factor, confidence)
    }


def _get_edge_reason(win_rate: float, expectancy: float, pf: float, confidence: str) -> str:
    """Explain why edge does or doesn't exist"""
    reasons = []

    if win_rate < EDGE_WIN_RATE_THRESHOLD:
        reasons.append(f"Win rate {win_rate*100:.1f}% < {EDGE_WIN_RATE_THRESHOLD*100}% threshold")
    else:
        reasons.append(f"Win rate {win_rate*100:.1f}% ✓")

    if expectancy < EDGE_EXPECTANCY_THRESHOLD:
        reasons.append(f"Expectancy {expectancy:.3f}% < {EDGE_EXPECTANCY_THRESHOLD}% threshold")
    else:
        reasons.append(f"Expectancy {expectancy:.3f}% ✓")

    if pf < EDGE_PROFIT_FACTOR_THRESHOLD:
        reasons.append(f"Profit factor {pf:.2f} < {EDGE_PROFIT_FACTOR_THRESHOLD} threshold")
    else:
        reasons.append(f"Profit factor {pf:.2f} ✓")

    if confidence in ['INSUFFICIENT', 'LOW']:
        reasons.append(f"Confidence {confidence} - need more data")

    return "; ".join(reasons)


class EdgeReport:
    """
    Generates edge discovery reports from research data.

    Purpose: Find market truth, not force profitability.
    """

    def __init__(self):
        self.db = get_research_db()
        self.findings: List[Dict] = []

    def analyze_displacement_continuation(self) -> Dict:
        """
        QUESTION 1: After displacement → what retrace % gives best continuation?

        We want to know:
        - Do displacements continue in their direction?
        - How much retrace is "normal" before continuation?
        - What's the optimal entry after a displacement?
        """
        print("\n" + "="*70)
        print("ANALYSIS 1: DISPLACEMENT CONTINUATION")
        print("Question: Do displacements continue or reverse?")
        print("="*70)

        retraces = self.db.get_retraces()

        if not retraces:
            print("⚠️ NO DATA: No retrace analyses found")
            return {'has_data': False}

        # Convert to dataframe
        df = pd.DataFrame(retraces)

        # Analyze 30-minute continuation (primary timeframe)
        if 'tf30_continuation' in df.columns:
            continuations = df['tf30_continuation'].sum()
            total = len(df[df['tf30_continuation'].notna()])

            if total > 0:
                cont_rate = continuations / total

                # Create outcomes for edge calculation
                outcomes = []
                for _, row in df.iterrows():
                    if pd.notna(row.get('tf30_continuation')):
                        outcomes.append({
                            'outcome': 'WIN' if row['tf30_continuation'] else 'LOSS',
                            'pnl_pct': row.get('tf30_max_favorable_pct', 0) if row['tf30_continuation']
                                       else -row.get('tf30_max_adverse_pct', 0)
                        })

                metrics = calculate_edge_metrics(outcomes)

                print(f"\n30-minute analysis (N={total}):")
                print(f"  Continuation rate: {cont_rate*100:.1f}%")
                print(f"  Avg favorable move: {df['tf30_max_favorable_pct'].mean():.2f}%")
                print(f"  Avg adverse move: {df['tf30_max_adverse_pct'].mean():.2f}%")
                print(f"  Expectancy: {metrics['expectancy']:.3f}%")
                print(f"  Confidence: {metrics['confidence_level']}")

                if metrics['has_edge']:
                    print(f"\n✅ EDGE EXISTS: Displacements tend to continue")
                else:
                    print(f"\n❌ NO EDGE: {metrics['edge_reason']}")

                # Analyze by direction
                print("\nBy direction:")
                for direction in ['BULLISH', 'BEARISH']:
                    dir_df = df[df['direction'] == direction]
                    if len(dir_df) > 0 and 'tf30_continuation' in dir_df.columns:
                        dir_cont = dir_df['tf30_continuation'].sum()
                        dir_total = len(dir_df[dir_df['tf30_continuation'].notna()])
                        if dir_total > 0:
                            print(f"  {direction}: {dir_cont}/{dir_total} = {dir_cont/dir_total*100:.1f}% continue")

                return {
                    'has_data': True,
                    'metrics': metrics,
                    'continuation_rate': round(cont_rate * 100, 1),
                    'by_direction': {}  # Add per-direction analysis
                }

        print("⚠️ No 30-minute continuation data available")
        return {'has_data': False}

    def analyze_regime_expectancy(self) -> Dict:
        """
        QUESTION 2: Which regime gives positive expectancy?

        We want to know:
        - Should we trade in TRENDING, RANGING, or CHOP?
        - Which regime has the best edge?
        """
        print("\n" + "="*70)
        print("ANALYSIS 2: REGIME EXPECTANCY")
        print("Question: Which market regime gives positive expectancy?")
        print("="*70)

        regimes = self.db.get_regimes()
        trades = self.db.get_simulated_trades()

        if not regimes:
            print("⚠️ NO DATA: No regime classifications found")
            return {'has_data': False}

        if not trades:
            print("⚠️ NO DATA: No simulated trades found")
            print("  (Need to run simulations with regime data)")
            return {'has_data': False}

        # Analyze trades by regime
        trades_df = pd.DataFrame(trades)

        results = {}
        for regime in ['TRENDING_UP', 'TRENDING_DOWN', 'RANGING', 'CHOP']:
            regime_trades = trades_df[trades_df['regime'] == regime]

            if len(regime_trades) == 0:
                continue

            outcomes = []
            for _, row in regime_trades.iterrows():
                if pd.notna(row.get('outcome')):
                    outcomes.append({
                        'outcome': row['outcome'],
                        'pnl_pct': row.get('pnl_pct', 0)
                    })

            if outcomes:
                metrics = calculate_edge_metrics(outcomes)
                results[regime] = metrics

                print(f"\n{regime}:")
                print(f"  Trades: {metrics['sample_size']}")
                print(f"  Win rate: {metrics['win_rate']:.1f}%")
                print(f"  Expectancy: {metrics['expectancy']:.3f}%")
                print(f"  Has edge: {'✅ YES' if metrics['has_edge'] else '❌ NO'}")

        # Summary
        print("\n" + "-"*50)
        print("REGIME RECOMMENDATION:")
        edge_regimes = [r for r, m in results.items() if m.get('has_edge', False)]
        if edge_regimes:
            print(f"  Trade in: {', '.join(edge_regimes)}")
        else:
            print("  ⚠️ No regime shows clear edge. Consider NOT trading.")

        return {'has_data': True, 'by_regime': results}

    def analyze_direction_failure(self) -> Dict:
        """
        QUESTION 3: Which direction fails most often?

        We want to know:
        - Do LONG trades fail more than SHORT?
        - Is there a directional bias we're fighting?
        """
        print("\n" + "="*70)
        print("ANALYSIS 3: DIRECTIONAL FAILURE RATES")
        print("Question: Which direction fails most often?")
        print("="*70)

        # Analyze from displacements (which direction continues less)
        retraces = self.db.get_retraces()

        if not retraces:
            print("⚠️ NO DATA: No retrace analyses found")
            return {'has_data': False}

        df = pd.DataFrame(retraces)

        results = {}
        for direction in ['BULLISH', 'BEARISH']:
            dir_df = df[df['direction'] == direction]

            if len(dir_df) == 0:
                continue

            if 'tf30_continuation' in dir_df.columns:
                outcomes = []
                for _, row in dir_df.iterrows():
                    if pd.notna(row.get('tf30_continuation')):
                        outcomes.append({
                            'outcome': 'WIN' if row['tf30_continuation'] else 'LOSS',
                            'pnl_pct': row.get('tf30_max_favorable_pct', 0) if row['tf30_continuation']
                                       else -row.get('tf30_max_adverse_pct', 0)
                        })

                if outcomes:
                    metrics = calculate_edge_metrics(outcomes)
                    results[direction] = metrics

                    failure_rate = 100 - metrics['win_rate']
                    print(f"\n{direction}:")
                    print(f"  Failure rate: {failure_rate:.1f}%")
                    print(f"  Sample size: {metrics['sample_size']}")
                    print(f"  Confidence: {metrics['confidence_level']}")

        # Compare directions
        print("\n" + "-"*50)
        if 'BULLISH' in results and 'BEARISH' in results:
            bull_fail = 100 - results['BULLISH']['win_rate']
            bear_fail = 100 - results['BEARISH']['win_rate']

            if abs(bull_fail - bear_fail) > 10:
                worse = 'BULLISH' if bull_fail > bear_fail else 'BEARISH'
                print(f"⚠️ WARNING: {worse} has significantly higher failure rate")
                print(f"   Consider avoiding {worse} trades or adjusting parameters")
            else:
                print("No significant directional bias detected")

        return {'has_data': True, 'by_direction': results}

    def analyze_sweep_outcomes(self) -> Dict:
        """
        QUESTION 4: What happens AFTER stop sweeps?

        We want to know:
        - Do sweeps actually lead to reversals?
        - Is there an edge in trading sweep reversals?
        """
        print("\n" + "="*70)
        print("ANALYSIS 4: LIQUIDITY SWEEP OUTCOMES")
        print("Question: Do stop sweeps lead to reversals?")
        print("="*70)

        sweeps = self.db.get_liquidity_events()

        if not sweeps:
            print("⚠️ NO DATA: No liquidity sweep events found")
            return {'has_data': False}

        df = pd.DataFrame(sweeps)
        analyzed = df[df['reversal_confirmed'].notna()]

        if len(analyzed) == 0:
            print("⚠️ NO DATA: No analyzed sweeps (need to run outcome analysis)")
            return {'has_data': False}

        # Calculate reversal success rate
        reversals = analyzed['reversal_confirmed'].sum()
        total = len(analyzed)
        reversal_rate = reversals / total

        outcomes = []
        for _, row in analyzed.iterrows():
            outcomes.append({
                'outcome': 'WIN' if row['reversal_confirmed'] else 'LOSS',
                'pnl_pct': row.get('max_move_after', 0) if row['reversal_confirmed']
                           else -row.get('max_move_after', 0.3)  # Assume 0.3% loss if no move data
            })

        metrics = calculate_edge_metrics(outcomes)

        print(f"\nSweep reversal analysis (N={total}):")
        print(f"  Reversal rate: {reversal_rate*100:.1f}%")
        print(f"  Win rate: {metrics['win_rate']:.1f}%")
        print(f"  Expectancy: {metrics['expectancy']:.3f}%")
        print(f"  Confidence: {metrics['confidence_level']}")

        if metrics['has_edge']:
            print(f"\n✅ EDGE EXISTS: Sweep reversals are tradeable")
        else:
            print(f"\n❌ NO EDGE: {metrics['edge_reason']}")

        # By sweep type
        print("\nBy sweep type:")
        for sweep_type in ['HIGH_SWEEP', 'LOW_SWEEP']:
            type_df = analyzed[analyzed['event_type'] == sweep_type]
            if len(type_df) > 0:
                type_reversals = type_df['reversal_confirmed'].sum()
                print(f"  {sweep_type}: {type_reversals}/{len(type_df)} = {type_reversals/len(type_df)*100:.1f}% reverse")

        return {'has_data': True, 'metrics': metrics, 'reversal_rate': round(reversal_rate * 100, 1)}

    def analyze_win_conditions(self) -> Dict:
        """
        QUESTION 5: What conditions precede large wins?

        We want to know:
        - What do winning trades have in common?
        - Can we identify these conditions before entry?
        """
        print("\n" + "="*70)
        print("ANALYSIS 5: WIN CONDITIONS")
        print("Question: What conditions precede large wins?")
        print("="*70)

        trades = self.db.get_simulated_trades()

        if not trades:
            print("⚠️ NO DATA: No simulated trades found")
            return {'has_data': False}

        df = pd.DataFrame(trades)
        completed = df[df['outcome'].notna()]

        if len(completed) == 0:
            print("⚠️ NO DATA: No completed trades")
            return {'has_data': False}

        wins = completed[completed['outcome'] == 'WIN']
        losses = completed[completed['outcome'] == 'LOSS']

        if len(wins) == 0:
            print("⚠️ NO WINS: No winning trades to analyze")
            return {'has_data': False}

        print(f"\nWinning trades: {len(wins)}")
        print(f"Losing trades: {len(losses)}")

        # Analyze conditions
        findings = []

        # Check regime distribution in wins vs losses
        if 'regime' in df.columns:
            print("\nRegime distribution:")
            win_regimes = wins['regime'].value_counts(normalize=True)
            loss_regimes = losses['regime'].value_counts(normalize=True) if len(losses) > 0 else pd.Series()

            for regime in win_regimes.index:
                win_pct = win_regimes.get(regime, 0) * 100
                loss_pct = loss_regimes.get(regime, 0) * 100 if regime in loss_regimes.index else 0
                diff = win_pct - loss_pct
                if abs(diff) > 10:
                    finding = f"  {regime}: {win_pct:.0f}% of wins vs {loss_pct:.0f}% of losses"
                    findings.append(finding)
                    print(finding)

        # Check PnL distribution
        print("\nPnL characteristics:")
        print(f"  Avg win: +{wins['pnl_pct'].mean():.2f}%")
        if len(losses) > 0:
            print(f"  Avg loss: {losses['pnl_pct'].mean():.2f}%")

        # Large wins analysis
        if len(wins) >= 5:
            large_wins = wins.nlargest(5, 'pnl_pct')
            print("\nTop 5 wins:")
            for _, win in large_wins.iterrows():
                print(f"  +{win['pnl_pct']:.2f}% | {win['symbol']} | {win.get('regime', 'N/A')} | {win.get('entry_reason', 'N/A')[:30]}")

        return {'has_data': True, 'findings': findings}

    def generate_full_report(self) -> Dict:
        """
        Generate complete edge discovery report.

        This is the TRUTH about whether we should trade.
        """
        print("\n" + "#"*70)
        print("#" + " "*68 + "#")
        print("#" + "    EDGE DISCOVERY REPORT".center(68) + "#")
        print("#" + "    Finding Market Truth, Not Forcing Profitability".center(68) + "#")
        print("#" + " "*68 + "#")
        print("#"*70)
        print(f"\nGenerated: {datetime.now(timezone.utc).isoformat()}")

        # Run all analyses
        results = {
            'displacement_continuation': self.analyze_displacement_continuation(),
            'regime_expectancy': self.analyze_regime_expectancy(),
            'direction_failure': self.analyze_direction_failure(),
            'sweep_outcomes': self.analyze_sweep_outcomes(),
            'win_conditions': self.analyze_win_conditions()
        }

        # Final verdict
        print("\n" + "#"*70)
        print("FINAL VERDICT")
        print("#"*70)

        edges_found = []
        no_edges = []

        # Check displacement continuation
        if results['displacement_continuation'].get('has_data'):
            if results['displacement_continuation'].get('metrics', {}).get('has_edge'):
                edges_found.append("Displacement continuation")
            else:
                no_edges.append("Displacement continuation")

        # Check sweep reversals
        if results['sweep_outcomes'].get('has_data'):
            if results['sweep_outcomes'].get('metrics', {}).get('has_edge'):
                edges_found.append("Sweep reversals")
            else:
                no_edges.append("Sweep reversals")

        # Check regime
        if results['regime_expectancy'].get('has_data'):
            regime_edges = [r for r, m in results['regime_expectancy'].get('by_regime', {}).items()
                           if m.get('has_edge')]
            if regime_edges:
                edges_found.append(f"Regime: {', '.join(regime_edges)}")

        # Print verdict
        if edges_found:
            print("\n✅ EDGES FOUND:")
            for edge in edges_found:
                print(f"   - {edge}")
            print("\n   Consider building strategy around these edges.")
        else:
            print("\n❌ NO STATISTICALLY SIGNIFICANT EDGES FOUND")

        if no_edges:
            print("\n⚠️ NO EDGE IN:")
            for ne in no_edges:
                print(f"   - {ne}")

        # Data quality warning
        counts = self.db.get_table_counts()
        total_data = sum(counts.values())

        print("\n" + "-"*50)
        print("DATA QUALITY:")
        if total_data < 100:
            print(f"⚠️ LOW DATA: Only {total_data} records. Need more data for reliable conclusions.")
            print("   Run research mode for several days to collect more market events.")
        elif total_data < 500:
            print(f"📊 MODERATE DATA: {total_data} records. Conclusions are preliminary.")
        else:
            print(f"✅ GOOD DATA: {total_data} records. Conclusions are more reliable.")

        # Recommendation
        print("\n" + "-"*50)
        print("RECOMMENDATION:")
        if not edges_found and total_data >= 100:
            print("   🛑 DO NOT TRADE - No edge discovered.")
            print("   Either collect more data or accept that this strategy has no edge.")
        elif not edges_found:
            print("   ⏳ WAIT - Not enough data to determine if edge exists.")
            print("   Continue research mode data collection.")
        else:
            print("   📈 CONSIDER TRADING - Edges found.")
            print("   But validate with out-of-sample testing before real money.")

        return results


def main():
    """Run edge discovery report"""
    report = EdgeReport()
    results = report.generate_full_report()

    # Save results
    output_path = "analysis/edge_report.json"
    with open(output_path, 'w') as f:
        # Convert to JSON-serializable format
        def convert(obj):
            if isinstance(obj, (np.int64, np.int32)):
                return int(obj)
            elif isinstance(obj, (np.float64, np.float32)):
                return float(obj)
            elif isinstance(obj, pd.Timestamp):
                return str(obj)
            return str(obj)

        json.dump(results, f, indent=2, default=convert)

    print(f"\n\nReport saved to: {output_path}")


if __name__ == "__main__":
    main()
