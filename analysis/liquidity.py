"""
MODULE 3: LIQUIDITY SWEEP DETECTOR
===================================
Answers: "Was this move a STOP HUNT or REAL breakout?"

Liquidity pools form at:
- Equal highs (sell stops clustered above)
- Equal lows (buy stops clustered below)

A SWEEP happens when:
1. Price breaks above equal highs OR below equal lows
2. Then REVERSES back inside the range

This is institutional manipulation - they trigger stops
to fill large orders, then move price the other way.

If we detect sweeps, we can:
- AVOID fake breakouts
- ENTER after the sweep completes (reversal trade)
"""
import pandas as pd
import numpy as np
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Tuple
import logging
import json
import os

logger = logging.getLogger(__name__)


class LiquiditySweepDetector:
    """
    Detects liquidity sweeps (stop hunts).

    A sweep = price breaks a level, triggers stops, then reverses.
    These are HIGH PROBABILITY reversal setups.
    """

    def __init__(self, db_path: str = "analysis/liquidity_sweeps.json"):
        self.db_path = db_path
        self.sweeps: List[Dict] = []

        # Detection parameters
        self.equal_tolerance_pct = 0.1  # Highs/lows within 0.1% are "equal"
        self.lookback_period = 50       # Look for equal levels over 50 bars
        self.min_touches = 2            # Minimum touches to form liquidity pool
        self.sweep_threshold_pct = 0.05  # Must break level by at least 0.05%
        self.reversal_confirm_bars = 3  # Bars to confirm reversal

        # Load existing data
        self._load_data()

    def _load_data(self):
        """Load existing sweep data"""
        if os.path.exists(self.db_path):
            try:
                with open(self.db_path, 'r') as f:
                    self.sweeps = json.load(f)
                logger.info(f"Loaded {len(self.sweeps)} existing liquidity sweeps")
            except:
                self.sweeps = []

    def _save_data(self):
        """Save sweep data"""
        os.makedirs(os.path.dirname(self.db_path) or '.', exist_ok=True)
        with open(self.db_path, 'w') as f:
            json.dump(self.sweeps, f, indent=2, default=str)

    def find_equal_highs(self, df: pd.DataFrame, lookback: int = None) -> List[Dict]:
        """
        Find clusters of equal highs (liquidity pools above).

        Equal highs = multiple candles with highs at similar price.
        These act as resistance AND as stop-loss clusters.
        """
        lookback = lookback or self.lookback_period
        if len(df) < lookback:
            return []

        recent = df.tail(lookback).copy()
        highs = recent['high'].values
        indices = recent.index

        equal_high_clusters = []
        used_indices = set()

        for i, (idx, high) in enumerate(zip(indices, highs)):
            if i in used_indices:
                continue

            # Find other highs within tolerance
            tolerance = high * (self.equal_tolerance_pct / 100)
            cluster_indices = []
            cluster_highs = []

            for j, (idx2, high2) in enumerate(zip(indices, highs)):
                if abs(high2 - high) <= tolerance:
                    cluster_indices.append(j)
                    cluster_highs.append(high2)
                    used_indices.add(j)

            if len(cluster_indices) >= self.min_touches:
                equal_high_clusters.append({
                    'level': np.mean(cluster_highs),
                    'touches': len(cluster_indices),
                    'first_touch': str(indices[cluster_indices[0]]),
                    'last_touch': str(indices[cluster_indices[-1]])
                })

        return equal_high_clusters

    def find_equal_lows(self, df: pd.DataFrame, lookback: int = None) -> List[Dict]:
        """
        Find clusters of equal lows (liquidity pools below).
        """
        lookback = lookback or self.lookback_period
        if len(df) < lookback:
            return []

        recent = df.tail(lookback).copy()
        lows = recent['low'].values
        indices = recent.index

        equal_low_clusters = []
        used_indices = set()

        for i, (idx, low) in enumerate(zip(indices, lows)):
            if i in used_indices:
                continue

            tolerance = low * (self.equal_tolerance_pct / 100)
            cluster_indices = []
            cluster_lows = []

            for j, (idx2, low2) in enumerate(zip(indices, lows)):
                if abs(low2 - low) <= tolerance:
                    cluster_indices.append(j)
                    cluster_lows.append(low2)
                    used_indices.add(j)

            if len(cluster_indices) >= self.min_touches:
                equal_low_clusters.append({
                    'level': np.mean(cluster_lows),
                    'touches': len(cluster_indices),
                    'first_touch': str(indices[cluster_indices[0]]),
                    'last_touch': str(indices[cluster_indices[-1]])
                })

        return equal_low_clusters

    def detect_sweep(self, df: pd.DataFrame, symbol: str) -> List[Dict]:
        """
        Detect liquidity sweeps in recent price action.

        A sweep happens when:
        1. Price breaks above equal highs (or below equal lows)
        2. Then closes back inside the range

        Returns list of detected sweeps.
        """
        if len(df) < self.lookback_period + 5:
            return []

        detected = []

        # Find liquidity pools
        equal_highs = self.find_equal_highs(df)
        equal_lows = self.find_equal_lows(df)

        # Check last few candles for sweeps
        for i in range(-self.reversal_confirm_bars, 0):
            candle = df.iloc[i]
            prev_candle = df.iloc[i-1] if i > -len(df) else None

            timestamp_str = str(candle.name)

            # Skip if already logged
            if any(s['timestamp'] == timestamp_str and s['symbol'] == symbol
                   for s in self.sweeps):
                continue

            # Check for HIGH sweep (broke above, closed back inside)
            for eq_high in equal_highs:
                level = eq_high['level']
                threshold = level * (1 + self.sweep_threshold_pct / 100)

                # Wick went above level
                if candle['high'] > threshold:
                    # But close is back below level
                    if candle['close'] < level:
                        sweep = {
                            'id': len(self.sweeps) + len(detected) + 1,
                            'symbol': symbol,
                            'timestamp': timestamp_str,
                            'type': 'HIGH_SWEEP',
                            'direction': 'BEARISH',  # Sweep high = expect reversal down
                            'level': float(level),
                            'wick_high': float(candle['high']),
                            'close': float(candle['close']),
                            'sweep_pct': round((candle['high'] - level) / level * 100, 3),
                            'touches': eq_high['touches'],
                            'analyzed': False,
                            'reversal_confirmed': None,
                            'max_move_after': None
                        }
                        detected.append(sweep)

            # Check for LOW sweep (broke below, closed back inside)
            for eq_low in equal_lows:
                level = eq_low['level']
                threshold = level * (1 - self.sweep_threshold_pct / 100)

                # Wick went below level
                if candle['low'] < threshold:
                    # But close is back above level
                    if candle['close'] > level:
                        sweep = {
                            'id': len(self.sweeps) + len(detected) + 1,
                            'symbol': symbol,
                            'timestamp': timestamp_str,
                            'type': 'LOW_SWEEP',
                            'direction': 'BULLISH',  # Sweep low = expect reversal up
                            'level': float(level),
                            'wick_low': float(candle['low']),
                            'close': float(candle['close']),
                            'sweep_pct': round((level - candle['low']) / level * 100, 3),
                            'touches': eq_low['touches'],
                            'analyzed': False,
                            'reversal_confirmed': None,
                            'max_move_after': None
                        }
                        detected.append(sweep)

        return detected

    def analyze_sweep_outcome(self, sweep: Dict, df: pd.DataFrame) -> Optional[Dict]:
        """
        Analyze what happened after a sweep.

        Did the expected reversal occur?
        """
        sweep_time = pd.to_datetime(sweep['timestamp'])
        entry_price = sweep['close']
        direction = sweep['direction']

        future_data = df[df.index > sweep_time]

        if len(future_data) < 30:
            return None

        # Look at next 30 minutes
        future_30 = future_data.head(30)

        if direction == 'BULLISH':
            max_favorable = ((future_30['high'].max() - entry_price) / entry_price) * 100
            max_adverse = ((entry_price - future_30['low'].min()) / entry_price) * 100
            final_change = ((future_30.iloc[-1]['close'] - entry_price) / entry_price) * 100
            reversal_confirmed = final_change > 0.2  # Moved up as expected
        else:
            max_favorable = ((entry_price - future_30['low'].min()) / entry_price) * 100
            max_adverse = ((future_30['high'].max() - entry_price) / entry_price) * 100
            final_change = ((entry_price - future_30.iloc[-1]['close']) / entry_price) * 100
            reversal_confirmed = final_change > 0.2  # Moved down as expected

        return {
            'sweep_id': sweep['id'],
            'reversal_confirmed': reversal_confirmed,
            'max_favorable_pct': round(max_favorable, 3),
            'max_adverse_pct': round(max_adverse, 3),
            'final_change_pct': round(final_change, 3)
        }

    def process_candle_data(self, df: pd.DataFrame, symbol: str) -> List[Dict]:
        """
        Process candle data to detect and log sweeps.
        """
        new_sweeps = self.detect_sweep(df, symbol)

        for sweep in new_sweeps:
            self.sweeps.append(sweep)
            logger.info(f"💧 LIQUIDITY SWEEP: {symbol}")
            logger.info(f"   Type: {sweep['type']}")
            logger.info(f"   Direction: {sweep['direction']} (expect reversal)")
            logger.info(f"   Level: {sweep['level']:.4f}")
            logger.info(f"   Sweep depth: {sweep['sweep_pct']:.3f}%")
            logger.info(f"   Touches: {sweep['touches']}")

        if new_sweeps:
            self._save_data()

        return new_sweeps

    def analyze_all_sweeps(self, df: pd.DataFrame, symbol: str) -> List[Dict]:
        """Analyze outcomes for all unanalyzed sweeps"""
        results = []

        for sweep in self.sweeps:
            if sweep['symbol'] != symbol or sweep.get('analyzed', False):
                continue

            outcome = self.analyze_sweep_outcome(sweep, df)
            if outcome:
                sweep['analyzed'] = True
                sweep['reversal_confirmed'] = outcome['reversal_confirmed']
                sweep['max_move_after'] = outcome['max_favorable_pct']
                results.append(outcome)

        if results:
            self._save_data()

        return results

    def get_statistics(self) -> Dict:
        """Get sweep statistics"""
        if not self.sweeps:
            return {}

        analyzed = [s for s in self.sweeps if s.get('analyzed', False)]
        if not analyzed:
            return {'total_sweeps': len(self.sweeps), 'analyzed': 0}

        high_sweeps = [s for s in analyzed if s['type'] == 'HIGH_SWEEP']
        low_sweeps = [s for s in analyzed if s['type'] == 'LOW_SWEEP']

        stats = {
            'total_sweeps': len(self.sweeps),
            'analyzed': len(analyzed),
            'high_sweeps': {
                'count': len(high_sweeps),
                'reversal_rate': round(sum(1 for s in high_sweeps if s.get('reversal_confirmed')) / len(high_sweeps) * 100, 1) if high_sweeps else 0,
                'avg_move_after': round(np.mean([s.get('max_move_after', 0) for s in high_sweeps]), 3) if high_sweeps else 0
            },
            'low_sweeps': {
                'count': len(low_sweeps),
                'reversal_rate': round(sum(1 for s in low_sweeps if s.get('reversal_confirmed')) / len(low_sweeps) * 100, 1) if low_sweeps else 0,
                'avg_move_after': round(np.mean([s.get('max_move_after', 0) for s in low_sweeps]), 3) if low_sweeps else 0
            }
        }

        # Overall reversal rate
        total_reversals = sum(1 for s in analyzed if s.get('reversal_confirmed'))
        stats['overall_reversal_rate'] = round(total_reversals / len(analyzed) * 100, 1)

        return stats

    def print_summary(self):
        """Print sweep summary"""
        stats = self.get_statistics()

        print("\n" + "="*70)
        print("LIQUIDITY SWEEP DETECTOR SUMMARY")
        print("="*70)

        if not stats:
            print("No sweeps detected yet")
            return

        print(f"\nTotal sweeps: {stats['total_sweeps']}")
        print(f"Analyzed: {stats['analyzed']}")

        if stats['analyzed'] > 0:
            print(f"\n📈 HIGH SWEEPS (expect reversal DOWN):")
            hs = stats['high_sweeps']
            print(f"   Count: {hs['count']}")
            print(f"   Reversal rate: {hs['reversal_rate']:.1f}%")
            print(f"   Avg move after: {hs['avg_move_after']:.2f}%")

            print(f"\n📉 LOW SWEEPS (expect reversal UP):")
            ls = stats['low_sweeps']
            print(f"   Count: {ls['count']}")
            print(f"   Reversal rate: {ls['reversal_rate']:.1f}%")
            print(f"   Avg move after: {ls['avg_move_after']:.2f}%")

            print(f"\n{'✅ EDGE EXISTS' if stats['overall_reversal_rate'] > 55 else '❌ NO EDGE'}: "
                  f"Overall reversal rate = {stats['overall_reversal_rate']:.1f}%")


# Standalone test
if __name__ == "__main__":
    import sys
    sys.path.insert(0, '/home/user/scalperbot')
    import ccxt

    print("Testing Liquidity Sweep Detector...")

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

    # Detect sweeps
    detector = LiquiditySweepDetector()
    sweeps = detector.process_candle_data(df, symbol)

    print(f"\nFound {len(sweeps)} liquidity sweeps")

    # Analyze outcomes
    detector.analyze_all_sweeps(df, symbol)
    detector.print_summary()
