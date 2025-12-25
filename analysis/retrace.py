"""
MODULE 2: RETRACE & CONTINUATION ANALYZER
==========================================
Answers: "After a displacement, what ACTUALLY happens?"

For each displacement event:
- Track price for 5, 15, 30, 60 minutes
- Measure: max retrace %, continuation yes/no
- Measure: max favorable move, max adverse move

This tells us:
1. Do displacements CONTINUE or FADE?
2. How deep do retraces go before continuation?
3. What's the realistic profit target after a displacement?
"""
import pandas as pd
import numpy as np
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Tuple
import logging
import json
import os

logger = logging.getLogger(__name__)


class RetraceAnalyzer:
    """
    Analyzes what happens AFTER a displacement event.

    This is where we discover:
    - Do big moves continue or reverse?
    - How much retrace is "normal" before continuation?
    - What's a realistic take profit target?
    """

    def __init__(self, displacement_db_path: str = "analysis/displacements.json",
                 results_path: str = "analysis/retrace_results.json"):
        self.displacement_db_path = displacement_db_path
        self.results_path = results_path
        self.results: List[Dict] = []

        # Analysis timeframes (in minutes)
        self.timeframes = [5, 15, 30, 60]

        # Load existing results
        self._load_results()

    def _load_results(self):
        """Load existing analysis results"""
        if os.path.exists(self.results_path):
            try:
                with open(self.results_path, 'r') as f:
                    self.results = json.load(f)
                logger.info(f"Loaded {len(self.results)} existing retrace analyses")
            except:
                self.results = []

    def _save_results(self):
        """Save analysis results"""
        os.makedirs(os.path.dirname(self.results_path) or '.', exist_ok=True)
        with open(self.results_path, 'w') as f:
            json.dump(self.results, f, indent=2, default=str)

    def _load_displacements(self) -> List[Dict]:
        """Load displacement events from MODULE 1"""
        if not os.path.exists(self.displacement_db_path):
            return []
        try:
            with open(self.displacement_db_path, 'r') as f:
                return json.load(f)
        except:
            return []

    def analyze_displacement(self, displacement: Dict, df: pd.DataFrame) -> Optional[Dict]:
        """
        Analyze what happens after a single displacement event.

        Args:
            displacement: The displacement event from MODULE 1
            df: Price data covering the period after the displacement

        Returns:
            Analysis results dict, or None if not enough data
        """
        # Handle both millisecond timestamps and ISO datetime strings
        ts = displacement['timestamp']
        try:
            if isinstance(ts, (int, float)):
                # Millisecond timestamp
                disp_time = pd.to_datetime(ts, unit='ms', utc=True)
            elif isinstance(ts, str) and ts.isdigit():
                # String that's actually a number (ms timestamp)
                disp_time = pd.to_datetime(int(ts), unit='ms', utc=True)
            elif isinstance(ts, str) and len(ts) > 12 and ts[:4].isdigit():
                # ISO format string like "2025-12-25T20:56:00+00:00"
                disp_time = pd.to_datetime(ts, utc=True)
            else:
                # Fallback - let pandas try to figure it out
                disp_time = pd.to_datetime(ts)
        except Exception as e:
            logger.warning(f"Cannot parse timestamp '{ts}': {e}")
            return None
        direction = displacement['direction']
        entry_price = displacement['close']

        # Get data after the displacement
        future_data = df[df.index > disp_time]

        if len(future_data) < 60:  # Need at least 60 1-minute candles
            return None

        results = {
            'displacement_id': displacement['id'],
            'symbol': displacement['symbol'],
            'displacement_time': str(disp_time),
            'direction': direction,
            'entry_price': entry_price,
            'displacement_pct': displacement['displacement_pct'],
            'timeframe_analysis': {}
        }

        # Analyze each timeframe
        for minutes in self.timeframes:
            tf_data = future_data.head(minutes)

            if len(tf_data) < minutes:
                continue

            # Calculate metrics relative to entry
            if direction == 'BULLISH':
                # For bullish: favorable = higher, adverse = lower
                max_favorable = ((tf_data['high'].max() - entry_price) / entry_price) * 100
                max_adverse = ((entry_price - tf_data['low'].min()) / entry_price) * 100
                final_price = tf_data.iloc[-1]['close']
                final_change = ((final_price - entry_price) / entry_price) * 100
                continuation = final_change > 0.1  # Ended higher than entry
            else:
                # For bearish: favorable = lower, adverse = higher
                max_favorable = ((entry_price - tf_data['low'].min()) / entry_price) * 100
                max_adverse = ((tf_data['high'].max() - entry_price) / entry_price) * 100
                final_price = tf_data.iloc[-1]['close']
                final_change = ((entry_price - final_price) / entry_price) * 100
                continuation = final_change > 0.1  # Ended lower than entry (in direction)

            # Max retrace is max_adverse (how much it moved against the direction)
            max_retrace = max_adverse

            results['timeframe_analysis'][str(minutes)] = {
                'minutes': minutes,
                'max_favorable_pct': round(max_favorable, 3),
                'max_adverse_pct': round(max_adverse, 3),
                'max_retrace_pct': round(max_retrace, 3),
                'final_change_pct': round(final_change, 3),
                'continuation': continuation
            }

        # Overall assessment (using 30-minute window as primary)
        if '30' in results['timeframe_analysis']:
            tf30 = results['timeframe_analysis']['30']
            results['overall'] = {
                'continuation': tf30['continuation'],
                'max_favorable_pct': tf30['max_favorable_pct'],
                'max_adverse_pct': tf30['max_adverse_pct'],
                'max_retrace_pct': tf30['max_retrace_pct'],
                'final_change_pct': tf30['final_change_pct']
            }

        return results

    def analyze_all_displacements(self, df: pd.DataFrame, symbol: str) -> List[Dict]:
        """
        Analyze all unanalyzed displacements for a symbol.

        Args:
            df: Full price data DataFrame
            symbol: Symbol to analyze

        Returns:
            List of new analysis results
        """
        displacements = self._load_displacements()

        # Filter to this symbol and unanalyzed
        unanalyzed = [
            d for d in displacements
            if d['symbol'] == symbol and not d.get('analyzed', False)
        ]

        logger.info(f"Analyzing {len(unanalyzed)} displacements for {symbol}")

        new_results = []
        for disp in unanalyzed:
            result = self.analyze_displacement(disp, df)
            if result:
                new_results.append(result)
                self.results.append(result)

                # Mark displacement as analyzed (update the source file)
                disp['analyzed'] = True
                if 'overall' in result:
                    disp['continuation'] = result['overall']['continuation']
                    disp['max_retrace_pct'] = result['overall']['max_retrace_pct']
                    disp['max_favorable_pct'] = result['overall']['max_favorable_pct']
                    disp['max_adverse_pct'] = result['overall']['max_adverse_pct']

        # Save updated displacements
        if new_results:
            with open(self.displacement_db_path, 'w') as f:
                json.dump(displacements, f, indent=2, default=str)
            self._save_results()

        return new_results

    def get_statistics(self) -> Dict:
        """Calculate continuation statistics across all analyzed displacements"""
        if not self.results:
            return {}

        stats = {
            'total_analyzed': len(self.results),
            'by_direction': {},
            'by_timeframe': {}
        }

        # By direction
        for direction in ['BULLISH', 'BEARISH']:
            dir_results = [r for r in self.results if r['direction'] == direction]
            if not dir_results:
                continue

            continuations = sum(1 for r in dir_results if r.get('overall', {}).get('continuation', False))

            stats['by_direction'][direction] = {
                'count': len(dir_results),
                'continuation_rate': round(continuations / len(dir_results) * 100, 1) if dir_results else 0,
                'avg_max_favorable': round(np.mean([r['overall']['max_favorable_pct'] for r in dir_results if 'overall' in r]), 3),
                'avg_max_adverse': round(np.mean([r['overall']['max_adverse_pct'] for r in dir_results if 'overall' in r]), 3),
                'avg_max_retrace': round(np.mean([r['overall']['max_retrace_pct'] for r in dir_results if 'overall' in r]), 3)
            }

        # By timeframe
        for tf in self.timeframes:
            tf_key = str(tf)
            tf_data = [
                r['timeframe_analysis'][tf_key]
                for r in self.results
                if tf_key in r.get('timeframe_analysis', {})
            ]

            if not tf_data:
                continue

            continuations = sum(1 for t in tf_data if t['continuation'])

            stats['by_timeframe'][f'{tf}min'] = {
                'count': len(tf_data),
                'continuation_rate': round(continuations / len(tf_data) * 100, 1),
                'avg_max_favorable': round(np.mean([t['max_favorable_pct'] for t in tf_data]), 3),
                'avg_max_adverse': round(np.mean([t['max_adverse_pct'] for t in tf_data]), 3),
                'avg_final_change': round(np.mean([t['final_change_pct'] for t in tf_data]), 3)
            }

        return stats

    def get_edge_assessment(self) -> Dict:
        """
        Determine if displacements provide a tradeable edge.

        An edge exists if:
        1. Continuation rate > 55%
        2. Avg favorable > avg adverse (positive expectancy)
        """
        stats = self.get_statistics()

        if not stats:
            return {'has_edge': False, 'reason': 'No data'}

        assessment = {
            'has_edge': False,
            'edge_score': 0,
            'recommendations': []
        }

        # Check overall continuation rate
        total_cont = 0
        total_count = 0
        for dir_stats in stats.get('by_direction', {}).values():
            total_cont += dir_stats['count'] * dir_stats['continuation_rate'] / 100
            total_count += dir_stats['count']

        if total_count > 0:
            overall_cont_rate = total_cont / total_count * 100
            assessment['overall_continuation_rate'] = round(overall_cont_rate, 1)

            if overall_cont_rate > 55:
                assessment['edge_score'] += 1
                assessment['recommendations'].append(
                    f"✅ Continuation rate {overall_cont_rate:.1f}% > 55% threshold"
                )
            else:
                assessment['recommendations'].append(
                    f"❌ Continuation rate {overall_cont_rate:.1f}% < 55% threshold"
                )

        # Check expectancy (favorable vs adverse)
        for tf, tf_stats in stats.get('by_timeframe', {}).items():
            if tf_stats['avg_max_favorable'] > tf_stats['avg_max_adverse']:
                assessment['edge_score'] += 1
                assessment['recommendations'].append(
                    f"✅ {tf}: Favorable ({tf_stats['avg_max_favorable']:.2f}%) > Adverse ({tf_stats['avg_max_adverse']:.2f}%)"
                )
            else:
                assessment['recommendations'].append(
                    f"❌ {tf}: Favorable ({tf_stats['avg_max_favorable']:.2f}%) < Adverse ({tf_stats['avg_max_adverse']:.2f}%)"
                )

        assessment['has_edge'] = assessment['edge_score'] >= 3

        return assessment

    def print_summary(self):
        """Print analysis summary"""
        stats = self.get_statistics()
        edge = self.get_edge_assessment()

        print("\n" + "="*70)
        print("RETRACE & CONTINUATION ANALYSIS")
        print("="*70)

        if not stats:
            print("No displacement data analyzed yet")
            return

        print(f"\nTotal displacements analyzed: {stats['total_analyzed']}")

        print("\n📊 BY DIRECTION:")
        for direction, dir_stats in stats.get('by_direction', {}).items():
            print(f"\n  {direction}:")
            print(f"    Count: {dir_stats['count']}")
            print(f"    Continuation rate: {dir_stats['continuation_rate']:.1f}%")
            print(f"    Avg max favorable: {dir_stats['avg_max_favorable']:.2f}%")
            print(f"    Avg max adverse: {dir_stats['avg_max_adverse']:.2f}%")
            print(f"    Avg max retrace: {dir_stats['avg_max_retrace']:.2f}%")

        print("\n⏱️ BY TIMEFRAME:")
        for tf, tf_stats in stats.get('by_timeframe', {}).items():
            print(f"\n  {tf}:")
            print(f"    Continuation rate: {tf_stats['continuation_rate']:.1f}%")
            print(f"    Avg max favorable: {tf_stats['avg_max_favorable']:.2f}%")
            print(f"    Avg max adverse: {tf_stats['avg_max_adverse']:.2f}%")
            print(f"    Avg final change: {tf_stats['avg_final_change']:.2f}%")

        print("\n" + "="*70)
        print("EDGE ASSESSMENT")
        print("="*70)
        print(f"\n{'✅ EDGE EXISTS' if edge['has_edge'] else '❌ NO EDGE'}")
        print(f"Edge score: {edge['edge_score']}/5")
        print("\nDetails:")
        for rec in edge['recommendations']:
            print(f"  {rec}")


# Standalone test
if __name__ == "__main__":
    import sys
    sys.path.insert(0, '/home/user/scalperbot')
    import ccxt
    from analysis.displacement import DisplacementDetector

    print("Testing Retrace Analyzer...")

    # Fetch data
    exchange = ccxt.mexc({'enableRateLimit': True})
    symbol = 'BNB/USDT'

    since = int((datetime.now(timezone.utc) - timedelta(days=3)).timestamp() * 1000)
    end_time = int(datetime.now(timezone.utc).timestamp() * 1000)
    all_candles = []

    print(f"Fetching data for {symbol}...")
    while since < end_time:
        candles = exchange.fetch_ohlcv(symbol, '1m', since=since, limit=1000)
        if not candles:
            break
        all_candles.extend(candles)
        since = candles[-1][0] + 60000
        if len(candles) < 100:
            break

    print(f"  Total: {len(all_candles)} candles")

    df = pd.DataFrame(all_candles, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
    df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms', utc=True)
    df.set_index('timestamp', inplace=True)

    # First detect displacements
    detector = DisplacementDetector()
    detector.process_candle_data(df, symbol)

    # Then analyze retraces
    analyzer = RetraceAnalyzer()
    new_results = analyzer.analyze_all_displacements(df, symbol)

    print(f"\nAnalyzed {len(new_results)} new displacements")
    analyzer.print_summary()
