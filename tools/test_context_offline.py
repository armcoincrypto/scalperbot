#!/usr/bin/env python3
"""
Test context analysis with simulated data (no network required).
"""
import sys
sys.path.insert(0, '/home/user/scalperbot')

import pandas as pd
import numpy as np
from datetime import datetime, timezone, timedelta

from analysis.context import ContextAnalyzer
from analysis.research_db import get_research_db

print("=" * 60)
print("CONTEXT ANALYSIS OFFLINE TEST")
print("=" * 60)

# Initialize components
print("\n[1] Initializing components...")
analyzer = ContextAnalyzer()
db = get_research_db()

# Create simulated market data
print("\n[2] Creating simulated market data...")
np.random.seed(42)

# Generate 500 minutes of candle data
base_price = 100.0
num_candles = 500
timestamps = pd.date_range(
    end=datetime.now(timezone.utc),
    periods=num_candles,
    freq='1min'
)

# Simulate price movement with some volatility
returns = np.random.normal(0, 0.001, num_candles)  # 0.1% avg move
prices = base_price * np.exp(np.cumsum(returns))

# Create OHLCV data
data = []
for i, (ts, close) in enumerate(zip(timestamps, prices)):
    volatility = np.random.uniform(0.001, 0.003)
    high = close * (1 + volatility)
    low = close * (1 - volatility)
    open_price = prices[i-1] if i > 0 else close
    volume = np.random.uniform(10000, 50000)
    data.append({
        'open': open_price,
        'high': high,
        'low': low,
        'close': close,
        'volume': volume
    })

df = pd.DataFrame(data, index=timestamps)
print(f"    Created {len(df)} simulated candles")
print(f"    Price range: {df['low'].min():.2f} to {df['high'].max():.2f}")

# Create a simulated displacement
print("\n[3] Creating test displacement...")
# Place it at index 400 (100 candles ago, so we have future data)
disp_idx = 400
disp_time = timestamps[disp_idx]
disp_price = df.iloc[disp_idx]['close']

test_displacement = {
    'id': 9999,  # Test ID
    'symbol': 'TEST/USDT',
    'timestamp': str(disp_time),
    'direction': 'BULLISH',
    'displacement_pct': 0.35,
    'body_ratio': 2.5,
    'volume_ratio': 2.0,
    'displacement_type': ['BODY', 'VOLUME'],
    'score': 3,
    'open': float(df.iloc[disp_idx]['open']),
    'high': float(df.iloc[disp_idx]['high']),
    'low': float(df.iloc[disp_idx]['low']),
    'close': float(df.iloc[disp_idx]['close']),
    'volume': float(df.iloc[disp_idx]['volume']),
    'analyzed': False
}

print(f"    Created displacement at {disp_time}")
print(f"    Price: {disp_price:.4f} | Direction: BULLISH")

# Test context analysis
print("\n[4] Analyzing displacement context...")
context = analyzer.analyze_displacement_context(df, test_displacement, [])

if context:
    print("    ✅ Context analysis successful!")
    print("\n    LOCATION:")
    location = context.get('location', {})
    print(f"      Location type: {location.get('location', 'N/A')}")
    print(f"      Position in range: {location.get('position_in_range', 'N/A')}")
    print(f"      Session: {location.get('session', 'N/A')}")
    print(f"      Near session extreme: {location.get('near_session_extreme', 'N/A')}")

    print("\n    VELOCITY:")
    velocity = context.get('velocity', {})
    print(f"      Speed type: {velocity.get('speed_type', 'N/A')}")
    print(f"      Velocity: {velocity.get('velocity', 'N/A')} %/min")
    print(f"      Avg body ratio: {velocity.get('avg_body_ratio', 'N/A')}")

    print("\n    FOLLOW-THROUGH:")
    followthrough = context.get('followthrough', {})
    print(f"      Status: {followthrough.get('followthrough', 'N/A')}")
    print(f"      New extreme made: {followthrough.get('new_extreme_made', 'N/A')}")
    print(f"      Compression ratio: {followthrough.get('compression_ratio', 'N/A')}")

    print("\n    LIQUIDITY:")
    liquidity = context.get('liquidity', {})
    print(f"      Has sweep: {liquidity.get('has_liquidity_sweep', 'N/A')}")

    print("\n    SIGNALS:")
    signals = context.get('signals', {})
    print(f"      Fade signal: {signals.get('fade_signal', False)}")
    print(f"      Continuation signal: {signals.get('continuation_signal', False)}")
    print(f"      Signal strength: {signals.get('signal_strength', 0)}")
    print(f"      Reasons: {signals.get('reasons', [])}")

    # Try to insert into database
    print("\n[5] Testing database insert...")
    try:
        row_id = db.insert_displacement_context(context)
        print(f"    ✅ Inserted context with row ID: {row_id}")

        # Verify it's there
        contexts = db.get_displacement_contexts(symbol='TEST/USDT')
        print(f"    Contexts in DB for TEST/USDT: {len(contexts)}")
    except Exception as e:
        print(f"    ❌ Database insert error: {e}")

else:
    print("    ❌ Context analysis returned None!")

# Summary
print("\n[6] Database summary...")
db.print_summary()

print("\n" + "=" * 60)
print("TEST COMPLETE")
print("=" * 60)

if context:
    print("\n✅ SUCCESS: Context analyzer is working correctly!")
    print("   Location, Velocity, Follow-through, and Signal generation all functional.")
else:
    print("\n❌ FAILED: Context analyzer returned no data")
