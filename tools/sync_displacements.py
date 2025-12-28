#!/usr/bin/env python3
"""
Sync displacements from research.db (SQLite) to displacements.json
This allows the retrace analyzer to process them.
"""
import sqlite3
import json
import os

def main():
    db_path = 'analysis/research.db'
    json_path = 'analysis/displacements.json'

    if not os.path.exists(db_path):
        print(f"Error: {db_path} not found")
        return

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    cursor = conn.execute("""
        SELECT * FROM displacements ORDER BY id
    """)

    displacements = []
    for row in cursor.fetchall():
        # Parse displacement_type (stored as JSON or comma-separated)
        dtype = row['displacement_type']
        if dtype:
            try:
                dtype_list = json.loads(dtype) if dtype.startswith('[') else dtype.split(',')
            except:
                dtype_list = dtype.split(',')
        else:
            dtype_list = []

        disp = {
            'id': row['id'],
            'symbol': row['symbol'],
            'timestamp': row['timestamp'],
            'direction': row['direction'],
            'open': row['open_price'],
            'high': row['high_price'],
            'low': row['low_price'],
            'close': row['close_price'],
            'volume': row['volume'],
            'body_ratio': row['body_ratio'],
            'volume_ratio': row['volume_ratio'],
            'displacement_pct': row['displacement_pct'],
            'displacement_type': dtype_list,
            'analyzed': False,  # Mark as not analyzed so retrace analyzer picks them up
            'continuation': None,
            'max_retrace_pct': None,
            'max_favorable_pct': None,
            'max_adverse_pct': None
        }
        displacements.append(disp)

    conn.close()

    # Save to JSON
    os.makedirs(os.path.dirname(json_path) or '.', exist_ok=True)
    with open(json_path, 'w') as f:
        json.dump(displacements, f, indent=2, default=str)

    print(f"Synced {len(displacements)} displacements to {json_path}")
    print(f"Retrace analyzer will now process them on next cycle")

if __name__ == "__main__":
    main()
