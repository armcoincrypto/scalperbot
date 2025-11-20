"""
Momentum Breakout Strategy
4-filter GREEN strategy for identifying breakout opportunities

GREEN 1: Trend check - 5m price > price 2 bars ago
GREEN 2: BB expansion - Bollinger Band width growing
GREEN 3: Volume surge - Volume Z-score > threshold
GREEN 4: Breakout - Price > 10-period high + buffer

ALL 4 filters must pass to generate a BUY signal.
"""
import pandas as pd
import numpy as np
from typing import Optional, Dict, Any
import logging
from scalperbot.datafeed.candle_store import CandleStore
from scalperbot.config import settings

logger = logging.getLogger(__name__)


class MomentumBreakoutStrategy:
    """
    Momentum Breakout Strategy with 4-filter GREEN system
    """

    def __init__(self, candle_store: CandleStore):
        self.candle_store = candle_store

        # Strategy parameters from config
        self.bb_period = settings.green2_bb_period
        self.bb_std = settings.green2_bb_std
        self.volume_threshold = settings.green3_volume_threshold
        self.volume_enabled = settings.green3_enabled
        self.breakout_period = settings.green4_breakout_period
        self.breakout_buffer_bps = settings.green4_breakout_buffer_bps

    def calculate_bollinger_bands(self, df: pd.DataFrame) -> pd.DataFrame:
        """Calculate Bollinger Bands"""
        df = df.copy()
        df['bb_middle'] = df['close'].rolling(window=self.bb_period).mean()
        df['bb_std'] = df['close'].rolling(window=self.bb_period).std()
        df['bb_upper'] = df['bb_middle'] + (self.bb_std * df['bb_std'])
        df['bb_lower'] = df['bb_middle'] - (self.bb_std * df['bb_std'])
        df['bb_width'] = df['bb_upper'] - df['bb_lower']
        return df

    def calculate_volume_zscore(self, df: pd.DataFrame, window: int = 20) -> pd.DataFrame:
        """Calculate volume Z-score"""
        df = df.copy()
        df['volume_mean'] = df['volume'].rolling(window=window).mean()
        df['volume_std'] = df['volume'].rolling(window=window).std()
        df['volume_zscore'] = (df['volume'] - df['volume_mean']) / df['volume_std']
        df['volume_zscore'] = df['volume_zscore'].fillna(0)
        return df

    def check_green1_trend(self, df: pd.DataFrame) -> tuple[bool, str]:
        """
        GREEN 1: Trend check
        5m price > price 2 bars ago (simple uptrend)
        """
        if len(df) < 3:
            return False, "GREEN 1: Not enough data"

        current_price = df.iloc[-1]['close']
        price_2bars_ago = df.iloc[-3]['close']

        trend_up = current_price > price_2bars_ago

        msg = f"GREEN 1: Price={current_price:.4f}, 2bars_ago={price_2bars_ago:.4f}, trending={'UP ✅' if trend_up else 'DOWN ❌'}"
        return trend_up, msg

    def check_green2_bb_expansion(self, df: pd.DataFrame) -> tuple[bool, str]:
        """
        GREEN 2: Bollinger Band expansion
        BB width must be increasing (volatility expanding)
        """
        if len(df) < self.bb_period + 2:
            return False, "GREEN 2: Not enough data for BB calculation"

        df = self.calculate_bollinger_bands(df)

        current_bb_width = df.iloc[-1]['bb_width']
        prev_bb_width = df.iloc[-2]['bb_width']

        expanding = current_bb_width > prev_bb_width

        msg = f"GREEN 2: BB_width={current_bb_width:.6f}, prev={prev_bb_width:.6f}, expanding={expanding}"
        return expanding, msg

    def check_green3_volume_surge(self, df: pd.DataFrame) -> tuple[bool, str]:
        """
        GREEN 3: Volume surge
        Volume Z-score > threshold (indicates unusual volume)
        """
        if not self.volume_enabled:
            return True, "GREEN 3: DISABLED (bypassed for testing)"

        if len(df) < 20:
            return False, "GREEN 3: Not enough data for volume calculation"

        df = self.calculate_volume_zscore(df)

        vol_z = df.iloc[-1]['volume_zscore']
        surge = vol_z > self.volume_threshold

        msg = f"GREEN 3: Volume_Z={vol_z:.2f}, threshold={self.volume_threshold}, surge={'YES ✅' if surge else 'NO ❌'}"
        return surge, msg

    def check_green4_breakout(self, df: pd.DataFrame) -> tuple[bool, str]:
        """
        GREEN 4: Price breakout
        Current price > highest high of last N periods + buffer
        """
        if len(df) < self.breakout_period + 1:
            return False, "GREEN 4: Not enough data for breakout calculation"

        # Get highest high of last N periods (excluding current candle)
        lookback = df.iloc[-(self.breakout_period+1):-1]
        highest_high = lookback['high'].max()

        # Add buffer (basis points)
        buffer = highest_high * (self.breakout_buffer_bps / 10000)
        breakout_level = highest_high + buffer

        current_price = df.iloc[-1]['close']

        breakout = current_price > breakout_level

        msg = f"GREEN 4: Price={current_price:.4f}, breakout_level={breakout_level:.4f}, breakout={'YES ✅' if breakout else 'NO ❌'}"
        return breakout, msg

    def generate_signal(self, symbol: str) -> Optional[Dict[str, Any]]:
        """
        Generate trading signal for a symbol
        Returns None if no signal, or dict with signal details
        """
        # Get 5m candles
        df = self.candle_store.get_candles(symbol, '5m', limit=100)

        if df.empty:
            logger.warning(f"⚠️ No candle data for {symbol}")
            return None

        # Check data sufficiency
        min_candles = max(self.bb_period, self.breakout_period, 20) + 5
        if len(df) < min_candles:
            logger.debug(f"{symbol}: Have {len(df)}/{min_candles} 5m candles - need more data")
            return None

        # Run all 4 GREEN filters
        green1_pass, green1_msg = self.check_green1_trend(df)
        green2_pass, green2_msg = self.check_green2_bb_expansion(df)
        green3_pass, green3_msg = self.check_green3_volume_surge(df)
        green4_pass, green4_msg = self.check_green4_breakout(df)

        # Log results
        logger.info(f"\n{'='*60}")
        logger.info(f"📊 {symbol} Strategy Check:")
        logger.info(f"  {green1_msg}")
        logger.info(f"  {green2_msg}")
        logger.info(f"  {green3_msg}")
        logger.info(f"  {green4_msg}")

        # All filters must pass
        all_pass = green1_pass and green2_pass and green3_pass and green4_pass

        if all_pass:
            signal = {
                'symbol': symbol,
                'action': 'BUY',
                'price': df.iloc[-1]['close'],
                'timestamp': df.iloc[-1]['timestamp'],
                'reason': 'GREEN 1-4 all passed',
                'filters': {
                    'green1': green1_msg,
                    'green2': green2_msg,
                    'green3': green3_msg,
                    'green4': green4_msg
                }
            }
            logger.info(f"🟢 SIGNAL GENERATED: {symbol} BUY @ {signal['price']:.4f}")
            logger.info(f"{'='*60}\n")
            return signal
        else:
            logger.info(f"❌ No signal - filters not all passed")
            logger.info(f"{'='*60}\n")
            return None

    def run_for_all_symbols(self, symbols: list) -> list:
        """
        Run strategy for all symbols
        Returns list of signals
        """
        signals = []

        for symbol in symbols:
            try:
                signal = self.generate_signal(symbol)
                if signal:
                    signals.append(signal)
            except Exception as e:
                logger.error(f"❌ Error running strategy for {symbol}: {e}", exc_info=True)

        if signals:
            logger.info(f"✅ Generated {len(signals)} signals")
        else:
            logger.debug(f"No signals generated this cycle")

        return signals
