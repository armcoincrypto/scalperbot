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
        self.volume_multiplier = 1.5  # Volume must be 1.5x average
        self.breakout_lookback = 20  # Look for breakout over 20 bars
        self.rsi_oversold = 40
        self.rsi_overbought = 70
        self.htf_rsi_limit = 75  # Higher timeframe RSI limit

    def calculate_ema(self, series: pd.Series, period: int) -> pd.Series:
        """Calculate Exponential Moving Average"""
        return series.ewm(span=period, adjust=False).mean()

    def calculate_rsi(self, df: pd.DataFrame, period: int = 14) -> pd.Series:
        """Calculate RSI"""
        delta = df['close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
        rs = gain / loss
        rsi = 100 - (100 / (1 + rs))
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
        - Price above EMA(20)
        - EMA slope positive
        """
        df_1h = self.candle_store.get_candles(symbol, '1h', limit=50)

        if df_1h.empty or len(df_1h) < 25:
            return False, "HTF: Not enough 1H data", {}

        df_1h = df_1h.copy()
        df_1h['ema20'] = self.calculate_ema(df_1h['close'], 20)
        df_1h['rsi'] = self.calculate_rsi(df_1h, 14)

        current_price = df_1h.iloc[-1]['close']
        current_ema = df_1h.iloc[-1]['ema20']
        prev_ema = df_1h.iloc[-2]['ema20']
        htf_rsi = df_1h.iloc[-1]['rsi']

        price_above_ema = current_price > current_ema
        ema_rising = current_ema > prev_ema
        rsi_ok = htf_rsi < self.htf_rsi_limit

        trend_up = price_above_ema and ema_rising and rsi_ok

        msg = (f"HTF: Price={current_price:.4f}, EMA20={current_ema:.4f}, "
               f"RSI={htf_rsi:.1f}, Trend={'UP ✅' if trend_up else 'DOWN ❌'}")

        data = {
            'htf_ema': current_ema,
            'htf_rsi': htf_rsi,
            'htf_trend': 'UP' if trend_up else 'DOWN'
        }

        return trend_up, msg, data

    def check_momentum_rsi(self, df: pd.DataFrame) -> Tuple[bool, str]:
        """
        FILTER 2: RSI momentum confirmation
        - RSI between 40-70 (not overbought, room to grow)
        - RSI trending up
        """
        df = df.copy()
        df['rsi'] = self.calculate_rsi(df, self.rsi_period)

        current_rsi = df.iloc[-1]['rsi']
        rsi_3bars_ago = df.iloc[-4]['rsi'] if len(df) >= 4 else current_rsi

        in_range = self.rsi_oversold <= current_rsi <= self.rsi_overbought
        trending_up = current_rsi > rsi_3bars_ago

        passed = in_range and trending_up

        msg = (f"RSI: {current_rsi:.1f} (range {self.rsi_oversold}-{self.rsi_overbought}), "
               f"3bars_ago={rsi_3bars_ago:.1f}, "
               f"{'IN RANGE ✅' if in_range else 'OUT OF RANGE ❌'}, "
               f"{'RISING ✅' if trending_up else 'FALLING ❌'}")

        return passed, msg

    def check_volume_confirms_direction(self, df: pd.DataFrame) -> Tuple[bool, str]:
        """
        FILTER 3: Volume confirms bullish direction
        - Volume > 1.5x 20-period average
        - Candle is green (close > open)
        """
        df = df.copy()
        df['vol_avg'] = df['volume'].rolling(window=20).mean()

        current_vol = df.iloc[-1]['volume']
        vol_avg = df.iloc[-1]['vol_avg']
        vol_ratio = current_vol / vol_avg if vol_avg > 0 else 0

        is_green_candle = df.iloc[-1]['close'] > df.iloc[-1]['open']
        volume_surge = vol_ratio >= self.volume_multiplier

        passed = volume_surge and is_green_candle

        msg = (f"VOLUME: {vol_ratio:.2f}x avg (need {self.volume_multiplier}x), "
               f"Candle={'GREEN ✅' if is_green_candle else 'RED ❌'}, "
               f"{'CONFIRMED ✅' if passed else 'NOT CONFIRMED ❌'}")

        return passed, msg

    def check_breakout_with_confirmation(self, df: pd.DataFrame) -> Tuple[bool, str, Dict]:
        """
        FILTER 4: Breakout with confirmation
        - Previous candle broke above resistance
        - Current candle holds above breakout level
        """
        if len(df) < self.breakout_lookback + 2:
            return False, "BREAKOUT: Not enough data", {}

        # Find resistance level (highest high of lookback period, excluding last 2 candles)
        lookback = df.iloc[-(self.breakout_lookback + 2):-2]
        resistance = lookback['high'].max()

        prev_candle = df.iloc[-2]
        current_candle = df.iloc[-1]

        # Previous candle broke above resistance
        prev_broke_out = prev_candle['close'] > resistance

        # Current candle holds above (confirmation)
        current_holds = current_candle['low'] > resistance * 0.998  # Allow 0.2% tolerance

        # Current price still above resistance
        price_above = current_candle['close'] > resistance

        passed = prev_broke_out and current_holds and price_above

        msg = (f"BREAKOUT: Resistance={resistance:.4f}, "
               f"PrevClose={prev_candle['close']:.4f}, "
               f"CurrentLow={current_candle['low']:.4f}, "
               f"{'CONFIRMED ✅' if passed else 'NOT CONFIRMED ❌'}")

        data = {
            'resistance': resistance,
            'breakout_candle_close': prev_candle['close']
        }

        return passed, msg, data

    def check_not_extended(self, df: pd.DataFrame) -> Tuple[bool, str]:
        """
        FILTER 5: Price not too extended from EMA
        - Prevents buying when price already far from mean
        """
        df = df.copy()
        df['ema20'] = self.calculate_ema(df['close'], 20)
        df['atr'] = self.calculate_atr(df, 14)

        current_price = df.iloc[-1]['close']
        ema = df.iloc[-1]['ema20']
        atr = df.iloc[-1]['atr']

        # Price should not be more than 2 ATR above EMA
        distance_from_ema = (current_price - ema) / atr if atr > 0 else 0
        not_extended = distance_from_ema < 2.0

        msg = (f"EXTENSION: Price {distance_from_ema:.1f} ATR from EMA20, "
               f"{'OK ✅' if not_extended else 'TOO EXTENDED ❌'}")

        return not_extended, msg

    def calculate_dynamic_targets(self, df: pd.DataFrame, entry_price: float) -> Dict[str, float]:
        """
        Calculate ATR-based TP and SL
        """
        df = df.copy()
        df['atr'] = self.calculate_atr(df, self.atr_period)
        atr = df.iloc[-1]['atr']

        # TP = 2 ATR, SL = 1 ATR
        tp_price = entry_price + (2 * atr)
        sl_price = entry_price - (1 * atr)

        # Calculate percentages
        tp_pct = ((tp_price - entry_price) / entry_price) * 100
        sl_pct = ((entry_price - sl_price) / entry_price) * 100

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
        # Get 5m candles for primary analysis
        df_5m = self.candle_store.get_candles(symbol, '5m', limit=100)

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

        # Filter 4: Breakout Confirmation
        breakout_pass, breakout_msg, breakout_data = self.check_breakout_with_confirmation(df_5m)
        logger.info(f"  [4] {breakout_msg}")
        if not breakout_pass:
            logger.info(f"  ❌ REJECTED: Breakout not confirmed")
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
            'reason': 'Smart Breakout - All 5 filters passed',
            'targets': targets,
            'htf_data': htf_data,
            'breakout_data': breakout_data,
            'filters': {
                'htf_trend': htf_msg,
                'rsi_momentum': rsi_msg,
                'volume_confirm': vol_msg,
                'breakout_confirm': breakout_msg,
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
