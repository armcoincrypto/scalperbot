#!/usr/bin/env python3
"""
Backtest Trading Hours Analysis
Analyzes historical win rates during different time windows over the past year.
"""
import sys
sys.path.insert(0, '/home/user/scalperbot')

import ccxt
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from typing import List, Tuple
import time

# Configuration
SYMBOLS = ['BNB/USDT', 'XRP/USDT', 'XLM/USDT']
TIMEFRAME = '1h'  # Use 1h for faster analysis (1m would be too slow for 1 year)
LOOKBACK_DAYS = 365

# Strategy parameters (matching smart_breakout)
RSI_PERIOD = 14
RSI_OVERSOLD = 40
RSI_OVERBOUGHT = 70
EMA_PERIOD = 20
ATR_PERIOD = 14
TP_ATR_MULT = 2.5
SL_ATR_MULT = 1.5
MIN_TP_PCT = 0.60
MIN_SL_PCT = 0.40


def calculate_rsi(prices: pd.Series, period: int = 14) -> pd.Series:
    """Calculate RSI"""
    delta = prices.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))


def calculate_atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    """Calculate ATR"""
    tr1 = high - low
    tr2 = abs(high - close.shift())
    tr3 = abs(low - close.shift())
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    return tr.rolling(window=period).mean()


def fetch_historical_data(symbol: str) -> pd.DataFrame:
    """Fetch historical OHLCV data from MEXC"""
    print(f"Fetching {LOOKBACK_DAYS} days of {TIMEFRAME} data for {symbol}...")

    exchange = ccxt.mexc({'enableRateLimit': True})

    since = int((datetime.utcnow() - timedelta(days=LOOKBACK_DAYS)).timestamp() * 1000)
    all_candles = []

    while True:
        try:
            candles = exchange.fetch_ohlcv(symbol, TIMEFRAME, since=since, limit=1000)
            if not candles:
                break
            all_candles.extend(candles)
            since = candles[-1][0] + 1

            # Progress indicator
            print(f"  Fetched {len(all_candles)} candles...", end='\r')

            if len(candles) < 1000:
                break
            time.sleep(0.2)  # Rate limit
        except Exception as e:
            print(f"Error fetching data: {e}")
            time.sleep(1)
            continue

    print(f"  Fetched {len(all_candles)} candles for {symbol}")

    df = pd.DataFrame(all_candles, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
    df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
    df.set_index('timestamp', inplace=True)

    return df


def simulate_strategy(df: pd.DataFrame, trading_hours: Tuple[int, int] = None) -> dict:
    """
    Simulate the strategy and return stats.
    If trading_hours is provided, only take trades during those hours (start, end).
    """
    # Calculate indicators
    df = df.copy()
    df['rsi'] = calculate_rsi(df['close'], RSI_PERIOD)
    df['ema'] = df['close'].ewm(span=EMA_PERIOD, adjust=False).mean()
    df['atr'] = calculate_atr(df['high'], df['low'], df['close'], ATR_PERIOD)
    df['hour'] = df.index.hour

    # Drop NaN rows
    df = df.dropna()

    trades = []
    position = None

    for i in range(len(df) - 1):
        row = df.iloc[i]
        next_row = df.iloc[i + 1]

        # Check if we have a position to manage
        if position is not None:
            # Check TP/SL
            if next_row['high'] >= position['tp']:
                # Take profit hit
                pnl_pct = (position['tp'] - position['entry']) / position['entry'] * 100
                trades.append({
                    'entry_time': position['entry_time'],
                    'exit_time': next_row.name,
                    'entry': position['entry'],
                    'exit': position['tp'],
                    'pnl_pct': pnl_pct,
                    'result': 'WIN',
                    'hour': position['hour']
                })
                position = None
            elif next_row['low'] <= position['sl']:
                # Stop loss hit
                pnl_pct = (position['sl'] - position['entry']) / position['entry'] * 100
                trades.append({
                    'entry_time': position['entry_time'],
                    'exit_time': next_row.name,
                    'entry': position['entry'],
                    'exit': position['sl'],
                    'pnl_pct': pnl_pct,
                    'result': 'LOSS',
                    'hour': position['hour']
                })
                position = None
            continue

        # Entry conditions (simplified smart breakout)
        hour = row['hour']

        # Skip if outside trading hours (if specified)
        if trading_hours is not None:
            start_h, end_h = trading_hours
            if not (start_h <= hour < end_h):
                continue

        # RSI in range
        if not (RSI_OVERSOLD <= row['rsi'] <= RSI_OVERBOUGHT):
            continue

        # Price above EMA (uptrend)
        if row['close'] <= row['ema']:
            continue

        # Calculate TP/SL
        atr = row['atr']
        entry = row['close']

        tp_atr = entry + (atr * TP_ATR_MULT)
        sl_atr = entry - (atr * SL_ATR_MULT)

        tp_pct = (tp_atr - entry) / entry * 100
        sl_pct = (entry - sl_atr) / entry * 100

        # Enforce minimums
        if tp_pct < MIN_TP_PCT:
            tp = entry * (1 + MIN_TP_PCT / 100)
        else:
            tp = tp_atr

        if sl_pct < MIN_SL_PCT:
            sl = entry * (1 - MIN_SL_PCT / 100)
        else:
            sl = sl_atr

        # Open position
        position = {
            'entry_time': row.name,
            'entry': entry,
            'tp': tp,
            'sl': sl,
            'hour': hour
        }

    return trades


def analyze_trades(trades: List[dict], label: str):
    """Analyze and print trade statistics"""
    if not trades:
        print(f"\n{label}: No trades")
        return

    df = pd.DataFrame(trades)
    total = len(df)
    wins = len(df[df['result'] == 'WIN'])
    losses = len(df[df['result'] == 'LOSS'])
    win_rate = (wins / total) * 100 if total > 0 else 0

    avg_win = df[df['result'] == 'WIN']['pnl_pct'].mean() if wins > 0 else 0
    avg_loss = df[df['result'] == 'LOSS']['pnl_pct'].mean() if losses > 0 else 0
    total_pnl = df['pnl_pct'].sum()

    print(f"\n{'='*60}")
    print(f"{label}")
    print(f"{'='*60}")
    print(f"Total Trades: {total}")
    print(f"Wins: {wins} | Losses: {losses}")
    print(f"Win Rate: {win_rate:.1f}%")
    print(f"Avg Win: {avg_win:.2f}% | Avg Loss: {avg_loss:.2f}%")
    print(f"Total PnL: {total_pnl:.2f}%")

    # Profit factor
    gross_profit = df[df['result'] == 'WIN']['pnl_pct'].sum()
    gross_loss = abs(df[df['result'] == 'LOSS']['pnl_pct'].sum())
    pf = gross_profit / gross_loss if gross_loss > 0 else 0
    print(f"Profit Factor: {pf:.2f}")

    return {
        'total': total,
        'wins': wins,
        'win_rate': win_rate,
        'total_pnl': total_pnl,
        'profit_factor': pf
    }


def analyze_by_hour(trades: List[dict]):
    """Analyze trades by hour of day"""
    if not trades:
        return

    df = pd.DataFrame(trades)

    print(f"\n{'='*60}")
    print("WIN RATE BY HOUR (UTC)")
    print(f"{'='*60}")
    print(f"{'Hour':<6} | {'Trades':<7} | {'Wins':<5} | {'Win%':<7} | {'PnL%':<8}")
    print("-" * 50)

    hourly_stats = []
    for hour in range(24):
        hour_df = df[df['hour'] == hour]
        if len(hour_df) == 0:
            continue

        total = len(hour_df)
        wins = len(hour_df[hour_df['result'] == 'WIN'])
        wr = (wins / total) * 100
        pnl = hour_df['pnl_pct'].sum()

        hourly_stats.append({'hour': hour, 'trades': total, 'wins': wins, 'wr': wr, 'pnl': pnl})
        print(f"{hour:02d}:00  | {total:<7} | {wins:<5} | {wr:>5.1f}% | {pnl:>+7.2f}%")

    # Find best hours
    if hourly_stats:
        sorted_by_wr = sorted(hourly_stats, key=lambda x: x['wr'], reverse=True)
        print(f"\n🏆 Best hours by win rate:")
        for s in sorted_by_wr[:5]:
            print(f"   {s['hour']:02d}:00 UTC: {s['wr']:.1f}% WR ({s['trades']} trades)")


def main():
    print("="*60)
    print("BACKTEST: Trading Hours Analysis (Last 365 Days)")
    print("="*60)
    print(f"Symbols: {SYMBOLS}")
    print(f"Timeframe: {TIMEFRAME}")
    print(f"Strategy: Smart Breakout (RSI {RSI_OVERSOLD}-{RSI_OVERBOUGHT}, EMA {EMA_PERIOD})")
    print(f"TP: {TP_ATR_MULT}x ATR (min {MIN_TP_PCT}%) | SL: {SL_ATR_MULT}x ATR (min {MIN_SL_PCT}%)")

    all_trades_24h = []
    all_trades_window = []

    for symbol in SYMBOLS:
        try:
            df = fetch_historical_data(symbol)

            # 24h trading (baseline)
            trades_24h = simulate_strategy(df, trading_hours=None)
            for t in trades_24h:
                t['symbol'] = symbol
            all_trades_24h.extend(trades_24h)

            # 05:00-10:00 UTC window
            trades_window = simulate_strategy(df, trading_hours=(5, 10))
            for t in trades_window:
                t['symbol'] = symbol
            all_trades_window.extend(trades_window)

            print(f"  {symbol}: {len(trades_24h)} trades (24h) | {len(trades_window)} trades (05-10 UTC)")

        except Exception as e:
            print(f"Error processing {symbol}: {e}")

    # Analyze results
    analyze_trades(all_trades_24h, "ALL HOURS (24h trading)")
    analyze_trades(all_trades_window, "FILTERED HOURS (05:00-10:00 UTC only)")

    # Hourly breakdown
    analyze_by_hour(all_trades_24h)

    print("\n" + "="*60)
    print("CONCLUSION")
    print("="*60)

    if all_trades_24h and all_trades_window:
        wr_24h = (len([t for t in all_trades_24h if t['result'] == 'WIN']) / len(all_trades_24h)) * 100
        wr_window = (len([t for t in all_trades_window if t['result'] == 'WIN']) / len(all_trades_window)) * 100

        pnl_24h = sum(t['pnl_pct'] for t in all_trades_24h)
        pnl_window = sum(t['pnl_pct'] for t in all_trades_window)

        print(f"24h trading:     {wr_24h:.1f}% WR | {pnl_24h:+.1f}% PnL | {len(all_trades_24h)} trades")
        print(f"05-10 UTC only:  {wr_window:.1f}% WR | {pnl_window:+.1f}% PnL | {len(all_trades_window)} trades")

        if wr_window > wr_24h:
            print(f"\n✅ Trading 05:00-10:00 UTC improves win rate by {wr_window - wr_24h:.1f}%")
        else:
            print(f"\n⚠️ Trading hours filter doesn't improve performance in backtest")


if __name__ == "__main__":
    main()
