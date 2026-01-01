#!/usr/bin/env python3
"""
Diagnostic script to identify why context analysis isn't populating data.
"""
import sys
sys.path.insert(0, '/home/user/scalperbot')

import json
import os
from collections import Counter

print("=" * 60)
print("CONTEXT ANALYSIS DIAGNOSTIC")
print("=" * 60)

# Step 1: Check what symbols are in trading_pairs
print("\n[1] Checking trading_pairs in config...")
try:
    from config import settings
    trading_pairs = settings.trading_pairs
    print(f"    Trading pairs: {trading_pairs}")
except Exception as e:
    print(f"    ERROR: {e}")
    trading_pairs = []

# Step 2: Check what symbols are in displacements.json
print("\n[2] Checking displacements.json...")
json_path = 'analysis/displacements.json'
if os.path.exists(json_path):
    with open(json_path) as f:
        disps = json.load(f)
    print(f"    Total displacements: {len(disps)}")

    # Count by symbol
    symbol_counts = Counter(d.get('symbol') for d in disps)
    print(f"    Symbols in displacements:")
    for sym, count in symbol_counts.most_common():
        in_config = "✅" if sym in trading_pairs else "❌ NOT IN CONFIG"
        print(f"      {sym}: {count} displacements {in_config}")

    # Show last 20 displacements
    print(f"\n    Last 20 displacements (what main.py uses):")
    last_20 = disps[-20:]
    last_20_symbols = Counter(d.get('symbol') for d in last_20)
    for sym, count in last_20_symbols.most_common():
        print(f"      {sym}: {count}")
else:
    print(f"    ERROR: {json_path} not found")
    disps = []

# Step 3: Check DisplacementDetector in-memory state
print("\n[3] Checking DisplacementDetector in-memory state...")
try:
    from analysis.displacement import DisplacementDetector
    detector = DisplacementDetector()
    print(f"    Loaded {len(detector.displacements)} displacements from JSON")

    if detector.displacements:
        mem_symbols = Counter(d.get('symbol') for d in detector.displacements)
        print(f"    Symbols in memory:")
        for sym, count in mem_symbols.most_common():
            print(f"      {sym}: {count}")
except Exception as e:
    print(f"    ERROR: {e}")

# Step 4: Check displacement_context table
print("\n[4] Checking displacement_context table...")
try:
    import sqlite3
    conn = sqlite3.connect('analysis/research.db')
    cursor = conn.execute('SELECT COUNT(*) FROM displacement_context')
    count = cursor.fetchone()[0]
    print(f"    Rows in displacement_context: {count}")

    if count > 0:
        cursor = conn.execute('SELECT symbol, COUNT(*) FROM displacement_context GROUP BY symbol')
        for row in cursor.fetchall():
            print(f"      {row[0]}: {row[1]}")
    conn.close()
except Exception as e:
    print(f"    ERROR: {e}")

# Step 5: Simulate what main.py does
print("\n[5] Simulating main.py context analysis logic...")
if disps and trading_pairs:
    for symbol in trading_pairs:
        # This is what main.py does
        recent_disps = disps[-20:]
        symbol_disps = [d for d in recent_disps if d.get('symbol') == symbol]
        print(f"    {symbol}: Would analyze {len(symbol_disps)} displacements")

        if len(symbol_disps) == 0:
            print(f"      ⚠️  PROBLEM: No displacements in last 20 for this symbol!")
            # Show what symbols ARE in last 20
            other_symbols = set(d.get('symbol') for d in recent_disps)
            print(f"      Last 20 contains: {other_symbols}")

# Step 6: Test context analyzer directly
print("\n[6] Testing context analyzer directly on first displacement...")
if disps:
    try:
        from analysis.context import ContextAnalyzer
        from datafeed.candle_store import CandleStore
        import ccxt
        import pandas as pd
        from datetime import datetime, timezone, timedelta

        analyzer = ContextAnalyzer()

        # Get a displacement to test
        test_disp = disps[0]
        symbol = test_disp['symbol']
        print(f"    Testing with displacement {test_disp['id']} for {symbol}")

        # Fetch candle data
        exchange = ccxt.mexc({'enableRateLimit': True})
        since = int((datetime.now(timezone.utc) - timedelta(hours=12)).timestamp() * 1000)
        candles = exchange.fetch_ohlcv(symbol, '1m', since=since, limit=500)
        print(f"    Fetched {len(candles)} candles")

        df = pd.DataFrame(candles, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        df['datetime'] = pd.to_datetime(df['timestamp'], unit='ms', utc=True)
        df = df.set_index('datetime')

        # Try to analyze
        context = analyzer.analyze_displacement_context(df, test_disp, [])

        if context:
            print(f"    ✅ SUCCESS! Context analysis returned data:")
            print(f"       Location: {context.get('location', {}).get('location')}")
            print(f"       Speed: {context.get('velocity', {}).get('speed_type')}")
            print(f"       Followthrough: {context.get('followthrough', {}).get('followthrough')}")
            print(f"       Fade signal: {context.get('signals', {}).get('fade_signal')}")
        else:
            print(f"    ❌ FAILED: analyze_displacement_context returned None/empty")

    except Exception as e:
        import traceback
        print(f"    ERROR: {e}")
        traceback.print_exc()

print("\n" + "=" * 60)
print("DIAGNOSIS COMPLETE")
print("=" * 60)

# Provide recommendation
print("\n📋 RECOMMENDATIONS:")
if disps and trading_pairs:
    # Check the fundamental issue
    last_20 = disps[-20:]
    last_20_symbols = set(d.get('symbol') for d in last_20)
    missing_symbols = set(trading_pairs) - last_20_symbols

    if missing_symbols:
        print(f"   ❌ ISSUE FOUND: main.py only uses last 20 displacements total")
        print(f"      These symbols are in config but NOT in last 20: {missing_symbols}")
        print(f"      FIX: Change main.py to use ALL displacements, not just [-20:]")
    else:
        print(f"   ✅ All trading pairs have displacements in last 20")
        print(f"   Check for other issues (exceptions, candle data, etc.)")
