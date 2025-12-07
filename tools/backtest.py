#!/usr/bin/env python3
"""
Backtesting Tool for ScalperBot
Supports multiple timeframes (1m, 5m, 15m, 1H) and strategies
Uses CCXT (MEXC/Binance) for historical data
"""
import argparse
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from typing import Dict, List, Tuple, Optional
import time
import sys

# Add parent directory to path
sys.path.insert(0, '/home/user/scalperbot')

# Try to import ccxt for exchange data
try:
    import ccxt
    HAS_CCXT = True
except ImportError:
    HAS_CCXT = False
    print("Warning: ccxt not installed. Install with: pip install ccxt")


class Backtester:
    """
    Multi-timeframe backtesting engine
    """

    def __init__(self, strategy_mode: str = 'smart', timeframe: str = '15m', exchange: str = 'mexc'):
        self.strategy_mode = strategy_mode
        self.timeframe = timeframe
        self.exchange_name = exchange

        # ATR-based TP/SL (adjustable)
        self.tp_atr_mult = 2.0  # Take profit = 2 ATR (more realistic)
        self.sl_atr_mult = 1.0  # Stop loss = 1 ATR
        self.trailing_start_atr = 1.0  # Start trailing at 1 ATR profit
        self.trailing_step_atr = 0.5   # Trail by 0.5 ATR

        # Risk management
        self.risk_per_trade_pct = 0.5  # 0.5% of equity per trade
        self.max_positions = 3

        # Timeframe settings
        self.tf_minutes = {
            '1m': 1, '5m': 5, '15m': 15, '30m': 30, '1h': 60, '4h': 240
        }

        # Initialize CCXT exchange
        self.exchange = None
        if HAS_CCXT:
            try:
                if exchange == 'mexc':
                    self.exchange = ccxt.mexc({'enableRateLimit': True})
                elif exchange == 'binance':
                    self.exchange = ccxt.binance({'enableRateLimit': True})
                else:
                    self.exchange = ccxt.mexc({'enableRateLimit': True})
                print(f"Using {exchange.upper()} for historical data")
            except Exception as e:
                print(f"Error initializing exchange: {e}")

    def fetch_data(self, symbol: str, timeframe: str, days: int) -> pd.DataFrame:
        """Fetch historical OHLCV data using CCXT"""
        if not self.exchange:
            print("Error: No exchange initialized")
            return pd.DataFrame()

        # Calculate time range
        minutes_per_candle = self.tf_minutes.get(timeframe, 15)
        candles_needed = (days * 24 * 60) // minutes_per_candle

        # Calculate since timestamp
        since = int((datetime.now() - timedelta(days=days)).timestamp() * 1000)

        all_candles = []

        print(f"Fetching {days} days of {timeframe} data for {symbol}...")

        try:
            # Fetch in chunks (most exchanges limit to 1000 per request)
            while len(all_candles) < candles_needed:
                ohlcv = self.exchange.fetch_ohlcv(
                    symbol,
                    timeframe,
                    since=since,
                    limit=1000
                )

                if not ohlcv:
                    break

                all_candles.extend(ohlcv)

                if len(ohlcv) < 1000:
                    break  # No more data

                # Move since to after last candle
                since = ohlcv[-1][0] + 1

                if len(all_candles) % 5000 == 0:
                    print(f"  ... fetched {len(all_candles)} candles so far")

                time.sleep(0.2)  # Rate limiting

        except Exception as e:
            print(f"Error fetching data: {e}")

        if not all_candles:
            return pd.DataFrame()

        # Convert to DataFrame
        df = pd.DataFrame(all_candles, columns=[
            'timestamp', 'open', 'high', 'low', 'close', 'volume'
        ])

        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
        for col in ['open', 'high', 'low', 'close', 'volume']:
            df[col] = df[col].astype(float)

        df = df.drop_duplicates(subset=['timestamp']).sort_values('timestamp').reset_index(drop=True)

        # Limit to requested days
        df = df.tail(candles_needed)

        if not df.empty:
            print(f"  Fetched {len(df)} {timeframe} candles ({df['timestamp'].min()} to {df['timestamp'].max()})")
        return df

    def fetch_htf_data(self, symbol: str, days: int) -> pd.DataFrame:
        """Fetch 1H data for HTF trend confirmation"""
        return self.fetch_data(symbol, '1h', days + 7)  # Extra days for EMA warmup

    def calculate_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """Calculate all technical indicators"""
        df = df.copy()

        # EMAs
        df['ema5'] = df['close'].ewm(span=5, adjust=False).mean()
        df['ema8'] = df['close'].ewm(span=8, adjust=False).mean()
        df['ema20'] = df['close'].ewm(span=20, adjust=False).mean()
        df['ema50'] = df['close'].ewm(span=50, adjust=False).mean()

        # RSI
        delta = df['close'].diff()
        gain = delta.where(delta > 0, 0).rolling(window=14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
        rs = gain / loss.replace(0, np.inf)
        df['rsi'] = 100 - (100 / (1 + rs))

        # ATR
        high_low = df['high'] - df['low']
        high_close = abs(df['high'] - df['close'].shift())
        low_close = abs(df['low'] - df['close'].shift())
        true_range = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
        df['atr'] = true_range.rolling(window=14).mean()

        # Bollinger Bands
        df['bb_middle'] = df['close'].rolling(window=20).mean()
        df['bb_std'] = df['close'].rolling(window=20).std()
        df['bb_upper'] = df['bb_middle'] + (2 * df['bb_std'])
        df['bb_lower'] = df['bb_middle'] - (2 * df['bb_std'])
        df['bb_width'] = df['bb_upper'] - df['bb_lower']

        # Volume analysis
        df['volume_ma'] = df['volume'].rolling(window=20).mean()
        df['volume_ratio'] = df['volume'] / df['volume_ma']

        # Momentum
        df['momentum'] = df['close'] - df['close'].shift(10)

        # Highest high / Lowest low
        df['highest_high_10'] = df['high'].rolling(window=10).max()
        df['lowest_low_10'] = df['low'].rolling(window=10).min()

        return df

    def calculate_htf_indicators(self, df_htf: pd.DataFrame) -> pd.DataFrame:
        """Calculate 1H indicators for trend confirmation"""
        df = df_htf.copy()
        df['htf_ema20'] = df['close'].ewm(span=20, adjust=False).mean()
        df['htf_ema50'] = df['close'].ewm(span=50, adjust=False).mean()
        df['htf_trend'] = (df['close'] > df['htf_ema20']).astype(int)

        # RSI for overbought check
        delta = df['close'].diff()
        gain = delta.where(delta > 0, 0).rolling(window=14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
        rs = gain / loss.replace(0, np.inf)
        df['htf_rsi'] = 100 - (100 / (1 + rs))

        return df

    def get_htf_context(self, df_htf: pd.DataFrame, current_time: pd.Timestamp) -> Dict:
        """Get HTF trend context for a given timestamp"""
        # Find the 1H candle that covers this time
        htf_row = df_htf[df_htf['timestamp'] <= current_time].iloc[-1] if len(df_htf[df_htf['timestamp'] <= current_time]) > 0 else None

        if htf_row is None:
            return {'trend_up': False, 'rsi': 50, 'above_ema': False}

        return {
            'trend_up': htf_row['htf_trend'] == 1,
            'rsi': htf_row['htf_rsi'],
            'above_ema': htf_row['close'] > htf_row['htf_ema20'],
            'ema20': htf_row['htf_ema20'],
            'price': htf_row['close']
        }

    def check_entry_smart(self, df: pd.DataFrame, idx: int, htf_context: Dict) -> Tuple[bool, str]:
        """
        Smart Breakout Entry with HTF confirmation

        Conditions:
        1. HTF (1H) trend is UP (price above EMA20)
        2. HTF RSI < 75 (not overbought)
        3. LTF RSI between 40-70 (momentum but not overbought)
        4. Price breaks above recent high OR RSI bounce from oversold
        5. Volume above average (1.2x)
        6. ATR expanding (volatility)
        """
        if idx < 50:
            return False, "Not enough data"

        row = df.iloc[idx]
        prev_row = df.iloc[idx - 1]

        # === HTF FILTERS ===
        if not htf_context.get('trend_up', False):
            return False, "HTF: Trend DOWN"

        if htf_context.get('rsi', 50) > 75:
            return False, "HTF: RSI overbought"

        # === LTF FILTERS ===
        rsi = row['rsi']
        if pd.isna(rsi):
            return False, "RSI not available"

        # RSI filter: 40-70 range
        if rsi < 40 or rsi > 70:
            return False, f"RSI out of range: {rsi:.1f}"

        # Volume filter: above 1.2x average
        vol_ratio = row['volume_ratio']
        if pd.isna(vol_ratio) or vol_ratio < 1.2:
            return False, f"Volume low: {vol_ratio:.2f}x"

        # Entry trigger: Breakout OR RSI bounce
        entry_triggered = False
        entry_reason = ""

        # Breakout: price closes above 10-period high
        breakout_level = df.iloc[idx-10:idx]['high'].max()
        if row['close'] > breakout_level:
            entry_triggered = True
            entry_reason = f"Breakout above {breakout_level:.2f}"

        # RSI bounce: was oversold, now rising
        recent_rsi = df.iloc[idx-5:idx]['rsi'].values
        if not entry_triggered and len(recent_rsi) >= 3:
            was_oversold = any(r < 35 for r in recent_rsi[:-1] if not pd.isna(r))
            rsi_rising = rsi > df.iloc[idx-1]['rsi'] if not pd.isna(df.iloc[idx-1]['rsi']) else False
            if was_oversold and rsi_rising and rsi > 40:
                entry_triggered = True
                entry_reason = "RSI bounce from oversold"

        if not entry_triggered:
            return False, "No entry trigger"

        # Green candle confirmation
        if row['close'] <= row['open']:
            return False, "Red candle"

        return True, entry_reason

    def check_entry_trend(self, df: pd.DataFrame, idx: int, htf_context: Dict) -> Tuple[bool, str]:
        """
        Trend Following Entry (EMA crossover with HTF confirmation)
        """
        if idx < 50:
            return False, "Not enough data"

        row = df.iloc[idx]
        prev_row = df.iloc[idx - 1]

        # HTF must be bullish
        if not htf_context.get('trend_up', False):
            return False, "HTF: Trend DOWN"

        # EMA8 crosses above EMA20
        ema8_cross = prev_row['ema8'] <= prev_row['ema20'] and row['ema8'] > row['ema20']

        # Price above EMA20
        price_above_ema = row['close'] > row['ema20']

        # RSI confirmation
        rsi = row['rsi']
        rsi_ok = 45 < rsi < 70 if not pd.isna(rsi) else False

        if ema8_cross and price_above_ema and rsi_ok:
            return True, "EMA8 crossed above EMA20"

        return False, "No EMA crossover"

    def check_entry_momentum(self, df: pd.DataFrame, idx: int, htf_context: Dict) -> Tuple[bool, str]:
        """
        Momentum Entry (3 consecutive green candles + volume)
        """
        if idx < 50:
            return False, "Not enough data"

        row = df.iloc[idx]

        # HTF must be bullish
        if not htf_context.get('trend_up', False):
            return False, "HTF: Trend DOWN"

        # Check 3 consecutive green candles
        green_count = 0
        for i in range(3):
            candle = df.iloc[idx - i]
            if candle['close'] > candle['open']:
                green_count += 1
            else:
                break

        if green_count < 3:
            return False, f"Only {green_count} green candles"

        # Volume confirmation
        if row['volume_ratio'] < 1.5:
            return False, "Volume too low"

        # RSI not overbought
        if row['rsi'] > 70:
            return False, "RSI overbought"

        return True, "3 green candles + volume"

    def check_entry_mean_reversion(self, df: pd.DataFrame, idx: int, htf_context: Dict) -> Tuple[bool, str]:
        """
        Mean Reversion Entry (buy dips in uptrend)
        """
        if idx < 50:
            return False, "Not enough data"

        row = df.iloc[idx]

        # HTF must be bullish (we're buying dips in uptrend)
        if not htf_context.get('trend_up', False):
            return False, "HTF: Trend DOWN"

        # Price near lower BB or oversold RSI
        near_lower_bb = row['close'] < row['bb_lower'] * 1.01
        rsi_oversold = row['rsi'] < 35

        if not (near_lower_bb or rsi_oversold):
            return False, "Not oversold"

        # Confirmation: current candle is green (reversal)
        if row['close'] <= row['open']:
            return False, "Waiting for green reversal candle"

        return True, "Oversold bounce"

    def simulate_trade(self, df: pd.DataFrame, entry_idx: int, entry_price: float) -> Dict:
        """
        Simulate a trade with ATR-based TP/SL and trailing stop
        """
        atr = df.iloc[entry_idx]['atr']
        if pd.isna(atr) or atr == 0:
            atr = entry_price * 0.01  # Fallback: 1% of price

        # Calculate levels
        stop_loss = entry_price - (atr * self.sl_atr_mult)
        take_profit = entry_price + (atr * self.tp_atr_mult)
        trailing_activation = entry_price + (atr * self.trailing_start_atr)

        # Simulate candle by candle
        trailing_stop = None
        highest_price = entry_price
        bars_held = 0
        max_bars = 100  # Max hold time

        for i in range(entry_idx + 1, min(entry_idx + max_bars, len(df))):
            row = df.iloc[i]
            bars_held += 1

            # Update highest price
            if row['high'] > highest_price:
                highest_price = row['high']

                # Activate/update trailing stop
                if highest_price >= trailing_activation:
                    new_trailing = highest_price - (atr * self.trailing_step_atr)
                    if trailing_stop is None or new_trailing > trailing_stop:
                        trailing_stop = new_trailing

            # Check stop loss (use low of candle)
            if row['low'] <= stop_loss:
                return {
                    'exit_price': stop_loss,
                    'exit_reason': 'STOP_LOSS',
                    'bars_held': bars_held,
                    'pnl_pct': (stop_loss - entry_price) / entry_price * 100
                }

            # Check trailing stop
            if trailing_stop and row['low'] <= trailing_stop:
                return {
                    'exit_price': trailing_stop,
                    'exit_reason': 'TRAILING_STOP',
                    'bars_held': bars_held,
                    'pnl_pct': (trailing_stop - entry_price) / entry_price * 100
                }

            # Check take profit (use high of candle)
            if row['high'] >= take_profit:
                return {
                    'exit_price': take_profit,
                    'exit_reason': 'TAKE_PROFIT',
                    'bars_held': bars_held,
                    'pnl_pct': (take_profit - entry_price) / entry_price * 100
                }

        # End of data - close at last price
        last_price = df.iloc[min(entry_idx + max_bars - 1, len(df) - 1)]['close']
        return {
            'exit_price': last_price,
            'exit_reason': 'END_OF_DATA',
            'bars_held': bars_held,
            'pnl_pct': (last_price - entry_price) / entry_price * 100
        }

    def run_backtest(self, symbol: str, days: int = 30) -> Dict:
        """Run backtest for a single symbol"""
        # Fetch data
        df = self.fetch_data(symbol, self.timeframe, days)
        if df.empty:
            return {'error': f'No data for {symbol}'}

        df_htf = self.fetch_htf_data(symbol, days)
        if df_htf.empty:
            return {'error': f'No HTF data for {symbol}'}

        # Calculate indicators
        df = self.calculate_indicators(df)
        df_htf = self.calculate_htf_indicators(df_htf)

        print(f"\nScanning {len(df)} bars for signals...")

        # Run simulation
        trades = []
        last_trade_idx = -10  # Cooldown between trades

        for idx in range(50, len(df) - 1):
            # Cooldown check
            if idx - last_trade_idx < 5:
                continue

            # Get HTF context
            htf_context = self.get_htf_context(df_htf, df.iloc[idx]['timestamp'])

            # Check entry based on strategy mode
            if self.strategy_mode == 'smart':
                entry, reason = self.check_entry_smart(df, idx, htf_context)
            elif self.strategy_mode == 'trend':
                entry, reason = self.check_entry_trend(df, idx, htf_context)
            elif self.strategy_mode == 'momentum':
                entry, reason = self.check_entry_momentum(df, idx, htf_context)
            elif self.strategy_mode == 'mean_reversion':
                entry, reason = self.check_entry_mean_reversion(df, idx, htf_context)
            else:
                entry, reason = self.check_entry_smart(df, idx, htf_context)

            if entry:
                entry_price = df.iloc[idx]['close']
                trade_result = self.simulate_trade(df, idx, entry_price)

                trades.append({
                    'entry_time': df.iloc[idx]['timestamp'],
                    'entry_price': entry_price,
                    'entry_reason': reason,
                    **trade_result
                })

                last_trade_idx = idx

        # Calculate statistics
        if not trades:
            return {
                'symbol': symbol,
                'timeframe': self.timeframe,
                'strategy': self.strategy_mode,
                'total_trades': 0,
                'message': 'No trades generated'
            }

        trades_df = pd.DataFrame(trades)

        wins = trades_df[trades_df['pnl_pct'] > 0]
        losses = trades_df[trades_df['pnl_pct'] <= 0]

        gross_profit = wins['pnl_pct'].sum() if len(wins) > 0 else 0
        gross_loss = abs(losses['pnl_pct'].sum()) if len(losses) > 0 else 0

        # Exit reason breakdown
        exit_reasons = trades_df['exit_reason'].value_counts().to_dict()

        results = {
            'symbol': symbol,
            'timeframe': self.timeframe,
            'strategy': self.strategy_mode,
            'period': f"{df['timestamp'].min().date()} to {df['timestamp'].max().date()}",
            'total_bars': len(df),
            'total_trades': len(trades),
            'winning_trades': len(wins),
            'losing_trades': len(losses),
            'win_rate': len(wins) / len(trades) * 100 if trades else 0,
            'profit_factor': gross_profit / gross_loss if gross_loss > 0 else float('inf'),
            'total_pnl_pct': trades_df['pnl_pct'].sum(),
            'avg_win_pct': wins['pnl_pct'].mean() if len(wins) > 0 else 0,
            'avg_loss_pct': losses['pnl_pct'].mean() if len(losses) > 0 else 0,
            'avg_bars_held': trades_df['bars_held'].mean(),
            'max_drawdown_pct': self.calculate_max_drawdown(trades_df),
            'exit_reasons': exit_reasons,
            'trades': trades
        }

        return results

    def calculate_max_drawdown(self, trades_df: pd.DataFrame) -> float:
        """Calculate maximum drawdown from trade sequence"""
        if trades_df.empty:
            return 0

        cumulative = trades_df['pnl_pct'].cumsum()
        running_max = cumulative.cummax()
        drawdown = running_max - cumulative
        return drawdown.max()


def print_results(results: Dict):
    """Pretty print backtest results"""
    if 'error' in results:
        print(f"\n{results['error']}")
        return

    if results['total_trades'] == 0:
        print(f"\n{results['symbol']}: No trades generated")
        return

    print(f"\n{'='*60}")
    print(f"RESULTS FOR {results['symbol']} ({results['timeframe']})")
    print(f"Strategy: {results['strategy'].upper()}")
    print(f"{'='*60}")
    print(f"Period: {results['period']}")
    print(f"Total Bars: {results['total_bars']:,}")
    print(f"Total Trades: {results['total_trades']}")
    print(f"Winning Trades: {results['winning_trades']}")
    print(f"Losing Trades: {results['losing_trades']}")
    print(f"Win Rate: {results['win_rate']:.1f}%")
    print(f"Profit Factor: {results['profit_factor']:.2f}")
    print(f"Total PnL: {results['total_pnl_pct']:.2f}%")
    print(f"Avg Win: {results['avg_win_pct']:.2f}%")
    print(f"Avg Loss: {results['avg_loss_pct']:.2f}%")
    print(f"Avg Bars Held: {results['avg_bars_held']:.1f}")
    print(f"Max Drawdown: {results['max_drawdown_pct']:.2f}%")
    print(f"Exit Reasons: {results['exit_reasons']}")


def main():
    parser = argparse.ArgumentParser(description='ScalperBot Backtester')
    parser.add_argument('--symbol', type=str, default='BNB/USDT',
                        help='Trading pair (default: BNB/USDT)')
    parser.add_argument('--symbols', type=str, nargs='+',
                        help='Multiple symbols to test')
    parser.add_argument('--timeframe', type=str, default='15m',
                        choices=['1m', '5m', '15m', '30m', '1h'],
                        help='Timeframe (default: 15m)')
    parser.add_argument('--days', type=int, default=30,
                        help='Days of history (default: 30)')
    parser.add_argument('--strategy', type=str, default='smart',
                        choices=['smart', 'trend', 'momentum', 'mean_reversion'],
                        help='Strategy mode (default: smart)')
    parser.add_argument('--tp-atr', type=float, default=2.0,
                        help='Take profit ATR multiplier (default: 2.0)')
    parser.add_argument('--sl-atr', type=float, default=1.0,
                        help='Stop loss ATR multiplier (default: 1.0)')
    parser.add_argument('--exchange', type=str, default='mexc',
                        choices=['mexc', 'binance'],
                        help='Exchange for data (default: mexc)')

    args = parser.parse_args()

    # Get symbols to test
    symbols = args.symbols if args.symbols else [args.symbol]

    print(f"\n{'='*60}")
    print(f"SCALPERBOT BACKTEST")
    print(f"Strategy: {args.strategy.upper()}")
    print(f"Timeframe: {args.timeframe}")
    print(f"Period: {args.days} days")
    print(f"R:R Ratio: {args.tp_atr}:{args.sl_atr} (TP={args.tp_atr} ATR, SL={args.sl_atr} ATR)")
    print(f"Exchange: {args.exchange.upper()}")
    print(f"Symbols: {symbols}")
    print(f"{'='*60}")

    # Run backtests
    backtester = Backtester(strategy_mode=args.strategy, timeframe=args.timeframe, exchange=args.exchange)
    backtester.tp_atr_mult = args.tp_atr
    backtester.sl_atr_mult = args.sl_atr

    all_results = []
    for symbol in symbols:
        print(f"\n{'='*60}")
        print(f"Backtesting {symbol} - {args.days} days")
        results = backtester.run_backtest(symbol, days=args.days)
        all_results.append(results)
        print_results(results)

    # Aggregate results
    if len(all_results) > 1:
        total_trades = sum(r.get('total_trades', 0) for r in all_results)
        total_wins = sum(r.get('winning_trades', 0) for r in all_results)
        total_losses = sum(r.get('losing_trades', 0) for r in all_results)
        total_pnl = sum(r.get('total_pnl_pct', 0) for r in all_results)

        print(f"\n{'='*60}")
        print("AGGREGATE RESULTS")
        print(f"{'='*60}")
        print(f"Total Symbols: {len(all_results)}")
        print(f"Total Trades: {total_trades}")
        print(f"Win Rate: {total_wins/total_trades*100:.1f}%" if total_trades > 0 else "N/A")
        print(f"Total PnL: {total_pnl:.2f}%")

    # Acceptance criteria check
    print(f"\n{'='*60}")
    print("ACCEPTANCE CRITERIA CHECK:")
    print(f"{'='*60}")

    if len(all_results) == 1:
        r = all_results[0]
        wr = r.get('win_rate', 0)
        pf = r.get('profit_factor', 0)
        trades = r.get('total_trades', 0)

        wr_pass = wr >= 40
        pf_pass = pf >= 1.15
        trades_pass = trades >= 50

        print(f"{'PASS' if wr_pass else 'FAIL'} Win Rate: {wr:.1f}% (target: >=40%)")
        print(f"{'PASS' if pf_pass else 'FAIL'} Profit Factor: {pf:.2f} (target: >=1.15)")
        print(f"{'PASS' if trades_pass else 'FAIL'} Trades: {trades} (target: >=50)")

        if wr_pass and pf_pass and trades_pass:
            print("\nSTRATEGY PASSES ALL CRITERIA!")
        else:
            print("\nSTRATEGY NEEDS ADJUSTMENT")


if __name__ == '__main__':
    main()
