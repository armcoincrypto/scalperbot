"""
Smart Breakout Strategy
Improved momentum breakout with higher timeframe confirmation

Key Improvements over original:
1. Higher timeframe trend filter (1H EMA)
2. RSI momentum confirmation (avoid overbought)
3. Volume must confirm direction (green candles)
4. Breakout confirmation (wait for retest)
5. ATR-based dynamic TP/SL
6. Market regime detection
"""
import pandas as pd
import numpy as np
from typing import Optional, Dict, Any, Tuple
import logging
from datafeed.candle_store import CandleStore
from config import settings

logger = logging.getLogger(__name__)


class SmartBreakoutStrategy:
    """
    Smart Breakout Strategy with multi-timeframe confirmation
    """

    def __init__(self, candle_store: CandleStore):
        self.candle_store = candle_store

        # Strategy parameters
        self.ema_period = 20  # For trend detection
        self.rsi_period = 14
        self.atr_period = 14
        self.volume_multiplier = 0.3  # Volume must be 0.3x average (lowered for more trades)
        self.breakout_lookback = 20  # Look for breakout over 20 bars
        self.rsi_oversold = 35  # Widened from 40
        self.rsi_overbought = 75  # Widened from 70
        self.htf_rsi_limit = 80  # Higher timeframe RSI limit (raised from 75)

    def calculate_ema(self, series: pd.Series, period: int) -> pd.Series:
        """Calculate Exponential Moving Average"""
        return series.ewm(span=period, adjust=False).mean()

    def calculate_rsi(self, df: pd.DataFrame, period: int = 14) -> pd.Series:
        """Calculate RSI with protection against division by zero"""
        delta = df['close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
        # Replace 0 loss with small value to avoid division by zero
        # When loss is 0, RSI should be 100 (all gains)
        loss = loss.replace(0, 1e-10)
        rs = gain / loss
        rsi = 100 - (100 / (1 + rs))
        # Fill any NaN/Inf values with neutral RSI
        rsi = rsi.fillna(50).replace([np.inf, -np.inf], 50)
        return rsi

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

    def check_higher_timeframe_trend(self, symbol: str) -> Tuple[bool, str, Dict]:
        """
        FILTER 1: Check 1H timeframe trend
        - Price near or above EMA(20) - allow within 1% below
        - RSI not overbought
        """
        df_1h = self.candle_store.get_candles(symbol, '1h', limit=50)

        if df_1h.empty or len(df_1h) < 25:
            return False, "HTF: Not enough 1H data", {}

        df_1h = df_1h.copy()
        df_1h['ema20'] = self.calculate_ema(df_1h['close'], 20)
        df_1h['rsi'] = self.calculate_rsi(df_1h, 14)

        current_price = df_1h.iloc[-1]['close']
        current_ema = df_1h.iloc[-1]['ema20']
        htf_rsi = df_1h.iloc[-1]['rsi']

        # Allow price within 1% below EMA (more lenient)
        price_near_ema = current_price >= current_ema * 0.99
        rsi_ok = htf_rsi < self.htf_rsi_limit

        trend_ok = price_near_ema and rsi_ok

        distance_pct = ((current_price - current_ema) / current_ema) * 100
        msg = (f"HTF: Price={current_price:.4f}, EMA20={current_ema:.4f} ({distance_pct:+.2f}%), "
               f"RSI={htf_rsi:.1f}, {'OK ✅' if trend_ok else 'DOWN ❌'}")

        data = {
            'htf_ema': current_ema,
            'htf_rsi': htf_rsi,
            'htf_trend': 'OK' if trend_ok else 'DOWN'
        }

        return trend_ok, msg, data

    def check_momentum_rsi(self, df: pd.DataFrame) -> Tuple[bool, str]:
        """
        FILTER 2: RSI momentum confirmation (SIMPLIFIED per expert advice)
        - RSI > 40 only (not oversold)
        - RSI < 75 (not overbought)
        - NO "rising" requirement - too noisy on 1m
        """
        df = df.copy()
        df['rsi'] = self.calculate_rsi(df, self.rsi_period)

        current_rsi = df.iloc[-1]['rsi']

        # Simplified: just check RSI is in tradeable range
        # RSI > 40 means not oversold (buyers present)
        # RSI < 75 means not overbought (room to grow)
        in_range = 40 <= current_rsi <= 75

        passed = in_range

        msg = (f"RSI: {current_rsi:.1f} (need 40-75), "
               f"{'IN RANGE ✅' if in_range else 'OUT OF RANGE ❌'}")

        return passed, msg

    def check_volume_confirms_direction(self, df: pd.DataFrame) -> Tuple[bool, str]:
        """
        FILTER 3: Volume OR green candle (relaxed for more trades)
        - Volume > 0.3x average OR candle is green
        - Much more permissive to allow more trading opportunities
        """
        df = df.copy()
        df['vol_avg'] = df['volume'].rolling(window=20).mean()

        current_vol = df.iloc[-1]['volume']
        vol_avg = df.iloc[-1]['vol_avg']
        vol_ratio = current_vol / vol_avg if vol_avg > 0 else 0

        is_green_candle = df.iloc[-1]['close'] > df.iloc[-1]['open']
        volume_surge = vol_ratio >= self.volume_multiplier

        # RELAXED: Pass if EITHER volume is high OR candle is green
        passed = volume_surge or is_green_candle

        msg = (f"VOLUME: {vol_ratio:.2f}x avg (need {self.volume_multiplier}x), "
               f"Candle={'GREEN ✅' if is_green_candle else 'RED ❌'}, "
               f"{'CONFIRMED ✅' if passed else 'NOT CONFIRMED ❌'}")

        return passed, msg

    def check_pullback_entry(self, df: pd.DataFrame) -> Tuple[bool, str, Dict]:
        """
        FILTER 4: Pullback entry (REPLACED breakout per expert advice)
        - Price pulled back to near EMA20
        - Current candle is green (buyers stepping in)
        - Much higher pass rate than breakout confirmation
        """
        if len(df) < 25:
            return False, "PULLBACK: Not enough data", {}

        df = df.copy()
        df['ema20'] = self.calculate_ema(df['close'], 20)

        current_candle = df.iloc[-1]
        current_price = current_candle['close']
        ema20 = df.iloc[-1]['ema20']

        # Price should be within 0.5% of EMA20 (pullback zone)
        distance_pct = ((current_price - ema20) / ema20) * 100
        near_ema = -0.3 <= distance_pct <= 0.5  # Allow slightly below to slightly above

        # Current candle should be green (buyers stepping in)
        is_green = current_candle['close'] > current_candle['open']

        # Either near EMA OR green candle (very permissive)
        passed = near_ema or is_green

        msg = (f"PULLBACK: Price {distance_pct:+.2f}% from EMA20, "
               f"Candle={'GREEN ✅' if is_green else 'RED ❌'}, "
               f"{'CONFIRMED ✅' if passed else 'NOT CONFIRMED ❌'}")

        data = {
            'ema20': ema20,
            'distance_pct': distance_pct
        }

        return passed, msg, data

    def check_not_extended(self, df: pd.DataFrame) -> Tuple[bool, str]:
        """
        FILTER 5: Price not too extended from EMA (RELAXED per expert advice)
        - Allow up to 5 ATR from EMA (was 2 ATR)
        - This filter was blocking good trades after breakouts
        """
        df = df.copy()
        df['ema20'] = self.calculate_ema(df['close'], 20)
        df['atr'] = self.calculate_atr(df, 14)

        current_price = df.iloc[-1]['close']
        ema = df.iloc[-1]['ema20']
        atr = df.iloc[-1]['atr']

        # Price should not be more than 5 ATR above EMA (relaxed from 2)
        distance_from_ema = (current_price - ema) / atr if atr > 0 else 0
        not_extended = distance_from_ema < 5.0

        msg = (f"EXTENSION: Price {distance_from_ema:.1f} ATR from EMA20 (max 5), "
               f"{'OK ✅' if not_extended else 'TOO EXTENDED ❌'}")

        return not_extended, msg

    def calculate_dynamic_targets(self, df: pd.DataFrame, entry_price: float) -> Dict[str, float]:
        """
        Calculate ATR-based TP and SL
        """
        df = df.copy()
        df['atr'] = self.calculate_atr(df, self.atr_period)
        atr = df.iloc[-1]['atr']

        # Guard against invalid entry_price
        if not entry_price or entry_price <= 0:
            logger.warning(f"Invalid entry_price={entry_price}, using default targets")
            return {
                'atr': atr,
                'take_profit_price': 0,
                'stop_loss_price': 0,
                'take_profit_pct': 2.0,
                'stop_loss_pct': 1.0,
                'risk_reward': 2.0
            }

        # TP = 2.5 ATR, SL = 1.5 ATR (WIDENED per expert advice)
        # Wider SL avoids getting stopped out by noise
        # Target: SL ~0.35-0.45%, TP ~0.5-0.8%
        tp_price = entry_price + (2.5 * atr)
        sl_price = entry_price - (1.5 * atr)

        # Calculate percentages
        tp_pct = ((tp_price - entry_price) / entry_price) * 100
        sl_pct = ((entry_price - sl_price) / entry_price) * 100

        # CRITICAL: Enforce minimum percentages per expert advice
        # ATR on 1m candles can be tiny, causing TP/SL to be within noise
        # Expert says: SL should be 0.35-0.45%, TP should be 0.5-0.8%
        MIN_SL_PCT = 0.40  # Minimum stop loss percentage
        MIN_TP_PCT = 0.60  # Minimum take profit percentage

        if sl_pct < MIN_SL_PCT:
            sl_pct = MIN_SL_PCT
            sl_price = entry_price * (1 - MIN_SL_PCT / 100)
            logger.info(f"  📏 SL was {((entry_price - (entry_price - 1.5*atr)) / entry_price * 100):.2f}%, enforcing minimum {MIN_SL_PCT}%")

        if tp_pct < MIN_TP_PCT:
            tp_pct = MIN_TP_PCT
            tp_price = entry_price * (1 + MIN_TP_PCT / 100)
            logger.info(f"  📏 TP was {((entry_price + 2.5*atr - entry_price) / entry_price * 100):.2f}%, enforcing minimum {MIN_TP_PCT}%")

        return {
            'atr': atr,
            'take_profit_price': tp_price,
            'stop_loss_price': sl_price,
            'take_profit_pct': tp_pct,
            'stop_loss_pct': sl_pct,
            'risk_reward': tp_pct / sl_pct if sl_pct > 0 else 0
        }

    def generate_signal(self, symbol: str) -> Optional[Dict[str, Any]]:
        """
        Generate trading signal with all filters
        """
        # Get 1m candles for primary analysis (matches backtest optimization)
        df_5m = self.candle_store.get_candles(symbol, '1m', limit=100)

        if df_5m.empty or len(df_5m) < 30:
            logger.debug(f"{symbol}: Not enough 5m data")
            return None

        # Run all filters
        logger.info(f"\n{'='*70}")
        logger.info(f"📊 {symbol} SMART BREAKOUT Check:")
        logger.info(f"{'='*70}")

        # Filter 1: Higher Timeframe Trend
        htf_pass, htf_msg, htf_data = self.check_higher_timeframe_trend(symbol)
        logger.info(f"  [1] {htf_msg}")
        if not htf_pass:
            logger.info(f"  ❌ REJECTED: Higher timeframe trend not bullish")
            logger.info(f"{'='*70}\n")
            return None

        # Filter 2: RSI Momentum
        rsi_pass, rsi_msg = self.check_momentum_rsi(df_5m)
        logger.info(f"  [2] {rsi_msg}")
        if not rsi_pass:
            logger.info(f"  ❌ REJECTED: RSI conditions not met")
            logger.info(f"{'='*70}\n")
            return None

        # Filter 3: Volume Confirmation
        vol_pass, vol_msg = self.check_volume_confirms_direction(df_5m)
        logger.info(f"  [3] {vol_msg}")
        if not vol_pass:
            logger.info(f"  ❌ REJECTED: Volume does not confirm direction")
            logger.info(f"{'='*70}\n")
            return None

        # Filter 4: Pullback Entry (REPLACED breakout per expert advice)
        pullback_pass, pullback_msg, pullback_data = self.check_pullback_entry(df_5m)
        logger.info(f"  [4] {pullback_msg}")
        if not pullback_pass:
            logger.info(f"  ❌ REJECTED: Pullback entry not confirmed")
            logger.info(f"{'='*70}\n")
            return None

        # Filter 5: Not Extended
        ext_pass, ext_msg = self.check_not_extended(df_5m)
        logger.info(f"  [5] {ext_msg}")
        if not ext_pass:
            logger.info(f"  ❌ REJECTED: Price too extended")
            logger.info(f"{'='*70}\n")
            return None

        # All filters passed!
        entry_price = df_5m.iloc[-1]['close']
        targets = self.calculate_dynamic_targets(df_5m, entry_price)

        logger.info(f"  ✅ ALL FILTERS PASSED!")
        logger.info(f"  📈 Entry: {entry_price:.4f}")
        logger.info(f"  🎯 TP: {targets['take_profit_price']:.4f} (+{targets['take_profit_pct']:.2f}%)")
        logger.info(f"  🛑 SL: {targets['stop_loss_price']:.4f} (-{targets['stop_loss_pct']:.2f}%)")
        logger.info(f"  ⚖️  R:R = {targets['risk_reward']:.1f}")
        logger.info(f"{'='*70}\n")

        signal = {
            'symbol': symbol,
            'action': 'BUY',
            'price': entry_price,
            'timestamp': df_5m.iloc[-1]['timestamp'],
            'reason': 'Smart Pullback - All 5 filters passed',
            'targets': targets,
            'htf_data': htf_data,
            'pullback_data': pullback_data,
            'filters': {
                'htf_trend': htf_msg,
                'rsi_momentum': rsi_msg,
                'volume_confirm': vol_msg,
                'pullback_entry': pullback_msg,
                'not_extended': ext_msg
            }
        }

        return signal

    def run_for_all_symbols(self, symbols: list) -> list:
        """
        Run strategy for all symbols
        """
        signals = []

        for symbol in symbols:
            try:
                signal = self.generate_signal(symbol)
                if signal:
                    signals.append(signal)
            except Exception as e:
                logger.error(f"❌ Error in Smart Breakout for {symbol}: {e}", exc_info=True)

        if signals:
            logger.info(f"✅ Smart Breakout generated {len(signals)} signals")

        return signals
