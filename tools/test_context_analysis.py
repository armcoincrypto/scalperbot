#!/usr/bin/env python3
"""
Test the context analysis pipeline end-to-end.
Creates test displacements and analyzes their context.
"""
import sys
sys.path.insert(0, '/home/user/scalperbot')

import pandas as pd
import ccxt
from datetime import datetime, timezone, timedelta

from analysis.displacement import DisplacementDetector
from analysis.context import ContextAnalyzer
from analysis.research_db import get_research_db

print("=" * 60)
print("CONTEXT ANALYSIS END-TO-END TEST")
print("=" * 60)

# Initialize components
print("\n[1] Initializing components...")
detector = DisplacementDetector()
analyzer = ContextAnalyzer()
db = get_research_db()

# Fetch real market data
print("\n[2] Fetching market data...")
exchange = ccxt.mexc({'enableRateLimit': True})
symbol = 'XLM/USDT'  # Good for testing (lower volume, more displacement opportunities)

# Fetch 24 hours of 1m candles
since = int((datetime.now(timezone.utc) - timedelta(hours=24)).timestamp() * 1000)
all_candles = []
while True:
    candles = exchange.fetch_ohlcv(symbol, '1m', since=since, limit=1000)
    if not candles:
        break
    all_candles.extend(candles)
    since = candles[-1][0] + 60000
    if len(candles) < 100:
        break

print(f"    Fetched {len(all_candles)} candles")

if len(all_candles) < 100:
    print("    ERROR: Not enough candles")
    sys.exit(1)

# Create DataFrame
df = pd.DataFrame(all_candles, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
df['datetime'] = pd.to_datetime(df['timestamp'], unit='ms', utc=True)
df = df.set_index('datetime')
print(f"    Data range: {df.index.min()} to {df.index.max()}")

# Detect displacements
print("\n[3] Detecting displacements...")
new_displacements = detector.process_candle_data(df, symbol)
print(f"    Found {len(new_displacements)} NEW displacements")
print(f"    Total displacements in memory: {len(detector.displacements)}")

if len(detector.displacements) == 0:
    print("    ⚠️ No displacements to analyze")
    print("    The market may be quiet. Waiting for more volatile period.")
    sys.exit(0)

# Show detected displacements
print("\n    Detected displacements:")
for d in detector.displacements[-5:]:  # Show last 5
    print(f"      {d['id']}: {d['timestamp']} | {d['direction']} | {d['displacement_pct']:.2f}%")

# Analyze context for each displacement
print("\n[4] Analyzing displacement context...")
contexts_created = 0
for disp in detector.displacements:
    # Check if already analyzed
    existing = db.get_displacement_contexts(symbol=symbol)
    analyzed_ids = {c.get('displacement_id') for c in existing}
    if disp.get('id') in analyzed_ids:
        print(f"    {disp['id']}: Already analyzed, skipping")
        continue

    # Analyze context
    context = analyzer.analyze_displacement_context(df, disp, [])

    if context:
        # Insert into database
        db.insert_displacement_context(context)
        contexts_created += 1

        location = context.get('location', {})
        velocity = context.get('velocity', {})
        followthrough = context.get('followthrough', {})
        signals = context.get('signals', {})

        print(f"    ✅ {disp['id']}: {location.get('location', 'N/A')} | "
              f"{velocity.get('speed_type', 'N/A')} | "
              f"{followthrough.get('followthrough', 'N/A')} | "
              f"Fade={signals.get('fade_signal', False)}")
    else:
        print(f"    ❌ {disp['id']}: Context analysis returned None")

print(f"\n    Created {contexts_created} new context analyses")

# Verify in database
print("\n[5] Verifying database...")
contexts = db.get_displacement_contexts(symbol=symbol)
print(f"    Total contexts in DB for {symbol}: {len(contexts)}")

# Summary
db.print_summary()

print("\n" + "=" * 60)
print("TEST COMPLETE")
print("=" * 60)

if contexts_created > 0:
    print(f"\n✅ SUCCESS: Created {contexts_created} context analyses")
    print("   The context analyzer is working correctly!")
else:
    print("\n⚠️ No new contexts created (may already exist or no displacements)")
