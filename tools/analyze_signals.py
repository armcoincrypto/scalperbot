#!/usr/bin/env python3
"""
Signal Quality Analyzer
Analyzes WHY certain trades win and others lose.
Uses 1m candles (same as the bot) to find predictive patterns.
"""
import sys
sys.path.insert(0, '/home/user/scalperbot')

import ccxt
import pandas as pd
import numpy as np
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Tuple
import time

# Configuration
SYMBOLS = ['BNB/USDT', 'XRP/USDT', 'XLM/USDT']
TIMEFRAME = '1m'  # Same as bot!
LOOKBACK_DAYS = 30  # 30 days of 1m data (more than enough)

# Strategy parameters (matching smart_breakout)
RSI_PERIOD = 14
RSI_OVERSOLD = 40
RSI_OVERBOUGHT = 70
EMA_PERIOD = 20
ATR_PERIOD = 14
VOLUME_MA_PERIOD = 20
TP_ATR_MULT = 2.5
SL_ATR_MULT = 1.5
MIN_TP_PCT = 0.60
MIN_SL_PCT = 0.40


def calculate_rsi(prices: pd.Series, period: int = 14) -> pd.Series:
    delta = prices.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))


def calculate_atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    tr1 = high - low
    tr2 = abs(high - close.shift())
    tr3 = abs(low - close.shift())
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    return tr.rolling(window=period).mean()


def calculate_bb(close: pd.Series, period: int = 20, std: float = 2.0) -> Tuple[pd.Series, pd.Series, pd.Series]:
    """Calculate Bollinger Bands"""
    middle = close.rolling(window=period).mean()
    std_dev = close.rolling(window=period).std()
    upper = middle + (std_dev * std)
    lower = middle - (std_dev * std)
    return upper, middle, lower


def fetch_historical_data(symbol: str, days: int = 30) -> pd.DataFrame:
    """Fetch historical 1m OHLCV data"""
    print(f"Fetching {days} days of 1m data for {symbol}...")

    exchange = ccxt.mexc({'enableRateLimit': True})

    since = int((datetime.now(timezone.utc) - timedelta(days=days)).timestamp() * 1000)
    all_candles = []

    while True:
        try:
            candles = exchange.fetch_ohlcv(symbol, TIMEFRAME, since=since, limit=1000)
            if not candles:
                break
            all_candles.extend(candles)
            since = candles[-1][0] + 1

            print(f"  Fetched {len(all_candles)} candles...", end='\r')

            if len(candles) < 1000:
                break
            time.sleep(0.1)
        except Exception as e:
            print(f"Error: {e}")
            time.sleep(1)
            continue

    print(f"  Total: {len(all_candles)} candles for {symbol}                    ")

    df = pd.DataFrame(all_candles, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
    df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms', utc=True)
    df.set_index('timestamp', inplace=True)

    return df


def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Add all technical indicators"""
    df = df.copy()

    # Basic indicators
    df['rsi'] = calculate_rsi(df['close'], RSI_PERIOD)
    df['ema20'] = df['close'].ewm(span=EMA_PERIOD, adjust=False).mean()
    df['ema50'] = df['close'].ewm(span=50, adjust=False).mean()
    df['atr'] = calculate_atr(df['high'], df['low'], df['close'], ATR_PERIOD)

    # Volume analysis
    df['volume_ma'] = df['volume'].rolling(window=VOLUME_MA_PERIOD).mean()
    df['volume_ratio'] = df['volume'] / df['volume_ma']

    # Bollinger Bands
    df['bb_upper'], df['bb_middle'], df['bb_lower'] = calculate_bb(df['close'])
    df['bb_width'] = (df['bb_upper'] - df['bb_lower']) / df['bb_middle'] * 100
    df['bb_position'] = (df['close'] - df['bb_lower']) / (df['bb_upper'] - df['bb_lower'])

    # Trend strength
    df['trend_strength'] = (df['close'] - df['ema20']) / df['ema20'] * 100
    df['ema_trend'] = (df['ema20'] - df['ema50']) / df['ema50'] * 100

    # Momentum
    df['momentum_5'] = (df['close'] - df['close'].shift(5)) / df['close'].shift(5) * 100
    df['momentum_10'] = (df['close'] - df['close'].shift(10)) / df['close'].shift(10) * 100

    # Volatility
    df['volatility'] = df['close'].pct_change().rolling(window=20).std() * 100

    # Price position in range
    df['high_20'] = df['high'].rolling(window=20).max()
    df['low_20'] = df['low'].rolling(window=20).min()
    df['price_position'] = (df['close'] - df['low_20']) / (df['high_20'] - df['low_20'])

    # Hour of day
    df['hour'] = df.index.hour
    df['day_of_week'] = df.index.dayofweek

    return df


def simulate_trades(df: pd.DataFrame) -> List[Dict]:
    """Simulate trades and capture entry conditions"""
    df = df.dropna()
    trades = []
    position = None

    for i in range(len(df) - 100):  # Leave room for trade to complete
        row = df.iloc[i]

        # Manage existing position
        if position is not None:
            # Look ahead for TP/SL hit
            for j in range(i + 1, min(i + 360, len(df))):  # Max 6 hours
                future = df.iloc[j]

                if future['high'] >= position['tp']:
                    # Take profit
                    pnl_pct = (position['tp'] - position['entry']) / position['entry'] * 100
                    trade = {
                        **position['conditions'],
                        'entry_price': position['entry'],
                        'exit_price': position['tp'],
                        'pnl_pct': pnl_pct,
                        'result': 'WIN',
                        'hold_minutes': j - i,
                    }
                    trades.append(trade)
                    position = None
                    break

                elif future['low'] <= position['sl']:
                    # Stop loss
                    pnl_pct = (position['sl'] - position['entry']) / position['entry'] * 100
                    trade = {
                        **position['conditions'],
                        'entry_price': position['entry'],
                        'exit_price': position['sl'],
                        'pnl_pct': pnl_pct,
                        'result': 'LOSS',
                        'hold_minutes': j - i,
                    }
                    trades.append(trade)
                    position = None
                    break

            if position is not None:
                # Timeout - close at current price
                position = None
            continue

        # Entry conditions (simplified smart breakout)
        if not (RSI_OVERSOLD <= row['rsi'] <= RSI_OVERBOUGHT):
            continue
        if row['close'] <= row['ema20']:
            continue
        if row['volume_ratio'] < 1.2:
            continue

        # Calculate TP/SL
        entry = row['close']
        atr = row['atr']

        tp = entry + max(atr * TP_ATR_MULT, entry * MIN_TP_PCT / 100)
        sl = entry - max(atr * SL_ATR_MULT, entry * MIN_SL_PCT / 100)

        # Capture all conditions at entry
        conditions = {
            'entry_time': row.name,
            'hour': row['hour'],
            'day_of_week': row['day_of_week'],
            'rsi': row['rsi'],
            'volume_ratio': row['volume_ratio'],
            'bb_position': row['bb_position'],
            'bb_width': row['bb_width'],
            'trend_strength': row['trend_strength'],
            'ema_trend': row['ema_trend'],
            'momentum_5': row['momentum_5'],
            'momentum_10': row['momentum_10'],
            'volatility': row['volatility'],
            'price_position': row['price_position'],
            'atr_pct': atr / entry * 100,
        }

        position = {
            'entry': entry,
            'tp': tp,
            'sl': sl,
            'conditions': conditions
        }

    return trades


def analyze_winning_conditions(trades: List[Dict]):
    """Find what conditions predict winning trades"""
    if not trades:
        print("No trades to analyze")
        return

    df = pd.DataFrame(trades)
    wins = df[df['result'] == 'WIN']
    losses = df[df['result'] == 'LOSS']

    print(f"\n{'='*70}")
    print(f"SIGNAL QUALITY ANALYSIS")
    print(f"{'='*70}")
    print(f"Total Trades: {len(df)} | Wins: {len(wins)} | Losses: {len(losses)}")
    print(f"Overall Win Rate: {len(wins)/len(df)*100:.1f}%")

    # Analyze each condition
    conditions = ['rsi', 'volume_ratio', 'bb_position', 'bb_width', 'trend_strength',
                  'ema_trend', 'momentum_5', 'momentum_10', 'volatility', 'price_position', 'atr_pct']

    print(f"\n{'='*70}")
    print("CONDITION ANALYSIS: What differs between WINS and LOSSES?")
    print(f"{'='*70}")
    print(f"{'Condition':<18} | {'Wins Avg':>10} | {'Loss Avg':>10} | {'Difference':>10} | Signal")
    print("-" * 70)

    important_conditions = []

    for cond in conditions:
        win_avg = wins[cond].mean()
        loss_avg = losses[cond].mean()
        diff = win_avg - loss_avg
        diff_pct = abs(diff / loss_avg * 100) if loss_avg != 0 else 0

        # Determine if significant
        signal = ""
        if diff_pct > 15:
            if diff > 0:
                signal = "✅ WINS higher"
            else:
                signal = "✅ WINS lower"
            important_conditions.append((cond, win_avg, loss_avg, diff))

        print(f"{cond:<18} | {win_avg:>10.2f} | {loss_avg:>10.2f} | {diff:>+10.2f} | {signal}")

    # Hour analysis
    print(f"\n{'='*70}")
    print("WIN RATE BY HOUR")
    print(f"{'='*70}")

    hourly = df.groupby('hour').agg({
        'result': ['count', lambda x: (x == 'WIN').sum()],
        'pnl_pct': 'sum'
    }).round(2)
    hourly.columns = ['trades', 'wins', 'pnl']
    hourly['win_rate'] = (hourly['wins'] / hourly['trades'] * 100).round(1)

    print(f"{'Hour':<6} | {'Trades':>7} | {'Wins':>5} | {'WinRate':>8} | {'PnL%':>8}")
    print("-" * 50)

    for hour in sorted(hourly.index):
        row = hourly.loc[hour]
        marker = "🟢" if row['win_rate'] >= 50 else "🔴" if row['win_rate'] < 35 else ""
        print(f"{hour:02d}:00  | {int(row['trades']):>7} | {int(row['wins']):>5} | {row['win_rate']:>7.1f}% | {row['pnl']:>+7.1f}% {marker}")

    # Find optimal conditions
    print(f"\n{'='*70}")
    print("🎯 OPTIMAL ENTRY CONDITIONS (based on winning trades)")
    print(f"{'='*70}")

    for cond, win_avg, loss_avg, diff in important_conditions:
        if diff > 0:
            print(f"  {cond}: Look for HIGHER values (wins avg: {win_avg:.2f} vs losses: {loss_avg:.2f})")
        else:
            print(f"  {cond}: Look for LOWER values (wins avg: {win_avg:.2f} vs losses: {loss_avg:.2f})")

    # Find best hour ranges
    best_hours = hourly[hourly['win_rate'] >= 45].index.tolist()
    if best_hours:
        print(f"\n  Best trading hours (UTC): {sorted(best_hours)}")

    # Suggest filters
    print(f"\n{'='*70}")
    print("📊 RECOMMENDED FILTERS TO IMPROVE WIN RATE")
    print(f"{'='*70}")

    # Test various filters
    filters_to_test = [
        ('RSI < 55', df['rsi'] < 55),
        ('RSI 45-60', (df['rsi'] >= 45) & (df['rsi'] <= 60)),
        ('Volume ratio > 1.5', df['volume_ratio'] > 1.5),
        ('Volume ratio > 2.0', df['volume_ratio'] > 2.0),
        ('BB position < 0.7', df['bb_position'] < 0.7),
        ('BB position < 0.5', df['bb_position'] < 0.5),
        ('Trend strength < 0.5%', df['trend_strength'] < 0.5),
        ('Momentum 5 < 0.3%', df['momentum_5'] < 0.3),
        ('Volatility < 0.15%', df['volatility'] < 0.15),
        ('Price position < 0.8', df['price_position'] < 0.8),
        ('EMA trend > 0', df['ema_trend'] > 0),
    ]

    print(f"{'Filter':<25} | {'Trades':>7} | {'Wins':>5} | {'WinRate':>8} | {'vs Base':>8}")
    print("-" * 65)

    base_wr = len(wins) / len(df) * 100

    best_filter = None
    best_improvement = 0

    for name, mask in filters_to_test:
        filtered = df[mask]
        if len(filtered) < 10:
            continue
        filtered_wins = len(filtered[filtered['result'] == 'WIN'])
        filtered_wr = filtered_wins / len(filtered) * 100
        improvement = filtered_wr - base_wr

        marker = "✅" if improvement > 5 else ""
        print(f"{name:<25} | {len(filtered):>7} | {filtered_wins:>5} | {filtered_wr:>7.1f}% | {improvement:>+7.1f}% {marker}")

        if improvement > best_improvement and len(filtered) >= 20:
            best_improvement = improvement
            best_filter = name

    if best_filter:
        print(f"\n🏆 Best single filter: {best_filter} (improves WR by {best_improvement:.1f}%)")

    return df


def main():
    print("="*70)
    print("SIGNAL QUALITY ANALYZER - Find WHY trades win or lose")
    print("="*70)
    print(f"Using 1m candles (same as live bot)")
    print(f"Analyzing {LOOKBACK_DAYS} days of data")
    print()

    all_trades = []

    for symbol in SYMBOLS:
        try:
            df = fetch_historical_data(symbol, LOOKBACK_DAYS)
            df = add_indicators(df)
            trades = simulate_trades(df)

            for t in trades:
                t['symbol'] = symbol
            all_trades.extend(trades)

            print(f"  {symbol}: {len(trades)} simulated trades")
        except Exception as e:
            print(f"Error processing {symbol}: {e}")
            import traceback
            traceback.print_exc()

    if all_trades:
        analyze_winning_conditions(all_trades)
    else:
        print("No trades generated - check strategy conditions")


if __name__ == "__main__":
    main()
