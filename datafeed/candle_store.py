import pandas as pd
from collections import defaultdict

class CandleStore:
    def __init__(self):
        self.store = defaultdict(lambda: pd.DataFrame(columns=['open','high','low','close','volume','ts']))

    def update(self, symbol: str, kline: list):
        ts = pd.to_datetime(kline[0], unit='ms')
        row = {
            'open': float(kline[1]),
            'high': float(kline[2]),
            'low': float(kline[3]),
            'close': float(kline[4]),
            'volume': float(kline[5]),
            'ts': ts
        }
        df = self.store[symbol]
        if df.empty:
            df = pd.DataFrame([row])
            df.set_index('ts', inplace=True)
        else:
            if df.index[-1] == ts:
                df.iloc[-1] = row
            else:
                new_row = pd.DataFrame([row])
                new_row.set_index('ts', inplace=True)
                df = pd.concat([df, new_row])
        self.store[symbol] = df.tail(2000)

    def ohlcv(self, symbol: str, timeframe: str = '1m') -> pd.DataFrame:
        df = self.store[symbol].copy()
        if df.empty or not isinstance(df.index, pd.DatetimeIndex):
            return pd.DataFrame()
        if timeframe == '1m':
            return df
        rule = {'5m': '5min', '30m': '30min', '1h': '60min'}.get(timeframe, '1min')
        return df.resample(rule).agg({
            'open': 'first',
            'high': 'max',
            'low': 'min',
            'close': 'last',
            'volume': 'sum'
        }).dropna()
