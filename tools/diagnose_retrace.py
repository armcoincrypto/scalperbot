#!/usr/bin/env python3
"""
Complete diagnostic test for retrace analysis pipeline.
Tests each component step by step to find exactly where the failure occurs.
"""
import sys
sys.path.insert(0, '/home/user/scalperbot')

import json
import pandas as pd
from datetime import datetime, timezone, timedelta
import sqlite3

print("=" * 60)
print("RETRACE ANALYSIS DIAGNOSTIC")
print("=" * 60)

# Step 1: Check displacements.json
print("\n[1] Checking displacements.json...")
try:
    with open('analysis/displacements.json') as f:
        disps = json.load(f)
    print(f"    Found {len(disps)} displacements")
    unanalyzed = [d for d in disps if not d.get('analyzed', False)]
    print(f"    Unanalyzed: {len(unanalyzed)}")
    if disps:
        sample = disps[0]
        print(f"    Sample timestamp: {sample['timestamp']} (type: {type(sample['timestamp']).__name__})")
except Exception as e:
    print(f"    ERROR: {e}")
    disps = []

# Step 2: Test timestamp parsing
print("\n[2] Testing timestamp parsing...")
if disps:
    ts = disps[0]['timestamp']
    try:
        if isinstance(ts, (int, float)):
            disp_time = pd.to_datetime(ts, unit='ms', utc=True)
        elif isinstance(ts, str) and ts.isdigit():
            disp_time = pd.to_datetime(int(ts), unit='ms', utc=True)
        else:
            disp_time = pd.to_datetime(ts, utc=True)
        print(f"    Parsed OK: {disp_time}")
        print(f"    Is UTC-aware: {disp_time.tzinfo is not None}")
    except Exception as e:
        print(f"    ERROR parsing timestamp: {e}")

# Step 3: Check candle store data
print("\n[3] Testing candle store...")
try:
    from datafeed.candle_store import CandleStore
    import ccxt

    # Fetch fresh data
    exchange = ccxt.mexc({'enableRateLimit': True})
    symbol = 'XLM/USDT'
    since = int((datetime.now(timezone.utc) - timedelta(hours=12)).timestamp() * 1000)

    candles = exchange.fetch_ohlcv(symbol, '1m', since=since, limit=500)
    print(f"    Fetched {len(candles)} candles from exchange")

    # Create DataFrame like candle_store does
    df = pd.DataFrame(candles, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
    df['datetime'] = pd.to_datetime(df['timestamp'], unit='ms', utc=True)
    df = df.set_index('datetime')

    print(f"    DataFrame range: {df.index.min()} to {df.index.max()}")
    print(f"    Index is UTC-aware: {df.index.tzinfo is not None}")

except Exception as e:
    print(f"    ERROR: {e}")
    df = None

# Step 4: Test the comparison
print("\n[4] Testing timestamp comparison...")
if disps and df is not None:
    ts = disps[0]['timestamp']
    if isinstance(ts, str) and ts.isdigit():
        disp_time = pd.to_datetime(int(ts), unit='ms', utc=True)
    else:
        disp_time = pd.to_datetime(ts, unit='ms', utc=True) if isinstance(ts, (int, float)) else pd.to_datetime(ts, utc=True)

    print(f"    Displacement time: {disp_time}")
    print(f"    DataFrame start: {df.index.min()}")
    print(f"    DataFrame end: {df.index.max()}")

    try:
        future_data = df[df.index > disp_time]
        print(f"    Future candles found: {len(future_data)}")
        if len(future_data) < 60:
            print(f"    ⚠️  Need 60 candles but only have {len(future_data)}")
            print(f"    This displacement is too recent or data doesn't cover it")
        else:
            print(f"    ✅ Enough data for analysis!")
    except Exception as e:
        print(f"    ERROR comparing: {e}")

# Step 5: Check research.db timestamps
print("\n[5] Checking research.db timestamps...")
try:
    conn = sqlite3.connect('analysis/research.db')
    cursor = conn.execute('SELECT id, symbol, timestamp FROM displacements ORDER BY id LIMIT 3')
    rows = cursor.fetchall()
    print(f"    Sample from DB:")
    for row in rows:
        ts_val = row[2]
        print(f"      ID={row[0]} {row[1]}: {ts_val}")
        # Try to parse
        if isinstance(ts_val, str) and ts_val.isdigit():
            parsed = pd.to_datetime(int(ts_val), unit='ms', utc=True)
            print(f"         -> Parsed: {parsed}")
    conn.close()
except Exception as e:
    print(f"    ERROR: {e}")

# Step 6: Test full retrace analysis on one displacement
print("\n[6] Running full retrace analysis test...")
if disps and df is not None and len(unanalyzed) > 0:
    from analysis.retrace import RetraceAnalyzer
    analyzer = RetraceAnalyzer()

    # Find a displacement old enough to have future data
    for d in unanalyzed:
        ts = d['timestamp']
        if isinstance(ts, str) and ts.isdigit():
            disp_time = pd.to_datetime(int(ts), unit='ms', utc=True)
        else:
            disp_time = pd.to_datetime(ts, unit='ms', utc=True) if isinstance(ts, (int, float)) else pd.to_datetime(ts, utc=True)

        if disp_time < df.index.min():
            print(f"    Displacement {d['id']} at {disp_time} is before data range")
            continue

        future = df[df.index > disp_time]
        if len(future) >= 60:
            print(f"    Testing displacement {d['id']} at {disp_time}...")
            result = analyzer.analyze_displacement(d, df)
            if result:
                print(f"    ✅ SUCCESS! Got result with {len(result.get('timeframe_analysis', {}))} timeframes")
            else:
                print(f"    ❌ FAILED - analyze_displacement returned None")
            break
    else:
        print("    ⚠️  No displacement has 60+ minutes of future data yet")

print("\n" + "=" * 60)
print("DIAGNOSTIC COMPLETE")
print("=" * 60)
