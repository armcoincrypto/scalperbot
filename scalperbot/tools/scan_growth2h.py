"""
CLI tool to run the Growth 2H Scanner.

Scans MEXC symbols for >=20% growth within any 2-hour window
over the last 10 days. Saves results to SQLite database.

Usage:
    python -m scalperbot.tools.scan_growth2h --db scalperbot.db --top 20
    python -m scalperbot.tools.scan_growth2h --threshold 15 --days 7
"""

from __future__ import annotations

import argparse
import asyncio
import sqlite3
import time

from scalperbot.core.growth2h_scanner import Growth2HScanner, MexcPublicClient, GrowthEvent


def ensure_tables(db_path: str) -> None:
    """Create required tables if they don't exist."""
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.cursor()

        # Main events table
        cur.execute("""
        CREATE TABLE IF NOT EXISTS growth2h_events (
            symbol TEXT PRIMARY KEY,
            quote_volume_24h REAL NOT NULL,
            best_growth_pct REAL NOT NULL,
            window_start_ms INTEGER NOT NULL,
            window_end_ms INTEGER NOT NULL,
            checked_at_ms INTEGER NOT NULL
        )
        """)

        # Scan state table for resumability
        cur.execute("""
        CREATE TABLE IF NOT EXISTS growth2h_scan_state (
            symbol TEXT PRIMARY KEY,
            last_checked_ms INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            error TEXT
        )
        """)

        # Index for faster queries
        cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_growth2h_events_growth
        ON growth2h_events(best_growth_pct DESC)
        """)

        conn.commit()
    finally:
        conn.close()


def save_events(db_path: str, events: list[GrowthEvent]) -> None:
    """Save growth events to database."""
    now_ms = int(time.time() * 1000)
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.cursor()
        for ev in events:
            cur.execute("""
            INSERT INTO growth2h_events(
                symbol, quote_volume_24h, best_growth_pct,
                window_start_ms, window_end_ms, checked_at_ms
            )
            VALUES(?, ?, ?, ?, ?, ?)
            ON CONFLICT(symbol) DO UPDATE SET
                quote_volume_24h = excluded.quote_volume_24h,
                best_growth_pct = excluded.best_growth_pct,
                window_start_ms = excluded.window_start_ms,
                window_end_ms = excluded.window_end_ms,
                checked_at_ms = excluded.checked_at_ms
            """, (
                ev.symbol,
                ev.quote_volume_24h,
                ev.best_growth_pct,
                ev.window_start_ms,
                ev.window_end_ms,
                now_ms
            ))
        conn.commit()
        print(f"[db] Saved {len(events)} events to {db_path}")
    finally:
        conn.close()


def clear_old_events(db_path: str) -> None:
    """Clear old events before new scan."""
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.cursor()
        cur.execute("DELETE FROM growth2h_events")
        conn.commit()
    finally:
        conn.close()


async def main_async(args) -> int:
    """Main async entry point."""
    print(f"[init] Growth 2H Scanner")
    print(f"       Lookback: {args.days} days")
    print(f"       Threshold: {args.threshold}%")
    print(f"       Volume: {args.min_qv:,.0f} - {args.max_qv:,.0f} USDT")
    print(f"       Concurrency: {args.concurrency}")
    print()

    # Ensure tables exist
    ensure_tables(args.db)

    # Clear old events if requested
    if args.clear:
        clear_old_events(args.db)
        print("[db] Cleared old events")

    # Create client and scanner
    client = MexcPublicClient(base_url=args.base_url, timeout_sec=args.timeout)
    scanner = Growth2HScanner(
        client,
        lookback_days=args.days,
        threshold_pct=args.threshold,
        max_quote_volume_24h=args.max_qv,
        min_quote_volume_24h=args.min_qv,
        concurrency=args.concurrency,
        request_delay_ms=args.delay_ms,
    )

    # Run scan
    start_time = time.time()
    events = await scanner.scan_all()
    elapsed = time.time() - start_time

    print()
    print(f"[done] Found {len(events)} symbols with >={args.threshold}% growth in 2h window")
    print(f"       Total time: {elapsed/60:.1f} minutes")
    print()

    # Show top results
    print(f"Top {args.top} results:")
    print("-" * 60)
    for ev in events[:args.top]:
        print(f"  {ev.symbol:14} growth={ev.best_growth_pct:6.2f}%  vol24h=${ev.quote_volume_24h:,.0f}")

    # Save to database
    save_events(args.db, events)

    return 0


def main():
    """CLI entry point."""
    p = argparse.ArgumentParser(
        description="Scan MEXC symbols for 2h growth windows"
    )
    p.add_argument(
        "--db",
        default="/home/user/scalperbot/scalperbot.db",
        help="SQLite database path"
    )
    p.add_argument(
        "--base-url",
        default="https://api.mexc.com",
        help="MEXC API base URL"
    )
    p.add_argument(
        "--days",
        type=int,
        default=10,
        help="Lookback period in days"
    )
    p.add_argument(
        "--threshold",
        type=float,
        default=20.0,
        help="Minimum growth %% within 2h window"
    )
    p.add_argument(
        "--max-qv",
        type=float,
        default=200_000.0,
        help="Max 24h quoteVolume (USDT)"
    )
    p.add_argument(
        "--min-qv",
        type=float,
        default=3_000.0,
        help="Min 24h quoteVolume (USDT)"
    )
    p.add_argument(
        "--concurrency",
        type=int,
        default=8,
        help="Number of concurrent symbol scans"
    )
    p.add_argument(
        "--delay-ms",
        type=int,
        default=80,
        help="Throttle delay between klines requests (ms)"
    )
    p.add_argument(
        "--timeout",
        type=int,
        default=20,
        help="HTTP request timeout (seconds)"
    )
    p.add_argument(
        "--top",
        type=int,
        default=25,
        help="Number of top results to display"
    )
    p.add_argument(
        "--clear",
        action="store_true",
        help="Clear old events before scanning"
    )

    args = p.parse_args()
    raise SystemExit(asyncio.run(main_async(args)))


if __name__ == "__main__":
    main()
