import pandas as pd
from typing import Dict
import numpy as np
from ta.volatility import BollingerBands
from ta.trend import EMAIndicator
from .base import Strategy

class MomentumBreakout(Strategy):
    async def warmup(self, data: Dict[str, pd.DataFrame]):
        h1 = data.get('1h', pd.DataFrame())
        self.higher_tf = h1 if not h1.empty else data.get('30m', pd.DataFrame())
        self.df_1m = data['1m']

    def _calc_volume_z(self, df):
        vol_mean = df['volume'].rolling(50).mean()
        vol_std = df['volume'].rolling(50).std()
        return (df['volume'] - vol_mean) / vol_std

    async def generate_signal(self, data: Dict[str, pd.DataFrame]):
        df_1m = data['1m']
        if len(df_1m) < 200:
            return {}

        # Higher TF trend: EMA50 > EMA200
        ema50 = EMAIndicator(self.higher_tf['close'], window=50).ema_indicator()
        ema200 = EMAIndicator(self.higher_tf['close'], window=200).ema_indicator()
        trend_up = ema50.iloc[-1] > ema200.iloc[-1]

        # BB width expansion
        bb = BollingerBands(df_1m['close'], window=20, window_dev=2)
        bb_width = (bb.bollinger_hband() - bb.bollinger_lband()) / bb.bollinger_mavg()
        bb_expanding = bb_width.iloc[-1] > bb_width.iloc[-2]

        # Volume z-score
        vol_z = self._calc_volume_z(df_1m).iloc[-1]
        volume_surge = vol_z > self.config.vol_z

        # Break prior swing high
        swing_high = df_1m['high'].rolling(10).max().shift(1).iloc[-1]
        breakout = df_1m['close'].iloc[-1] > swing_high * (1 + 10 / 10000)

        if trend_up and bb_expanding and volume_surge and breakout:
            return {
                'side': 'buy',
                'tp': df_1m['close'].iloc[-1] * (1 + self.config.tp_bps / 10000),
                'sl': df_1m['close'].iloc[-1] * (1 - self.config.sl_bps / 10000),
                'trail': self.config.trail_bps / 10000
            }
        return {}
