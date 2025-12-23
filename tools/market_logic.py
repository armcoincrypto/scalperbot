#!/usr/bin/env python3
"""
MARKET LOGIC ANALYZER
=====================
NOT about indicators. About understanding WHY price moves.

Key Questions:
1. After price breaks a level, does it CONTINUE or REVERSE?
2. What happens after volume spikes?
3. Does momentum predict continuation?
4. What is the ACTUAL edge (not correlation)?

Purpose: Find ONE repeatable pattern with real edge.
"""
import sys
sys.path.insert(0, '/home/user/scalperbot')

import ccxt
import pandas as pd
import numpy as np
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Tuple
import time

# Configuration
SYMBOLS = ['BNB/USDT', 'XRP/USDT', 'XLM/USDT']
LOOKBACK_DAYS = 7  # Recent data for relevance


def fetch_data(symbol: str, timeframe: str = '1m', days: int = 7) -> pd.DataFrame:
    """Fetch OHLCV data"""
    print(f"Fetching {days} days of {timeframe} data for {symbol}...")
    exchange = ccxt.mexc({'enableRateLimit': True})

    since = int((datetime.now(timezone.utc) - timedelta(days=days)).timestamp() * 1000)
    end_time = int(datetime.now(timezone.utc).timestamp() * 1000)
    all_candles = []

    while since < end_time:
        try:
            candles = exchange.fetch_ohlcv(symbol, timeframe, since=since, limit=1000)
            if not candles:
                break
            all_candles.extend(candles)
            since = candles[-1][0] + 60000
            print(f"  {len(all_candles)} candles...", end='\r')
            if len(candles) < 100:
                break
            time.sleep(0.1)
        except Exception as e:
            print(f"Error: {e}")
            time.sleep(1)

    print(f"  Total: {len(all_candles)} candles")

    df = pd.DataFrame(all_candles, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
    df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms', utc=True)
    df.set_index('timestamp', inplace=True)
    return df


def analyze_breakout_continuation(df: pd.DataFrame) -> Dict:
    """
    QUESTION 1: After price breaks above a recent high, does it CONTINUE or REVERSE?

    This is the core assumption of breakout strategies.
    Let's test if it's actually true.
    """
    print("\n" + "="*70)
    print("ANALYSIS 1: BREAKOUT CONTINUATION")
    print("Question: After breaking a 20-bar high, does price continue UP?")
    print("="*70)

    df = df.copy()
    df['high_20'] = df['high'].rolling(window=20).max().shift(1)  # Previous 20-bar high
    df['breakout'] = df['high'] > df['high_20']

    # Track what happens after breakouts
    results = {'continue_up': 0, 'reverse_down': 0, 'neutral': 0}
    breakout_details = []

    breakout_indices = df[df['breakout']].index

    for idx in breakout_indices:
        try:
            pos = df.index.get_loc(idx)
            if pos + 10 >= len(df):
                continue

            entry_price = df.iloc[pos]['close']
            # Look 10 candles ahead
            future_prices = df.iloc[pos+1:pos+11]['close']
            max_gain = (future_prices.max() - entry_price) / entry_price * 100
            max_loss = (future_prices.min() - entry_price) / entry_price * 100
            final_price = future_prices.iloc[-1]
            final_change = (final_price - entry_price) / entry_price * 100

            if final_change > 0.1:
                results['continue_up'] += 1
                outcome = 'UP'
            elif final_change < -0.1:
                results['reverse_down'] += 1
                outcome = 'DOWN'
            else:
                results['neutral'] += 1
                outcome = 'NEUTRAL'

            breakout_details.append({
                'entry': entry_price,
                'max_gain': max_gain,
                'max_loss': max_loss,
                'final': final_change,
                'outcome': outcome
            })
        except:
            continue

    total = sum(results.values())
    if total == 0:
        print("No breakouts found")
        return {}

    print(f"\nBreakouts analyzed: {total}")
    print(f"  Continue UP:   {results['continue_up']:>4} ({results['continue_up']/total*100:.1f}%)")
    print(f"  Reverse DOWN:  {results['reverse_down']:>4} ({results['reverse_down']/total*100:.1f}%)")
    print(f"  Neutral:       {results['neutral']:>4} ({results['neutral']/total*100:.1f}%)")

    if breakout_details:
        details_df = pd.DataFrame(breakout_details)
        print(f"\nAverage outcomes after breakout:")
        print(f"  Max gain seen:  {details_df['max_gain'].mean():+.3f}%")
        print(f"  Max loss seen:  {details_df['max_loss'].mean():+.3f}%")
        print(f"  Final change:   {details_df['final'].mean():+.3f}%")

    edge = results['continue_up'] / total if total > 0 else 0
    print(f"\n{'✅ EDGE EXISTS' if edge > 0.55 else '❌ NO EDGE'}: Breakout continuation rate = {edge*100:.1f}%")

    return results


def analyze_volume_prediction(df: pd.DataFrame) -> Dict:
    """
    QUESTION 2: Does HIGH VOLUME predict price direction?

    Common belief: High volume = continuation
    Let's test it.
    """
    print("\n" + "="*70)
    print("ANALYSIS 2: VOLUME PREDICTS DIRECTION?")
    print("Question: After a volume spike, does price move in the candle direction?")
    print("="*70)

    df = df.copy()
    df['vol_avg'] = df['volume'].rolling(window=20).mean()
    df['vol_ratio'] = df['volume'] / df['vol_avg']
    df['is_green'] = df['close'] > df['open']
    df['vol_spike'] = df['vol_ratio'] > 2.0  # 2x average volume

    green_spikes = df[(df['vol_spike']) & (df['is_green'])]
    red_spikes = df[(df['vol_spike']) & (~df['is_green'])]

    def check_follow_through(spike_df, expected_direction):
        follow = 0
        reverse = 0
        for idx in spike_df.index:
            try:
                pos = df.index.get_loc(idx)
                if pos + 5 >= len(df):
                    continue
                entry = df.iloc[pos]['close']
                future = df.iloc[pos+1:pos+6]['close'].mean()
                change = (future - entry) / entry * 100

                if expected_direction == 'up' and change > 0.05:
                    follow += 1
                elif expected_direction == 'up' and change < -0.05:
                    reverse += 1
                elif expected_direction == 'down' and change < -0.05:
                    follow += 1
                elif expected_direction == 'down' and change > 0.05:
                    reverse += 1
            except:
                continue
        return follow, reverse

    print(f"\nGREEN volume spikes (expect UP):")
    g_follow, g_reverse = check_follow_through(green_spikes, 'up')
    g_total = g_follow + g_reverse
    if g_total > 0:
        print(f"  Follows UP:   {g_follow:>4} ({g_follow/g_total*100:.1f}%)")
        print(f"  Reverses DOWN: {g_reverse:>4} ({g_reverse/g_total*100:.1f}%)")
        print(f"  {'✅ PREDICTIVE' if g_follow/g_total > 0.55 else '❌ NOT PREDICTIVE'}")

    print(f"\nRED volume spikes (expect DOWN):")
    r_follow, r_reverse = check_follow_through(red_spikes, 'down')
    r_total = r_follow + r_reverse
    if r_total > 0:
        print(f"  Follows DOWN: {r_follow:>4} ({r_follow/r_total*100:.1f}%)")
        print(f"  Reverses UP:  {r_reverse:>4} ({r_reverse/r_total*100:.1f}%)")
        print(f"  {'✅ PREDICTIVE' if r_follow/r_total > 0.55 else '❌ NOT PREDICTIVE'}")

    return {'green_follow': g_follow, 'green_reverse': g_reverse,
            'red_follow': r_follow, 'red_reverse': r_reverse}


def analyze_momentum_continuation(df: pd.DataFrame) -> Dict:
    """
    QUESTION 3: Does MOMENTUM (consecutive moves) predict continuation?

    After 3 green candles, is the 4th more likely green?
    """
    print("\n" + "="*70)
    print("ANALYSIS 3: MOMENTUM CONTINUATION")
    print("Question: After 3 consecutive up candles, does the 4th continue up?")
    print("="*70)

    df = df.copy()
    df['is_green'] = df['close'] > df['open']

    # Find sequences of 3 consecutive greens
    consecutive_greens = 0
    results = {'4th_green': 0, '4th_red': 0}

    for i in range(3, len(df)-1):
        # Check if previous 3 were all green
        if df.iloc[i-3:i]['is_green'].all():
            # Check the 4th candle
            if df.iloc[i]['is_green']:
                results['4th_green'] += 1
            else:
                results['4th_red'] += 1

    total = sum(results.values())
    if total == 0:
        print("No 3-green sequences found")
        return {}

    print(f"\nAfter 3 consecutive GREEN candles:")
    print(f"  4th is GREEN: {results['4th_green']:>4} ({results['4th_green']/total*100:.1f}%)")
    print(f"  4th is RED:   {results['4th_red']:>4} ({results['4th_red']/total*100:.1f}%)")

    edge = results['4th_green'] / total
    print(f"\n{'✅ MOMENTUM WORKS' if edge > 0.55 else '❌ MOMENTUM IS RANDOM'}: 4th green rate = {edge*100:.1f}%")

    return results


def analyze_mean_reversion(df: pd.DataFrame) -> Dict:
    """
    QUESTION 4: Does price MEAN REVERT after extreme moves?

    After a big move, does it reverse?
    """
    print("\n" + "="*70)
    print("ANALYSIS 4: MEAN REVERSION")
    print("Question: After price moves 1%+ from EMA, does it revert?")
    print("="*70)

    df = df.copy()
    df['ema20'] = df['close'].ewm(span=20, adjust=False).mean()
    df['distance'] = (df['close'] - df['ema20']) / df['ema20'] * 100

    # Find overextended moves (>1% from EMA)
    extended_up = df[df['distance'] > 1.0]
    extended_down = df[df['distance'] < -1.0]

    def check_reversion(ext_df, expected_direction):
        revert = 0
        continue_ext = 0
        for idx in ext_df.index:
            try:
                pos = df.index.get_loc(idx)
                if pos + 10 >= len(df):
                    continue
                entry_dist = df.iloc[pos]['distance']
                future_dist = df.iloc[pos+5:pos+11]['distance'].mean()

                if expected_direction == 'down' and future_dist < entry_dist - 0.2:
                    revert += 1
                elif expected_direction == 'down' and future_dist > entry_dist:
                    continue_ext += 1
                elif expected_direction == 'up' and future_dist > entry_dist + 0.2:
                    revert += 1
                elif expected_direction == 'up' and future_dist < entry_dist:
                    continue_ext += 1
            except:
                continue
        return revert, continue_ext

    print(f"\nExtended UP (+1% from EMA) - expect mean reversion DOWN:")
    up_revert, up_continue = check_reversion(extended_up, 'down')
    up_total = up_revert + up_continue
    if up_total > 0:
        print(f"  Reverts DOWN:  {up_revert:>4} ({up_revert/up_total*100:.1f}%)")
        print(f"  Continues UP:  {up_continue:>4} ({up_continue/up_total*100:.1f}%)")
        print(f"  {'✅ MEAN REVERSION WORKS' if up_revert/up_total > 0.55 else '❌ NO MEAN REVERSION'}")

    print(f"\nExtended DOWN (-1% from EMA) - expect mean reversion UP:")
    dn_revert, dn_continue = check_reversion(extended_down, 'up')
    dn_total = dn_revert + dn_continue
    if dn_total > 0:
        print(f"  Reverts UP:    {dn_revert:>4} ({dn_revert/dn_total*100:.1f}%)")
        print(f"  Continues DOWN: {dn_continue:>4} ({dn_continue/dn_total*100:.1f}%)")
        print(f"  {'✅ MEAN REVERSION WORKS' if dn_revert/dn_total > 0.55 else '❌ NO MEAN REVERSION'}")

    return {}


def analyze_time_patterns(df: pd.DataFrame) -> Dict:
    """
    QUESTION 5: Are there real TIME-BASED patterns?

    Not just "trade at 8am" but WHY certain times work.
    """
    print("\n" + "="*70)
    print("ANALYSIS 5: TIME-BASED PATTERNS")
    print("Question: What ACTUALLY happens at different times?")
    print("="*70)

    df = df.copy()
    df['hour'] = df.index.hour
    df['return'] = df['close'].pct_change() * 100
    df['volatility'] = df['return'].abs()
    df['is_green'] = df['close'] > df['open']

    print("\nHourly characteristics:")
    print(f"{'Hour':<6} | {'Avg Move':>8} | {'Volatility':>10} | {'Green%':>7} | {'Pattern':>15}")
    print("-" * 60)

    for hour in range(24):
        hour_data = df[df['hour'] == hour]
        if len(hour_data) < 10:
            continue

        avg_return = hour_data['return'].mean()
        volatility = hour_data['volatility'].mean()
        green_pct = hour_data['is_green'].mean() * 100

        # Classify pattern
        if volatility > df['volatility'].mean() * 1.5:
            pattern = "HIGH VOLATILITY"
        elif green_pct > 55:
            pattern = "BULLISH BIAS"
        elif green_pct < 45:
            pattern = "BEARISH BIAS"
        else:
            pattern = "NEUTRAL"

        print(f"{hour:02d}:00  | {avg_return:>+7.3f}% | {volatility:>9.3f}% | {green_pct:>6.1f}% | {pattern}")

    return {}


def main():
    print("="*70)
    print("MARKET LOGIC ANALYZER")
    print("Finding WHY price moves, not just WHEN")
    print("="*70)

    for symbol in SYMBOLS:
        print(f"\n{'#'*70}")
        print(f"# {symbol}")
        print(f"{'#'*70}")

        try:
            df = fetch_data(symbol, '1m', LOOKBACK_DAYS)

            if len(df) < 500:
                print(f"Not enough data for {symbol}")
                continue

            analyze_breakout_continuation(df)
            analyze_volume_prediction(df)
            analyze_momentum_continuation(df)
            analyze_mean_reversion(df)
            analyze_time_patterns(df)

        except Exception as e:
            print(f"Error analyzing {symbol}: {e}")
            import traceback
            traceback.print_exc()

    print("\n" + "="*70)
    print("CONCLUSION")
    print("="*70)
    print("""
Based on this analysis, look for:

1. BREAKOUT: If continuation rate > 55%, breakout strategy has edge
2. VOLUME: If volume spikes predict direction > 55%, use volume
3. MOMENTUM: If 4th candle continues > 55%, use momentum
4. MEAN REVERSION: If reversion rate > 55%, trade against extremes
5. TIME: Note which hours have actual directional bias

The ONLY valid edge is one with >55% hit rate AND positive expectancy.
Everything else is noise.
""")


if __name__ == "__main__":
    main()
