#!/usr/bin/env python3
"""
Fix Orphaned Trades Utility
Identifies and optionally fixes trades that were FILLED but never had positions created
(This happened due to a bug where MEXC returned None values for order fields)
"""
import sqlite3
import argparse
from datetime import datetime
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent.parent))


class OrphanedTradesFixer:
    """Fix orphaned trades in ScalperBot database"""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self.conn = None
        self._connect()

    def _connect(self):
        """Connect to database"""
        if not Path(self.db_path).exists():
            raise FileNotFoundError(f"Database not found: {self.db_path}")
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row

    def close(self):
        """Close database connection"""
        if self.conn:
            self.conn.close()

    def find_orphaned_trades(self) -> list:
        """Find FILLED trades without corresponding positions"""
        query = """
            SELECT t.* FROM trades t
            LEFT JOIN positions p ON t.id = p.trade_id
            WHERE t.status = 'FILLED' AND p.id IS NULL
            ORDER BY t.created_at DESC
        """
        cursor = self.conn.execute(query)
        return [dict(row) for row in cursor.fetchall()]

    def analyze_orphaned_trades(self, trades: list) -> dict:
        """Analyze orphaned trades to understand the scope of the problem"""
        if not trades:
            return {
                'count': 0,
                'total_notional': 0,
                'symbols': {},
                'date_range': None
            }

        symbols = {}
        for t in trades:
            symbol = t['symbol']
            if symbol not in symbols:
                symbols[symbol] = {'count': 0, 'notional': 0}
            symbols[symbol]['count'] += 1
            symbols[symbol]['notional'] += t['notional']

        dates = [t['created_at'] for t in trades if t['created_at']]

        return {
            'count': len(trades),
            'total_notional': sum(t['notional'] for t in trades),
            'symbols': symbols,
            'date_range': {
                'earliest': min(dates) if dates else None,
                'latest': max(dates) if dates else None
            }
        }

    def mark_as_phantom(self, trade_ids: list, dry_run: bool = True) -> int:
        """Mark orphaned trades as PHANTOM_CLOSED (can't recover exact exit)"""
        if not trade_ids:
            return 0

        if dry_run:
            print(f"[DRY RUN] Would mark {len(trade_ids)} trades as PHANTOM_CLOSED")
            return 0

        placeholders = ','.join(['?' for _ in trade_ids])
        self.conn.execute(
            f"UPDATE trades SET status = 'PHANTOM_CLOSED' WHERE id IN ({placeholders})",
            trade_ids
        )
        self.conn.commit()
        return len(trade_ids)

    def create_estimated_positions(self, trades: list, dry_run: bool = True,
                                   tp_pct: float = 2.0, sl_pct: float = 1.0) -> int:
        """Create position records for orphaned trades (for tracking purposes only)"""
        if not trades:
            return 0

        if dry_run:
            print(f"[DRY RUN] Would create {len(trades)} position records")
            return 0

        created = 0
        for t in trades:
            entry_price = t['price']
            tp_price = entry_price * (1 + tp_pct / 100)
            sl_price = entry_price * (1 - sl_pct / 100)

            try:
                self.conn.execute("""
                    INSERT INTO positions (
                        trade_id, symbol, side, entry_price, quantity, notional,
                        take_profit_price, stop_loss_price, highest_price,
                        status, opened_at, exit_reason, pnl
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'ORPHANED', ?, 'ORPHANED_NO_EXIT', 0)
                """, (
                    t['id'],
                    t['symbol'],
                    t['side'],
                    entry_price,
                    t['quantity'],
                    t['notional'],
                    tp_price,
                    sl_price,
                    entry_price,
                    t['timestamp']
                ))
                created += 1
            except Exception as e:
                print(f"Error creating position for trade {t['id']}: {e}")

        self.conn.commit()
        return created

    def print_report(self):
        """Print report of orphaned trades"""
        orphans = self.find_orphaned_trades()
        analysis = self.analyze_orphaned_trades(orphans)

        print("=" * 70)
        print("ORPHANED TRADES REPORT")
        print("=" * 70)

        if not orphans:
            print("\nNo orphaned trades found!")
            print("All FILLED trades have corresponding position records.")
            return

        print(f"\nFound {analysis['count']} orphaned FILLED trades")
        print(f"Total notional value: ${analysis['total_notional']:.2f}")

        if analysis['date_range']:
            print(f"Date range: {analysis['date_range']['earliest']} to {analysis['date_range']['latest']}")

        print("\nBreakdown by symbol:")
        for symbol, stats in sorted(analysis['symbols'].items(), key=lambda x: -x[1]['count']):
            print(f"  {symbol}: {stats['count']} trades (${stats['notional']:.2f})")

        print("\nOrphaned trade details:")
        print("-" * 70)
        for t in orphans[:20]:  # Show first 20
            print(f"  ID={t['id']} | {t['symbol']} | {t['side']} | "
                  f"Price={t['price']:.4f} | Qty={t['quantity']:.6f} | "
                  f"Notional=${t['notional']:.2f} | {t['created_at']}")

        if len(orphans) > 20:
            print(f"  ... and {len(orphans) - 20} more")

        print("\n" + "=" * 70)
        print("EXPLANATION")
        print("=" * 70)
        print("""
These trades were marked as FILLED but no position was created in the
positions table. This happened because:

1. The order was placed successfully on MEXC
2. MEXC returned the order confirmation
3. But MEXC returned NULL for 'average' and 'filled' fields
4. The code tried to multiply NULL * quantity, causing a TypeError
5. The position creation code was skipped due to the exception

IMPACT:
- These positions were NEVER monitored for TP/SL
- These positions may still be open on the exchange
- PnL was never calculated or recorded

RECOMMENDED ACTIONS:
1. Check MEXC account for any open positions
2. Run with --fix to mark these as PHANTOM_CLOSED
3. Manually close any remaining positions on MEXC
""")


def main():
    parser = argparse.ArgumentParser(description='Fix orphaned trades in ScalperBot database')
    parser.add_argument('--db', type=str, default='scalperbot/trades.db',
                        help='Path to trades database')
    parser.add_argument('--fix', action='store_true',
                        help='Actually fix the trades (default: report only)')
    parser.add_argument('--create-positions', action='store_true',
                        help='Create position records for orphaned trades')

    args = parser.parse_args()

    try:
        fixer = OrphanedTradesFixer(args.db)
        fixer.print_report()

        if args.fix:
            orphans = fixer.find_orphaned_trades()
            if orphans:
                trade_ids = [t['id'] for t in orphans]
                count = fixer.mark_as_phantom(trade_ids, dry_run=False)
                print(f"\nMarked {count} trades as PHANTOM_CLOSED")

        if args.create_positions:
            orphans = fixer.find_orphaned_trades()
            if orphans:
                count = fixer.create_estimated_positions(orphans, dry_run=False)
                print(f"\nCreated {count} orphaned position records")

        fixer.close()

    except FileNotFoundError as e:
        print(f"Error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
