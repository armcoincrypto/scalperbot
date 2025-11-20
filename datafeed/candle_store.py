"""
Candle Store - OHLCV data storage and resampling
Accumulates 1m candles and resamples to higher timeframes
"""
import pandas as pd
from typing import Dict, List, Optional
from datetime import datetime, timedelta
import logging

logger = logging.getLogger(__name__)


class CandleStore:
    """
    Stores and manages OHLCV candle data
    - Accumulates 1m candles
    - Resamples to 5m, 15m, etc.
    - Provides historical data for strategy
    """

    def __init__(self):
        # Store 1m candles as DataFrames: {symbol: DataFrame}
        self.candles_1m: Dict[str, pd.DataFrame] = {}

        # Store resampled candles: {(symbol, timeframe): DataFrame}
        self.resampled: Dict[tuple, pd.DataFrame] = {}

        # Max candles to keep in memory (1440 = 24 hours of 1m data)
        self.max_candles = 1440

    def add_candle(
        self,
        symbol: str,
        timestamp: int,
        open_price: float,
        high: float,
        low: float,
        close: float,
        volume: float
    ):
        """Add a new 1m candle"""
        candle_data = {
            'timestamp': timestamp,
            'open': open_price,
            'high': high,
            'low': low,
            'close': close,
            'volume': volume
        }

        if symbol not in self.candles_1m:
            self.candles_1m[symbol] = pd.DataFrame([candle_data])
        else:
            # Check if candle already exists (avoid duplicates)
            df = self.candles_1m[symbol]
            if timestamp not in df['timestamp'].values:
                new_row = pd.DataFrame([candle_data])
                self.candles_1m[symbol] = pd.concat([df, new_row], ignore_index=True)

                # Keep only recent candles
                if len(self.candles_1m[symbol]) > self.max_candles:
                    self.candles_1m[symbol] = self.candles_1m[symbol].iloc[-self.max_candles:]

        logger.debug(f"Added 1m candle for {symbol}: {datetime.fromtimestamp(timestamp/1000)}")

    def add_candles_bulk(self, symbol: str, ohlcv_list: List[List]):
        """
        Add multiple candles at once (from historical fetch)
        ohlcv_list: List of [timestamp, open, high, low, close, volume]
        """
        if not ohlcv_list:
            return

        df = pd.DataFrame(ohlcv_list, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])

        if symbol not in self.candles_1m:
            self.candles_1m[symbol] = df
        else:
            # Merge with existing, remove duplicates
            existing = self.candles_1m[symbol]
            combined = pd.concat([existing, df], ignore_index=True)
            combined = combined.drop_duplicates(subset='timestamp', keep='last')
            combined = combined.sort_values('timestamp').reset_index(drop=True)

            # Keep only recent candles
            if len(combined) > self.max_candles:
                combined = combined.iloc[-self.max_candles:]

            self.candles_1m[symbol] = combined

        logger.info(f"Added {len(ohlcv_list)} bulk candles for {symbol}. Total: {len(self.candles_1m[symbol])}")

    def get_candles(self, symbol: str, timeframe: str = '1m', limit: Optional[int] = None) -> pd.DataFrame:
        """
        Get candles for a symbol and timeframe
        timeframe: '1m', '5m', '15m', '1h', etc.
        """
        if timeframe == '1m':
            df = self.candles_1m.get(symbol, pd.DataFrame())
            if limit and not df.empty:
                return df.iloc[-limit:].copy()
            return df.copy()

        # For higher timeframes, resample from 1m data
        return self._resample_candles(symbol, timeframe, limit)

    def _resample_candles(self, symbol: str, timeframe: str, limit: Optional[int] = None) -> pd.DataFrame:
        """Resample 1m candles to higher timeframes"""
        if symbol not in self.candles_1m or self.candles_1m[symbol].empty:
            return pd.DataFrame()

        df = self.candles_1m[symbol].copy()

        # Convert timestamp to datetime
        df['datetime'] = pd.to_datetime(df['timestamp'], unit='ms')
        df = df.set_index('datetime')

        # Map timeframe to pandas resample rule
        resample_map = {
            '5m': '5min',
            '15m': '15min',
            '30m': '30min',
            '1h': '1h',
            '4h': '4h',
            '1d': '1d'
        }

        if timeframe not in resample_map:
            logger.warning(f"Unsupported timeframe: {timeframe}, returning 1m data")
            return df.reset_index()

        rule = resample_map[timeframe]

        # Resample OHLCV
        resampled = df.resample(rule).agg({
            'open': 'first',
            'high': 'max',
            'low': 'min',
            'close': 'last',
            'volume': 'sum',
            'timestamp': 'first'
        }).dropna()

        resampled = resampled.reset_index()

        if limit:
            resampled = resampled.iloc[-limit:]

        logger.debug(f"Resampled {symbol} to {timeframe}: {len(resampled)} candles")
        return resampled

    def has_sufficient_data(self, symbol: str, timeframe: str, min_candles: int) -> bool:
        """Check if we have enough candles for analysis"""
        df = self.get_candles(symbol, timeframe)
        sufficient = len(df) >= min_candles

        if not sufficient:
            logger.debug(f"{symbol} {timeframe}: Have {len(df)}/{min_candles} candles")

        return sufficient

    def initialize_historical(self, symbol: str, ohlcv_data: List[List]):
        """
        Initialize with historical data on startup
        Speeds up warmup by fetching historical candles from exchange
        """
        self.add_candles_bulk(symbol, ohlcv_data)
        logger.info(f"Initialized {symbol} with {len(ohlcv_data)} historical candles")

    def get_latest_price(self, symbol: str) -> Optional[float]:
        """Get the latest close price"""
        if symbol not in self.candles_1m or self.candles_1m[symbol].empty:
            return None
        return self.candles_1m[symbol].iloc[-1]['close']

    def summary(self) -> str:
        """Get summary of stored data"""
        lines = ["📊 CandleStore Summary:"]
        for symbol, df in self.candles_1m.items():
            if not df.empty:
                lines.append(f"  {symbol}: {len(df)} 1m candles")
        return "\n".join(lines)
