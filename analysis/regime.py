"""
MODULE 4: REGIME CLASSIFIER
============================
Answers: "What TYPE of market is this right now?"

Three regimes:
1. TRENDING: Directional movement, ATR expanding
2. RANGING: Sideways oscillation, mean-reverting
3. CHOP: Random noise, no edge

WHY THIS MATTERS:
- Breakout strategies work in TRENDING regimes
- Mean reversion works in RANGING regimes
- NOTHING works in CHOP - stay out!

The #1 reason strategies fail is trading the wrong
regime with the wrong strategy.
"""
import pandas as pd
import numpy as np
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Tuple
from enum import Enum
import logging
import json
import os

logger = logging.getLogger(__name__)


class MarketRegime(Enum):
    TRENDING_UP = "TRENDING_UP"
    TRENDING_DOWN = "TRENDING_DOWN"
    RANGING = "RANGING"
    CHOP = "CHOP"
    UNKNOWN = "UNKNOWN"


class RegimeClassifier:
    """
    Classifies current market regime.

    Uses multiple signals:
    1. ATR expansion/contraction (volatility)
    2. Directional persistence (trend strength)
    3. Price vs moving average (trend direction)
    4. Range vs breakout behavior
    """

    def __init__(self, db_path: str = "analysis/regime_history.json"):
        self.db_path = db_path
        self.history: List[Dict] = []

        # Detection parameters
        self.atr_period = 14
        self.ema_period = 20
        self.lookback = 50  # Bars to analyze for regime
        self.trend_threshold = 0.6  # 60% of bars in same direction = trending
        self.atr_expansion_threshold = 1.3  # ATR > 1.3x average = expanding
        self.atr_contraction_threshold = 0.7  # ATR < 0.7x average = contracting

        # Load history
        self._load_data()

    def _load_data(self):
        """Load regime history"""
        if os.path.exists(self.db_path):
            try:
                with open(self.db_path, 'r') as f:
                    self.history = json.load(f)
                logger.info(f"Loaded {len(self.history)} regime records")
            except:
                self.history = []

    def _save_data(self):
        """Save regime history"""
        os.makedirs(os.path.dirname(self.db_path) or '.', exist_ok=True)
        with open(self.db_path, 'w') as f:
            json.dump(self.history, f, indent=2, default=str)

    def calculate_atr(self, df: pd.DataFrame, period: int = 14) -> pd.Series:
        """Calculate Average True Range"""
        high = df['high']
        low = df['low']
        close = df['close'].shift(1)

        tr1 = high - low
        tr2 = abs(high - close)
        tr3 = abs(low - close)

        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        atr = tr.rolling(window=period).mean()
        return atr

    def calculate_directional_persistence(self, df: pd.DataFrame, lookback: int = None) -> Dict:
        """
        Calculate how persistent price direction is.

        Returns:
        - up_persistence: % of bars that closed higher than open
        - trend_strength: Correlation of close with time (R-squared)
        """
        lookback = lookback or self.lookback
        recent = df.tail(lookback).copy()

        # Up/down bar count
        up_bars = (recent['close'] > recent['open']).sum()
        down_bars = (recent['close'] < recent['open']).sum()
        total_bars = len(recent)

        up_pct = up_bars / total_bars if total_bars > 0 else 0.5
        down_pct = down_bars / total_bars if total_bars > 0 else 0.5

        # Trend strength (linear regression R-squared)
        closes = recent['close'].values
        x = np.arange(len(closes))

        if len(closes) > 2:
            correlation = np.corrcoef(x, closes)[0, 1]
            r_squared = correlation ** 2 if not np.isnan(correlation) else 0
        else:
            r_squared = 0

        # Direction
        if len(closes) >= 2:
            slope = (closes[-1] - closes[0]) / len(closes)
            direction = 'UP' if slope > 0 else 'DOWN'
        else:
            direction = 'NEUTRAL'

        return {
            'up_pct': round(up_pct, 3),
            'down_pct': round(down_pct, 3),
            'r_squared': round(r_squared, 3),
            'direction': direction
        }

    def calculate_range_behavior(self, df: pd.DataFrame, lookback: int = None) -> Dict:
        """
        Analyze if price is ranging (bouncing between levels).

        Ranging markets have:
        - Price oscillating around a mean
        - Multiple touches of support/resistance
        - Low directional persistence
        """
        lookback = lookback or self.lookback
        recent = df.tail(lookback).copy()

        high = recent['high'].max()
        low = recent['low'].min()
        mid = (high + low) / 2
        range_size = high - low
        current = recent.iloc[-1]['close']

        # How many times did price cross the midpoint?
        crosses = 0
        above = recent.iloc[0]['close'] > mid
        for close in recent['close']:
            if (close > mid) != above:
                crosses += 1
                above = close > mid

        # Normalize crosses (more crosses = more ranging)
        cross_score = min(crosses / (lookback / 5), 1.0)  # Expected ~5 crosses in ranging

        # Where is current price in range?
        range_position = (current - low) / range_size if range_size > 0 else 0.5

        return {
            'range_high': round(high, 6),
            'range_low': round(low, 6),
            'range_size_pct': round(range_size / mid * 100, 3) if mid > 0 else 0,
            'midpoint_crosses': crosses,
            'cross_score': round(cross_score, 3),
            'range_position': round(range_position, 3)
        }

    def calculate_volatility_state(self, df: pd.DataFrame) -> Dict:
        """
        Determine if volatility is expanding or contracting.

        Expanding volatility = potential trend
        Contracting volatility = potential range or breakout soon
        """
        df = df.copy()
        df['atr'] = self.calculate_atr(df, self.atr_period)

        # Current ATR vs average ATR
        recent_atr = df.tail(5)['atr'].mean()
        avg_atr = df.tail(50)['atr'].mean()

        atr_ratio = recent_atr / avg_atr if avg_atr > 0 else 1.0

        if atr_ratio > self.atr_expansion_threshold:
            state = 'EXPANDING'
        elif atr_ratio < self.atr_contraction_threshold:
            state = 'CONTRACTING'
        else:
            state = 'NORMAL'

        return {
            'current_atr': round(recent_atr, 6),
            'avg_atr': round(avg_atr, 6),
            'atr_ratio': round(atr_ratio, 3),
            'volatility_state': state
        }

    def classify_regime(self, df: pd.DataFrame, symbol: str) -> Dict:
        """
        Classify the current market regime.

        Returns comprehensive regime analysis.
        """
        if len(df) < self.lookback + 10:
            return {'regime': MarketRegime.UNKNOWN.value, 'reason': 'Not enough data'}

        # Get all component analyses
        direction = self.calculate_directional_persistence(df)
        range_info = self.calculate_range_behavior(df)
        volatility = self.calculate_volatility_state(df)

        # Price vs EMA
        df = df.copy()
        df['ema'] = df['close'].ewm(span=self.ema_period, adjust=False).mean()
        current_price = df.iloc[-1]['close']
        ema = df.iloc[-1]['ema']
        price_above_ema = current_price > ema
        ema_distance_pct = (current_price - ema) / ema * 100

        # REGIME DECISION LOGIC
        regime = MarketRegime.UNKNOWN
        confidence = 0.0
        reasons = []

        # TRENDING: High directional persistence + ATR expanding
        if direction['r_squared'] > 0.5 and volatility['volatility_state'] == 'EXPANDING':
            if direction['direction'] == 'UP':
                regime = MarketRegime.TRENDING_UP
            else:
                regime = MarketRegime.TRENDING_DOWN
            confidence = min(direction['r_squared'] + 0.2, 1.0)
            reasons.append(f"Strong trend (R²={direction['r_squared']:.2f})")
            reasons.append(f"Volatility expanding ({volatility['atr_ratio']:.2f}x)")

        # RANGING: Low directional persistence + high cross score + contracting ATR
        elif direction['r_squared'] < 0.3 and range_info['cross_score'] > 0.5:
            regime = MarketRegime.RANGING
            confidence = range_info['cross_score']
            reasons.append(f"No clear trend (R²={direction['r_squared']:.2f})")
            reasons.append(f"High oscillation ({range_info['midpoint_crosses']} crosses)")

        # CHOP: Low persistence + low cross score (random movement)
        elif direction['r_squared'] < 0.2 and range_info['cross_score'] < 0.3:
            regime = MarketRegime.CHOP
            confidence = 0.5
            reasons.append("No trend or range pattern")
            reasons.append("Random price movement")

        # Default: Use simpler heuristics
        else:
            # Check if clearly trending
            if direction['up_pct'] > self.trend_threshold:
                regime = MarketRegime.TRENDING_UP
                confidence = direction['up_pct']
                reasons.append(f"{direction['up_pct']*100:.0f}% up bars")
            elif direction['down_pct'] > self.trend_threshold:
                regime = MarketRegime.TRENDING_DOWN
                confidence = direction['down_pct']
                reasons.append(f"{direction['down_pct']*100:.0f}% down bars")
            # Check if clearly ranging
            elif range_info['cross_score'] > 0.4:
                regime = MarketRegime.RANGING
                confidence = 0.5
                reasons.append("Oscillating around midpoint")
            else:
                regime = MarketRegime.CHOP
                confidence = 0.3
                reasons.append("No clear pattern detected")

        result = {
            'symbol': symbol,
            'timestamp': str(df.index[-1]),
            'regime': regime.value,
            'confidence': round(confidence, 3),
            'reasons': reasons,
            'direction_analysis': direction,
            'range_analysis': range_info,
            'volatility_analysis': volatility,
            'ema_analysis': {
                'price_above_ema': price_above_ema,
                'ema_distance_pct': round(ema_distance_pct, 3)
            }
        }

        return result

    def log_regime(self, regime_data: Dict):
        """Log regime classification"""
        self.history.append(regime_data)

        # Keep only last 1000 records
        if len(self.history) > 1000:
            self.history = self.history[-1000:]

        self._save_data()

        logger.info(f"📊 REGIME: {regime_data['symbol']} = {regime_data['regime']}")
        logger.info(f"   Confidence: {regime_data['confidence']*100:.0f}%")
        for reason in regime_data['reasons']:
            logger.info(f"   - {reason}")

    def get_current_regime(self, df: pd.DataFrame, symbol: str, log: bool = True) -> Dict:
        """Get and optionally log current regime"""
        regime = self.classify_regime(df, symbol)
        if log:
            self.log_regime(regime)
        return regime

    def get_trading_recommendation(self, regime: Dict) -> Dict:
        """
        Based on regime, recommend what strategy to use.
        """
        regime_type = regime['regime']
        confidence = regime['confidence']

        recommendations = {
            MarketRegime.TRENDING_UP.value: {
                'action': 'TRADE_LONG',
                'strategy': 'Breakout/Momentum',
                'advice': 'Look for pullbacks to EMA, buy breakouts',
                'avoid': 'Mean reversion shorts'
            },
            MarketRegime.TRENDING_DOWN.value: {
                'action': 'AVOID_LONGS',
                'strategy': 'Stay out or short',
                'advice': 'Wait for trend reversal or short rallies',
                'avoid': 'Buying dips'
            },
            MarketRegime.RANGING.value: {
                'action': 'MEAN_REVERSION',
                'strategy': 'Buy support, sell resistance',
                'advice': 'Trade range boundaries, use tight stops',
                'avoid': 'Breakout trades (likely fake)'
            },
            MarketRegime.CHOP.value: {
                'action': 'STAY_OUT',
                'strategy': 'No trading',
                'advice': 'Wait for clear regime to develop',
                'avoid': 'Any trading - no edge in chop'
            },
            MarketRegime.UNKNOWN.value: {
                'action': 'WAIT',
                'strategy': 'Gather more data',
                'advice': 'Need more price data to classify',
                'avoid': 'Trading without clarity'
            }
        }

        rec = recommendations.get(regime_type, recommendations[MarketRegime.UNKNOWN.value])
        rec['regime'] = regime_type
        rec['confidence'] = confidence

        # Adjust confidence requirement
        if confidence < 0.4:
            rec['warning'] = 'Low confidence - regime unclear'

        return rec

    def get_statistics(self) -> Dict:
        """Get regime statistics from history"""
        if not self.history:
            return {}

        df = pd.DataFrame(self.history)

        stats = {
            'total_classifications': len(df),
            'regime_distribution': df['regime'].value_counts().to_dict(),
            'avg_confidence': round(df['confidence'].mean(), 3)
        }

        # By symbol
        stats['by_symbol'] = {}
        for symbol in df['symbol'].unique():
            sym_df = df[df['symbol'] == symbol]
            stats['by_symbol'][symbol] = {
                'count': len(sym_df),
                'regime_distribution': sym_df['regime'].value_counts().to_dict(),
                'avg_confidence': round(sym_df['confidence'].mean(), 3)
            }

        return stats

    def print_summary(self):
        """Print regime summary"""
        stats = self.get_statistics()

        print("\n" + "="*70)
        print("REGIME CLASSIFIER SUMMARY")
        print("="*70)

        if not stats:
            print("No regime data yet")
            return

        print(f"\nTotal classifications: {stats['total_classifications']}")
        print(f"Average confidence: {stats['avg_confidence']*100:.1f}%")

        print("\n📊 REGIME DISTRIBUTION:")
        for regime, count in stats['regime_distribution'].items():
            pct = count / stats['total_classifications'] * 100
            print(f"   {regime}: {count} ({pct:.1f}%)")

        print("\n📈 BY SYMBOL:")
        for symbol, sym_stats in stats['by_symbol'].items():
            print(f"\n   {symbol}:")
            for regime, count in sym_stats['regime_distribution'].items():
                print(f"      {regime}: {count}")


# Standalone test
if __name__ == "__main__":
    import sys
    sys.path.insert(0, '/home/user/scalperbot')
    import ccxt

    print("Testing Regime Classifier...")

    exchange = ccxt.mexc({'enableRateLimit': True})
    symbols = ['BNB/USDT', 'XRP/USDT', 'XLM/USDT']

    classifier = RegimeClassifier()

    for symbol in symbols:
        print(f"\n{'='*70}")
        print(f"Analyzing {symbol}...")

        since = int((datetime.now(timezone.utc) - timedelta(days=1)).timestamp() * 1000)
        candles = exchange.fetch_ohlcv(symbol, '1m', since=since, limit=1000)

        df = pd.DataFrame(candles, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms', utc=True)
        df.set_index('timestamp', inplace=True)

        regime = classifier.get_current_regime(df, symbol, log=False)

        print(f"\n   REGIME: {regime['regime']}")
        print(f"   Confidence: {regime['confidence']*100:.0f}%")
        print(f"   Reasons:")
        for reason in regime['reasons']:
            print(f"      - {reason}")

        rec = classifier.get_trading_recommendation(regime)
        print(f"\n   📋 RECOMMENDATION:")
        print(f"      Action: {rec['action']}")
        print(f"      Strategy: {rec['strategy']}")
        print(f"      Advice: {rec['advice']}")
        print(f"      Avoid: {rec['avoid']}")
