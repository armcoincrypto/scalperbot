#!/usr/bin/env python3
"""Debug script to test retrace analyzer"""
import sys
sys.path.insert(0, '/home/user/scalperbot')

import json
import pandas as pd
from datetime import datetime, timezone, timedelta
import ccxt

from analysis.retrace import RetraceAnalyzer

def main():
    # Load displacements
    with open('analysis/displacements.json') as f:
        disps = json.load(f)

    print(f"Total displacements in JSON: {len(disps)}")

    # Check analyzed status
    unanalyzed = [d for d in disps if not d.get('analyzed', False)]
    print(f"Unanalyzed: {len(unanalyzed)}")

    if not unanalyzed:
        print("All displacements already analyzed!")
        return

    # Show first few unanalyzed
    print("\nFirst 3 unanalyzed displacements:")
    for d in unanalyzed[:3]:
        print(f"  {d['symbol']}: ts={d['timestamp']} dir={d['direction']}")

    # Fetch price data for one symbol
    symbol = unanalyzed[0]['symbol']
    print(f"\nFetching data for {symbol}...")

    exchange = ccxt.mexc({'enableRateLimit': True})
    since = int((datetime.now(timezone.utc) - timedelta(days=3)).timestamp() * 1000)

    all_candles = []
    while True:
        candles = exchange.fetch_ohlcv(symbol, '1m', since=since, limit=1000)
        if not candles:
            break
        all_candles.extend(candles)
        since = candles[-1][0] + 60000
        if len(candles) < 100:
            break

    print(f"Fetched {len(all_candles)} candles")

    if not all_candles:
        print("No candles fetched!")
        return

    df = pd.DataFrame(all_candles, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
    df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms', utc=True)
    df.set_index('timestamp', inplace=True)

    print(f"DataFrame range: {df.index.min()} to {df.index.max()}")

    # Try to analyze one displacement manually
    analyzer = RetraceAnalyzer()

    test_disp = unanalyzed[0]
    print(f"\nTesting displacement: {test_disp['timestamp']}")

    result = analyzer.analyze_displacement(test_disp, df)

    if result:
        print(f"SUCCESS! Result: {result}")
    else:
        print("FAILED - returned None")

        # Debug why
        ts = test_disp['timestamp']
        print(f"  Timestamp raw: {ts} (type: {type(ts)})")

        try:
            if isinstance(ts, str) and ts.isdigit():
                disp_time = pd.to_datetime(int(ts), unit='ms', utc=True)
            else:
                disp_time = pd.to_datetime(ts, utc=True)
            print(f"  Parsed time: {disp_time}")

            future_data = df[df.index > disp_time]
            print(f"  Future candles available: {len(future_data)} (need 60)")
        except Exception as e:
            print(f"  Parse error: {e}")

if __name__ == "__main__":
    main()
