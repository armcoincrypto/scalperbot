#!/usr/bin/env python3
"""
Reset research data to start fresh with correct timestamps.
Clears displacements and retraces that have bad timestamp data.
"""
import sqlite3
import os

def main():
    print("Resetting research data with bad timestamps...")

    # 1. Clear displacements.json
    json_path = 'analysis/displacements.json'
    if os.path.exists(json_path):
        os.remove(json_path)
        print(f"  Deleted {json_path}")

    # 2. Clear retrace_results.json
    retrace_path = 'analysis/retrace_results.json'
    if os.path.exists(retrace_path):
        os.remove(retrace_path)
        print(f"  Deleted {retrace_path}")

    # 3. Clear displacements and retraces from SQLite (keep other data)
    db_path = 'analysis/research.db'
    if os.path.exists(db_path):
        conn = sqlite3.connect(db_path)

        # Check current counts
        for table in ['displacements', 'retraces']:
            cursor = conn.execute(f'SELECT COUNT(*) FROM {table}')
            count = cursor.fetchone()[0]
            print(f"  {table}: {count} rows (will be cleared)")

        # Clear the tables
        conn.execute('DELETE FROM displacements')
        conn.execute('DELETE FROM retraces')
        conn.commit()

        print("  Cleared displacements and retraces from research.db")

        # Verify
        for table in ['displacements', 'retraces', 'liquidity_events', 'regimes', 'simulated_trades']:
            cursor = conn.execute(f'SELECT COUNT(*) FROM {table}')
            count = cursor.fetchone()[0]
            print(f"  {table}: {count} rows remaining")

        conn.close()

    print("\nDone! Restart the bot to collect fresh data with correct timestamps.")
    print("Run: systemctl restart scalperbot")

if __name__ == "__main__":
    main()
