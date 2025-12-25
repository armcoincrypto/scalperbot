"""
MODULE 1: PRICE DISPLACEMENT DETECTOR
=====================================
Detects REAL moves, not noise.

A displacement is when "something actually happened":
- Candle body > 2.5x average body (institutional move)
- Volume > 2x average (real participation)
- Price breaks recent range (structural break)

This is the FOUNDATION - we first detect events,
then analyze what happens after them.
"""
import pandas as pd
import numpy as np
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple
import logging
import json
import os

logger = logging.getLogger(__name__)


class DisplacementDetector:
    """
    Detects significant price displacements (real moves, not noise).

    A displacement indicates institutional activity or news impact.
    These are the moments we want to study.
    """

    def __init__(self, db_path: str = "analysis/displacements.json"):
        self.db_path = db_path
        self.displacements: List[Dict] = []

        # Detection parameters - relaxed for more sensitivity
        self.body_multiplier = 1.5      # Candle body > 1.5x average = displacement
        self.volume_multiplier = 1.3    # Volume > 1.3x average = significant
        self.range_lookback = 20        # Look for range breaks over 20 candles
        self.min_displacement_pct = 0.15  # Minimum 0.15% move to qualify

        # Load existing data
        self._load_data()

    def _load_data(self):
        """Load existing displacement data"""
        if os.path.exists(self.db_path):
            try:
                with open(self.db_path, 'r') as f:
                    self.displacements = json.load(f)
                logger.info(f"Loaded {len(self.displacements)} existing displacements")
            except:
                self.displacements = []

    def _save_data(self):
        """Save displacement data"""
        os.makedirs(os.path.dirname(self.db_path) or '.', exist_ok=True)
        with open(self.db_path, 'w') as f:
            json.dump(self.displacements, f, indent=2, default=str)

    def calculate_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """Add displacement detection indicators to dataframe"""
        df = df.copy()

        # Candle body size
        df['body'] = abs(df['close'] - df['open'])
        df['body_avg'] = df['body'].rolling(window=20).mean()
        df['body_ratio'] = df['body'] / df['body_avg']

        # Volume ratio
        df['vol_avg'] = df['volume'].rolling(window=20).mean()
        df['vol_ratio'] = df['volume'] / df['vol_avg']

        # Range (high-low)
        df['range'] = df['high'] - df['low']
        df['range_avg'] = df['range'].rolling(window=20).mean()

        # Recent high/low for range breaks
        df['high_20'] = df['high'].rolling(window=self.range_lookback).max()
        df['low_20'] = df['low'].rolling(window=self.range_lookback).min()

        # Displacement percentage
        df['displacement_pct'] = (df['body'] / df['open']) * 100

        # Direction
        df['direction'] = np.where(df['close'] > df['open'], 'BULLISH', 'BEARISH')

        # Detect displacements
        df['is_body_displacement'] = df['body_ratio'] > self.body_multiplier
        df['is_volume_spike'] = df['vol_ratio'] > self.volume_multiplier
        df['breaks_high'] = df['high'] > df['high_20'].shift(1)
        df['breaks_low'] = df['low'] < df['low_20'].shift(1)

        # Combined displacement score
        df['displacement_score'] = (
            df['is_body_displacement'].astype(int) +
            df['is_volume_spike'].astype(int) +
            df['breaks_high'].astype(int) +
            df['breaks_low'].astype(int)
        )

        return df

    def detect_displacements(self, df: pd.DataFrame, symbol: str) -> List[Dict]:
        """
        Detect displacement events in the data.

        Returns list of displacement events with full context.
        """
        df = self.calculate_indicators(df)
        detected = []

        for i in range(25, len(df)):
            row = df.iloc[i]

            # Must have minimum displacement size
            if row['displacement_pct'] < self.min_displacement_pct:
                continue

            # Must have at least 2 displacement signals
            if row['displacement_score'] < 2:
                continue

            # Skip if we already logged this timestamp
            timestamp_str = str(row.name)
            if any(d['timestamp'] == timestamp_str and d['symbol'] == symbol
                   for d in self.displacements):
                continue

            # Determine displacement type
            disp_type = []
            if row['is_body_displacement']:
                disp_type.append('BODY')
            if row['is_volume_spike']:
                disp_type.append('VOLUME')
            if row['breaks_high']:
                disp_type.append('BREAK_HIGH')
            if row['breaks_low']:
                disp_type.append('BREAK_LOW')

            displacement = {
                'id': len(self.displacements) + len(detected) + 1,
                'symbol': symbol,
                'timestamp': timestamp_str,
                'direction': row['direction'],
                'displacement_pct': round(row['displacement_pct'], 3),
                'body_ratio': round(row['body_ratio'], 2),
                'volume_ratio': round(row['vol_ratio'], 2),
                'displacement_type': disp_type,
                'score': int(row['displacement_score']),
                'open': float(row['open']),
                'high': float(row['high']),
                'low': float(row['low']),
                'close': float(row['close']),
                'volume': float(row['volume']),
                # These will be filled by retrace analyzer
                'analyzed': False,
                'continuation': None,
                'max_retrace_pct': None,
                'max_favorable_pct': None,
                'max_adverse_pct': None
            }

            detected.append(displacement)

        return detected

    def log_displacement(self, displacement: Dict):
        """Log a new displacement event"""
        self.displacements.append(displacement)
        self._save_data()

        logger.info(f"🔥 DISPLACEMENT DETECTED: {displacement['symbol']}")
        logger.info(f"   Time: {displacement['timestamp']}")
        logger.info(f"   Direction: {displacement['direction']}")
        logger.info(f"   Size: {displacement['displacement_pct']:.2f}%")
        logger.info(f"   Body ratio: {displacement['body_ratio']:.1f}x")
        logger.info(f"   Volume ratio: {displacement['volume_ratio']:.1f}x")
        logger.info(f"   Type: {', '.join(displacement['displacement_type'])}")

    def process_candle_data(self, df: pd.DataFrame, symbol: str) -> List[Dict]:
        """
        Process candle data and detect/log displacements.

        Call this from the main bot loop with fresh candle data.
        """
        new_displacements = self.detect_displacements(df, symbol)

        for disp in new_displacements:
            self.log_displacement(disp)

        return new_displacements

    def get_unanalyzed_displacements(self) -> List[Dict]:
        """Get displacements that haven't been analyzed for continuation yet"""
        return [d for d in self.displacements if not d['analyzed']]

    def update_displacement(self, disp_id: int, updates: Dict):
        """Update a displacement with analysis results"""
        for d in self.displacements:
            if d['id'] == disp_id:
                d.update(updates)
                break
        self._save_data()

    def get_statistics(self) -> Dict:
        """Get displacement statistics"""
        if not self.displacements:
            return {}

        df = pd.DataFrame(self.displacements)

        stats = {
            'total_displacements': len(df),
            'bullish': len(df[df['direction'] == 'BULLISH']),
            'bearish': len(df[df['direction'] == 'BEARISH']),
            'avg_displacement_pct': df['displacement_pct'].mean(),
            'avg_body_ratio': df['body_ratio'].mean(),
            'avg_volume_ratio': df['volume_ratio'].mean(),
        }

        # Continuation stats if available
        analyzed = df[df['analyzed'] == True]
        if len(analyzed) > 0:
            continued = analyzed[analyzed['continuation'] == True]
            stats['continuation_rate'] = len(continued) / len(analyzed) * 100
            stats['avg_max_favorable'] = analyzed['max_favorable_pct'].mean()
            stats['avg_max_adverse'] = analyzed['max_adverse_pct'].mean()

        return stats

    def print_summary(self):
        """Print summary of detected displacements"""
        stats = self.get_statistics()

        print("\n" + "="*60)
        print("DISPLACEMENT DETECTOR SUMMARY")
        print("="*60)

        if not stats:
            print("No displacements detected yet")
            return

        print(f"Total displacements: {stats['total_displacements']}")
        print(f"  Bullish: {stats['bullish']}")
        print(f"  Bearish: {stats['bearish']}")
        print(f"\nAverage characteristics:")
        print(f"  Displacement size: {stats['avg_displacement_pct']:.2f}%")
        print(f"  Body ratio: {stats['avg_body_ratio']:.1f}x average")
        print(f"  Volume ratio: {stats['avg_volume_ratio']:.1f}x average")

        if 'continuation_rate' in stats:
            print(f"\nContinuation analysis:")
            print(f"  Continuation rate: {stats['continuation_rate']:.1f}%")
            print(f"  Avg max favorable: {stats['avg_max_favorable']:.2f}%")
            print(f"  Avg max adverse: {stats['avg_max_adverse']:.2f}%")


# Standalone test
if __name__ == "__main__":
    import sys
    sys.path.insert(0, '/home/user/scalperbot')
    import ccxt
    from datetime import timedelta

    print("Testing Displacement Detector...")

    # Fetch some data
    exchange = ccxt.mexc({'enableRateLimit': True})
    symbol = 'BNB/USDT'

    since = int((datetime.now(timezone.utc) - timedelta(days=3)).timestamp() * 1000)
    candles = exchange.fetch_ohlcv(symbol, '1m', since=since, limit=1000)

    df = pd.DataFrame(candles, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
    df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms', utc=True)
    df.set_index('timestamp', inplace=True)

    # Detect displacements
    detector = DisplacementDetector()
    displacements = detector.process_candle_data(df, symbol)

    print(f"\nFound {len(displacements)} displacements in {symbol}")
    detector.print_summary()
