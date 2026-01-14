"""
CLI tool to run the Growth 2H Scanner.

Scans MEXC symbols for >=20% growth within any 2-hour window
over the last 10 days. Saves results to SQLite database.

Features:
- Recency filter: Only include spikes from last N days
- Frequency detection: Count spikes per symbol, require minimum
- Sanity filter: Cap maximum growth to filter manipulation
- Watchlist integration: Auto-update trading watchlist

Usage:
    python -m scalperbot.tools.scan_growth2h --db scalperbot.db --top 20
    python -m scalperbot.tools.scan_growth2h --threshold 15 --days 7
    python -m scalperbot.tools.scan_growth2h --update-watchlist
"""

from __future__ import annotations

import argparse
import asyncio
import sqlite3
import time
from datetime import datetime, timezone

from scalperbot.core.growth2h_scanner import Growth2HScanner, MexcPublicClient, SymbolAnalysis


def ensure_tables(db_path: str) -> None:
    """Create required tables if they don't exist."""
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.cursor()

        # Main events table (enhanced with frequency/recency data)
        cur.execute("""
        CREATE TABLE IF NOT EXISTS growth2h_events (
            symbol TEXT PRIMARY KEY,
            quote_volume_24h REAL NOT NULL,
            best_growth_pct REAL NOT NULL,
            window_start_ms INTEGER NOT NULL,
            window_end_ms INTEGER NOT NULL,
            spike_count INTEGER NOT NULL DEFAULT 1,
            recent_spike_count INTEGER NOT NULL DEFAULT 0,
            last_spike_ms INTEGER NOT NULL,
            score REAL NOT NULL DEFAULT 0,
            checked_at_ms INTEGER NOT NULL
        )
        """)

        # Add new columns if they don't exist (for upgrades)
        for col, coldef in [
            ("spike_count", "INTEGER NOT NULL DEFAULT 1"),
            ("recent_spike_count", "INTEGER NOT NULL DEFAULT 0"),
            ("last_spike_ms", "INTEGER NOT NULL DEFAULT 0"),
            ("score", "REAL NOT NULL DEFAULT 0"),
        ]:
            try:
                cur.execute(f"ALTER TABLE growth2h_events ADD COLUMN {col} {coldef}")
            except sqlite3.OperationalError:
                pass  # Column already exists

        # Scanner watchlist table - check if schema matches, recreate if not
        # This table is refreshed daily so safe to recreate
        cur.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='scanner_watchlist'")
        row = cur.fetchone()
        if row and 'best_growth_pct' not in row[0]:
            # Old schema, drop and recreate
            cur.execute("DROP TABLE scanner_watchlist")

        cur.execute("""
        CREATE TABLE IF NOT EXISTS scanner_watchlist (
            symbol TEXT PRIMARY KEY,
            source TEXT NOT NULL DEFAULT 'growth2h',
            best_growth_pct REAL NOT NULL,
            spike_count_10d INTEGER NOT NULL,
            recent_spike_count INTEGER NOT NULL,
            last_spike_ms INTEGER NOT NULL,
            quote_volume_24h REAL NOT NULL,
            score REAL NOT NULL,
            added_at_ms INTEGER NOT NULL,
            updated_at_ms INTEGER NOT NULL
        )
        """)

        # Add missing columns to scanner_watchlist (for upgrades)
        for col, coldef in [
            ("source", "TEXT NOT NULL DEFAULT 'growth2h'"),
            ("spike_count_10d", "INTEGER NOT NULL DEFAULT 0"),
            ("recent_spike_count", "INTEGER NOT NULL DEFAULT 0"),
            ("last_spike_ms", "INTEGER NOT NULL DEFAULT 0"),
            ("quote_volume_24h", "REAL NOT NULL DEFAULT 0"),
            ("score", "REAL NOT NULL DEFAULT 0"),
            ("added_at_ms", "INTEGER NOT NULL DEFAULT 0"),
            ("updated_at_ms", "INTEGER NOT NULL DEFAULT 0"),
        ]:
            try:
                cur.execute(f"ALTER TABLE scanner_watchlist ADD COLUMN {col} {coldef}")
            except sqlite3.OperationalError:
                pass  # Column already exists

        # Index for faster queries
        cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_growth2h_events_score
        ON growth2h_events(score DESC)
        """)

        cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_scanner_watchlist_score
        ON scanner_watchlist(score DESC)
        """)

        conn.commit()
    finally:
        conn.close()


def save_events(db_path: str, results: list[SymbolAnalysis]) -> None:
    """Save growth events to database."""
    now_ms = int(time.time() * 1000)
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.cursor()
        for r in results:
            cur.execute("""
            INSERT INTO growth2h_events(
                symbol, quote_volume_24h, best_growth_pct,
                window_start_ms, window_end_ms, spike_count,
                recent_spike_count, last_spike_ms, score, checked_at_ms
            )
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(symbol) DO UPDATE SET
                quote_volume_24h = excluded.quote_volume_24h,
                best_growth_pct = excluded.best_growth_pct,
                window_start_ms = excluded.window_start_ms,
                window_end_ms = excluded.window_end_ms,
                spike_count = excluded.spike_count,
                recent_spike_count = excluded.recent_spike_count,
                last_spike_ms = excluded.last_spike_ms,
                score = excluded.score,
                checked_at_ms = excluded.checked_at_ms
            """, (
                r.symbol,
                r.quote_volume_24h,
                r.best_growth_pct,
                r.best_window_start_ms,
                r.best_window_end_ms,
                r.spike_count,
                r.recent_spike_count,
                r.last_spike_ms,
                r.score,
                now_ms
            ))
        conn.commit()
        print(f"[db] Saved {len(results)} events to {db_path}")
    finally:
        conn.close()


def update_watchlist(db_path: str, qualified: list[SymbolAnalysis], top_n: int = 10) -> int:
    """
    Update scanner_watchlist with top qualified symbols.
    Returns number of symbols added/updated.
    """
    now_ms = int(time.time() * 1000)
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.cursor()

        # Clear old growth2h entries
        cur.execute("DELETE FROM scanner_watchlist WHERE source = 'growth2h'")

        # Insert top N qualified symbols
        count = 0
        for r in qualified[:top_n]:
            cur.execute("""
            INSERT INTO scanner_watchlist(
                symbol, source, best_growth_pct, spike_count_10d,
                recent_spike_count, last_spike_ms, quote_volume_24h,
                score, added_at_ms, updated_at_ms
            )
            VALUES(?, 'growth2h', ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(symbol) DO UPDATE SET
                source = 'growth2h',
                best_growth_pct = excluded.best_growth_pct,
                spike_count_10d = excluded.spike_count_10d,
                recent_spike_count = excluded.recent_spike_count,
                last_spike_ms = excluded.last_spike_ms,
                quote_volume_24h = excluded.quote_volume_24h,
                score = excluded.score,
                updated_at_ms = excluded.updated_at_ms
            """, (
                r.symbol,
                r.best_growth_pct,
                r.spike_count,
                r.recent_spike_count,
                r.last_spike_ms,
                r.quote_volume_24h,
                r.score,
                now_ms,
                now_ms
            ))
            count += 1

        conn.commit()
        return count
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


def format_timestamp(ms: int) -> str:
    """Format millisecond timestamp as human-readable date."""
    if ms <= 0:
        return "N/A"
    dt = datetime.fromtimestamp(ms / 1000, tz=timezone.utc)
    return dt.strftime("%Y-%m-%d %H:%M")


async def main_async(args) -> int:
    """Main async entry point."""
    print(f"[init] Growth 2H Scanner")
    print(f"       Lookback: {args.days} days")
    print(f"       Threshold: {args.threshold}%")
    print(f"       Max growth cap: {args.max_growth}%")
    print(f"       Volume: {args.min_qv:,.0f} - {args.max_qv:,.0f} USDT")
    print(f"       Recency filter: {args.recency_days} days")
    print(f"       Min spikes required: {args.min_spikes}")
    print(f"       Concurrency: {args.concurrency}")
    if args.update_watchlist:
        print(f"       Watchlist update: TOP {args.watchlist_size}")
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
        max_growth_pct=args.max_growth,
        max_quote_volume_24h=args.max_qv,
        min_quote_volume_24h=args.min_qv,
        recency_days=args.recency_days,
        min_spikes=args.min_spikes,
        concurrency=args.concurrency,
        request_delay_ms=args.delay_ms,
    )

    # Run scan
    start_time = time.time()
    results = await scanner.scan_all()
    elapsed = time.time() - start_time

    print()
    print(f"[scan] Found {len(results)} symbols with >={args.threshold}% growth in 2h window")
    print(f"       Total time: {elapsed/60:.1f} minutes")

    # Apply watchlist filters
    qualified = scanner.filter_for_watchlist(results)
    print(f"[filter] {len(qualified)} symbols pass watchlist filters")
    print(f"         (max_growth<={args.max_growth}%, spikes>={args.min_spikes}, recent spike in {args.recency_days}d)")
    print()

    # Show top results (all results)
    print(f"Top {args.top} results (by score):")
    print("-" * 90)
    print(f"  {'Symbol':14} {'Growth':>8} {'Spikes':>7} {'Recent':>7} {'Vol24h':>12} {'Score':>8} {'Last Spike':>16}")
    print("-" * 90)
    for r in results[:args.top]:
        marker = "*" if r in qualified else " "
        print(f"{marker} {r.symbol:14} {r.best_growth_pct:7.1f}% {r.spike_count:>7} "
              f"{r.recent_spike_count:>7} ${r.quote_volume_24h:>10,.0f} {r.score:>8.1f} "
              f"{format_timestamp(r.last_spike_ms):>16}")
    print("-" * 90)
    print("  * = Qualifies for watchlist")
    print()

    # Show qualified symbols
    if qualified:
        print(f"Qualified for watchlist ({len(qualified)} symbols):")
        print("-" * 90)
        for r in qualified[:args.watchlist_size]:
            print(f"  {r.symbol:14} growth={r.best_growth_pct:5.1f}%  spikes={r.spike_count}  "
                  f"recent={r.recent_spike_count}  score={r.score:.1f}")
        print()

    # Save all events to database
    save_events(args.db, results)

    # Update watchlist if requested
    if args.update_watchlist:
        count = update_watchlist(args.db, qualified, args.watchlist_size)
        print(f"[watchlist] Updated scanner_watchlist with {count} symbols")

    return 0


def main():
    """CLI entry point."""
    p = argparse.ArgumentParser(
        description="Scan MEXC symbols for 2h growth windows with frequency detection"
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
        "--max-growth",
        type=float,
        default=300.0,
        help="Maximum growth %% (sanity filter for manipulation)"
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
        "--recency-days",
        type=int,
        default=3,
        help="Only count spikes from last N days as 'recent'"
    )
    p.add_argument(
        "--min-spikes",
        type=int,
        default=2,
        help="Minimum number of spikes required for watchlist"
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
    p.add_argument(
        "--update-watchlist",
        action="store_true",
        help="Update scanner_watchlist table with qualified symbols"
    )
    p.add_argument(
        "--watchlist-size",
        type=int,
        default=10,
        help="Number of top symbols to add to watchlist"
    )

    args = p.parse_args()
    raise SystemExit(asyncio.run(main_async(args)))


if __name__ == "__main__":
    main()
