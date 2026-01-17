"""
Performance Evaluation Script

Calculates key trading metrics from the database:
- Win rate
- Average win / Average loss
- Expectancy
- Max drawdown
- Performance by growth2h bucket

Usage:
    python -m scalperbot.tools.evaluate_performance --db scalperbot.db
    python -m scalperbot.tools.evaluate_performance --db scalperbot.db --days 7
"""

from __future__ import annotations

import argparse
import sqlite3
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass


@dataclass
class PerformanceMetrics:
    """Performance metrics summary."""
    total_trades: int
    winning_trades: int
    losing_trades: int
    win_rate: float
    avg_win: float
    avg_loss: float
    expectancy: float
    profit_factor: float
    total_pnl: float
    max_drawdown: float
    max_drawdown_pct: float
    best_trade: float
    worst_trade: float
    avg_hold_time_min: float
    trades_per_day: float


def get_db_connection(db_path: str) -> sqlite3.Connection:
    """Get SQLite connection with row factory."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def calculate_metrics(
    conn: sqlite3.Connection,
    days: Optional[int] = None,
    symbol: Optional[str] = None
) -> Optional[PerformanceMetrics]:
    """Calculate performance metrics from positions table."""
    cur = conn.cursor()

    # Build WHERE clause
    conditions = ["status = 'CLOSED'"]
    params = []

    if days:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        conditions.append("exit_time >= ?")
        params.append(cutoff)

    if symbol:
        conditions.append("symbol = ?")
        params.append(symbol)

    where_clause = " AND ".join(conditions)

    # Get all closed positions
    query = f"""
    SELECT
        id,
        symbol,
        entry_price,
        exit_price,
        quantity,
        realized_pnl,
        entry_time,
        exit_time,
        exit_reason
    FROM positions
    WHERE {where_clause}
    ORDER BY exit_time ASC
    """

    cur.execute(query, params)
    rows = cur.fetchall()

    if not rows:
        return None

    # Calculate basic metrics
    total_trades = len(rows)
    pnls = [row['realized_pnl'] or 0.0 for row in rows]

    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]

    winning_trades = len(wins)
    losing_trades = len(losses)

    win_rate = winning_trades / total_trades if total_trades > 0 else 0
    avg_win = sum(wins) / len(wins) if wins else 0
    avg_loss = sum(losses) / len(losses) if losses else 0

    # Expectancy = (Win% * Avg Win) + (Loss% * Avg Loss)
    expectancy = (win_rate * avg_win) + ((1 - win_rate) * avg_loss)

    # Profit factor = Gross profit / Gross loss
    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else float('inf')

    total_pnl = sum(pnls)

    # Calculate max drawdown
    cumulative = 0
    peak = 0
    max_dd = 0
    for pnl in pnls:
        cumulative += pnl
        if cumulative > peak:
            peak = cumulative
        dd = peak - cumulative
        if dd > max_dd:
            max_dd = dd

    max_dd_pct = (max_dd / peak * 100) if peak > 0 else 0

    # Best and worst trades
    best_trade = max(pnls) if pnls else 0
    worst_trade = min(pnls) if pnls else 0

    # Average hold time
    hold_times = []
    for row in rows:
        if row['entry_time'] and row['exit_time']:
            try:
                entry = datetime.fromisoformat(row['entry_time'].replace('Z', '+00:00'))
                exit = datetime.fromisoformat(row['exit_time'].replace('Z', '+00:00'))
                hold_times.append((exit - entry).total_seconds() / 60)
            except Exception:
                pass

    avg_hold_time = sum(hold_times) / len(hold_times) if hold_times else 0

    # Trades per day
    if rows:
        try:
            first_exit = datetime.fromisoformat(rows[0]['exit_time'].replace('Z', '+00:00'))
            last_exit = datetime.fromisoformat(rows[-1]['exit_time'].replace('Z', '+00:00'))
            days_trading = max((last_exit - first_exit).days, 1)
            trades_per_day = total_trades / days_trading
        except Exception:
            trades_per_day = 0
    else:
        trades_per_day = 0

    return PerformanceMetrics(
        total_trades=total_trades,
        winning_trades=winning_trades,
        losing_trades=losing_trades,
        win_rate=win_rate,
        avg_win=avg_win,
        avg_loss=avg_loss,
        expectancy=expectancy,
        profit_factor=profit_factor,
        total_pnl=total_pnl,
        max_drawdown=max_dd,
        max_drawdown_pct=max_dd_pct,
        best_trade=best_trade,
        worst_trade=worst_trade,
        avg_hold_time_min=avg_hold_time,
        trades_per_day=trades_per_day
    )


def analyze_by_growth2h_bucket(conn: sqlite3.Connection) -> Dict[str, PerformanceMetrics]:
    """Analyze performance by growth2h characteristics."""
    cur = conn.cursor()

    # Join positions with growth2h_events
    query = """
    SELECT
        p.symbol,
        p.realized_pnl,
        p.entry_time,
        p.exit_time,
        g.best_growth_pct,
        g.spike_count,
        g.recent_spike_count,
        g.quote_volume_24h
    FROM positions p
    LEFT JOIN growth2h_events g ON p.symbol = g.symbol
    WHERE p.status = 'CLOSED'
    ORDER BY p.exit_time ASC
    """

    cur.execute(query)
    rows = cur.fetchall()

    if not rows:
        return {}

    # Bucket definitions
    buckets = {
        'fresh_repeater': [],     # Recent spike + multiple spikes
        'old_repeater': [],       # Multiple spikes but not recent
        'fresh_single': [],       # Recent spike, single occurrence
        'old_single': [],         # Old single spike
        'no_growth2h': [],        # Not in growth2h database
    }

    for row in rows:
        pnl = row['realized_pnl'] or 0.0
        spike_count = row['spike_count'] or 0
        recent_count = row['recent_spike_count'] or 0

        if spike_count == 0:
            buckets['no_growth2h'].append(pnl)
        elif recent_count > 0 and spike_count >= 2:
            buckets['fresh_repeater'].append(pnl)
        elif recent_count == 0 and spike_count >= 2:
            buckets['old_repeater'].append(pnl)
        elif recent_count > 0 and spike_count == 1:
            buckets['fresh_single'].append(pnl)
        else:
            buckets['old_single'].append(pnl)

    # Calculate metrics for each bucket
    results = {}
    for bucket_name, pnls in buckets.items():
        if not pnls:
            continue

        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p < 0]
        win_rate = len(wins) / len(pnls) if pnls else 0
        avg_win = sum(wins) / len(wins) if wins else 0
        avg_loss = sum(losses) / len(losses) if losses else 0
        expectancy = (win_rate * avg_win) + ((1 - win_rate) * avg_loss)

        results[bucket_name] = PerformanceMetrics(
            total_trades=len(pnls),
            winning_trades=len(wins),
            losing_trades=len(losses),
            win_rate=win_rate,
            avg_win=avg_win,
            avg_loss=avg_loss,
            expectancy=expectancy,
            profit_factor=sum(wins) / abs(sum(losses)) if losses else float('inf'),
            total_pnl=sum(pnls),
            max_drawdown=0,  # Not calculated per bucket
            max_drawdown_pct=0,
            best_trade=max(pnls) if pnls else 0,
            worst_trade=min(pnls) if pnls else 0,
            avg_hold_time_min=0,
            trades_per_day=0
        )

    return results


def analyze_by_volume_bucket(conn: sqlite3.Connection) -> Dict[str, PerformanceMetrics]:
    """Analyze performance by 24h volume bucket."""
    cur = conn.cursor()

    query = """
    SELECT
        p.symbol,
        p.realized_pnl,
        g.quote_volume_24h
    FROM positions p
    LEFT JOIN growth2h_events g ON p.symbol = g.symbol
    WHERE p.status = 'CLOSED'
    """

    cur.execute(query)
    rows = cur.fetchall()

    if not rows:
        return {}

    # Volume buckets
    buckets = {
        'micro_3k_20k': [],      # 3k-20k (very low volume)
        'small_20k_50k': [],     # 20k-50k (low volume)
        'medium_50k_100k': [],   # 50k-100k (sweet spot)
        'high_100k_200k': [],    # 100k-200k (higher volume)
        'unknown': [],           # No volume data
    }

    for row in rows:
        pnl = row['realized_pnl'] or 0.0
        vol = row['quote_volume_24h']

        if vol is None:
            buckets['unknown'].append(pnl)
        elif vol < 20000:
            buckets['micro_3k_20k'].append(pnl)
        elif vol < 50000:
            buckets['small_20k_50k'].append(pnl)
        elif vol < 100000:
            buckets['medium_50k_100k'].append(pnl)
        else:
            buckets['high_100k_200k'].append(pnl)

    # Calculate metrics
    results = {}
    for bucket_name, pnls in buckets.items():
        if not pnls:
            continue

        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p < 0]
        win_rate = len(wins) / len(pnls) if pnls else 0
        avg_win = sum(wins) / len(wins) if wins else 0
        avg_loss = sum(losses) / len(losses) if losses else 0
        expectancy = (win_rate * avg_win) + ((1 - win_rate) * avg_loss)

        results[bucket_name] = PerformanceMetrics(
            total_trades=len(pnls),
            winning_trades=len(wins),
            losing_trades=len(losses),
            win_rate=win_rate,
            avg_win=avg_win,
            avg_loss=avg_loss,
            expectancy=expectancy,
            profit_factor=sum(wins) / abs(sum(losses)) if losses else float('inf'),
            total_pnl=sum(pnls),
            max_drawdown=0,
            max_drawdown_pct=0,
            best_trade=max(pnls) if pnls else 0,
            worst_trade=min(pnls) if pnls else 0,
            avg_hold_time_min=0,
            trades_per_day=0
        )

    return results


def get_watchlist_stats(conn: sqlite3.Connection) -> Dict:
    """Get current watchlist statistics."""
    cur = conn.cursor()

    # Growth2H events stats
    cur.execute("""
    SELECT
        COUNT(*) as total,
        AVG(best_growth_pct) as avg_growth,
        AVG(spike_count) as avg_spikes,
        AVG(recent_spike_count) as avg_recent_spikes,
        SUM(CASE WHEN spike_count >= 2 THEN 1 ELSE 0 END) as repeaters,
        SUM(CASE WHEN recent_spike_count > 0 THEN 1 ELSE 0 END) as fresh
    FROM growth2h_events
    """)
    events_stats = dict(cur.fetchone() or {})

    # Scanner watchlist stats
    cur.execute("""
    SELECT
        COUNT(*) as watchlist_size,
        AVG(score) as avg_score,
        AVG(spike_count_10d) as avg_spike_count
    FROM scanner_watchlist
    WHERE source = 'growth2h'
    """)
    watchlist_stats = dict(cur.fetchone() or {})

    return {
        'growth2h_events': events_stats,
        'scanner_watchlist': watchlist_stats
    }


def print_metrics(title: str, metrics: PerformanceMetrics):
    """Print metrics in a formatted table."""
    print(f"\n{'='*60}")
    print(f" {title}")
    print(f"{'='*60}")
    print(f"  Total Trades:     {metrics.total_trades}")
    print(f"  Winning:          {metrics.winning_trades} ({metrics.win_rate*100:.1f}%)")
    print(f"  Losing:           {metrics.losing_trades} ({(1-metrics.win_rate)*100:.1f}%)")
    print(f"  {'-'*40}")
    print(f"  Avg Win:          ${metrics.avg_win:.2f}")
    print(f"  Avg Loss:         ${metrics.avg_loss:.2f}")
    print(f"  Expectancy:       ${metrics.expectancy:.2f}")
    print(f"  Profit Factor:    {metrics.profit_factor:.2f}")
    print(f"  {'-'*40}")
    print(f"  Total PnL:        ${metrics.total_pnl:.2f}")
    print(f"  Max Drawdown:     ${metrics.max_drawdown:.2f} ({metrics.max_drawdown_pct:.1f}%)")
    print(f"  Best Trade:       ${metrics.best_trade:.2f}")
    print(f"  Worst Trade:      ${metrics.worst_trade:.2f}")
    print(f"  {'-'*40}")
    print(f"  Avg Hold Time:    {metrics.avg_hold_time_min:.1f} min")
    print(f"  Trades/Day:       {metrics.trades_per_day:.1f}")


def print_bucket_comparison(title: str, buckets: Dict[str, PerformanceMetrics]):
    """Print bucket comparison table."""
    if not buckets:
        print(f"\n{title}: No data")
        return

    print(f"\n{'='*80}")
    print(f" {title}")
    print(f"{'='*80}")
    print(f"  {'Bucket':<20} {'Trades':>8} {'Win%':>8} {'Expect':>10} {'PnL':>10} {'PF':>8}")
    print(f"  {'-'*74}")

    for bucket_name, m in sorted(buckets.items(), key=lambda x: x[1].expectancy, reverse=True):
        pf_str = f"{m.profit_factor:.2f}" if m.profit_factor != float('inf') else "inf"
        print(f"  {bucket_name:<20} {m.total_trades:>8} {m.win_rate*100:>7.1f}% "
              f"${m.expectancy:>8.2f} ${m.total_pnl:>9.2f} {pf_str:>8}")


def main():
    """Main entry point."""
    p = argparse.ArgumentParser(
        description="Evaluate trading performance from database"
    )
    p.add_argument(
        "--db",
        default="/home/user/scalperbot/scalperbot.db",
        help="SQLite database path"
    )
    p.add_argument(
        "--days",
        type=int,
        help="Only analyze last N days"
    )
    p.add_argument(
        "--symbol",
        help="Filter to specific symbol"
    )
    p.add_argument(
        "--buckets",
        action="store_true",
        help="Show growth2h bucket analysis"
    )
    p.add_argument(
        "--volume-buckets",
        action="store_true",
        help="Show volume bucket analysis"
    )
    p.add_argument(
        "--watchlist",
        action="store_true",
        help="Show watchlist statistics"
    )
    p.add_argument(
        "--all",
        action="store_true",
        help="Show all analyses"
    )

    args = p.parse_args()

    conn = get_db_connection(args.db)

    # Overall metrics
    title = "Overall Performance"
    if args.days:
        title += f" (Last {args.days} days)"
    if args.symbol:
        title += f" ({args.symbol})"

    metrics = calculate_metrics(conn, args.days, args.symbol)

    if metrics:
        print_metrics(title, metrics)
    else:
        print(f"\nNo closed positions found")

    # Growth2H bucket analysis
    if args.buckets or args.all:
        buckets = analyze_by_growth2h_bucket(conn)
        print_bucket_comparison("Performance by Growth2H Bucket", buckets)

    # Volume bucket analysis
    if args.volume_buckets or args.all:
        vol_buckets = analyze_by_volume_bucket(conn)
        print_bucket_comparison("Performance by Volume Bucket", vol_buckets)

    # Watchlist stats
    if args.watchlist or args.all:
        stats = get_watchlist_stats(conn)

        print(f"\n{'='*60}")
        print(f" Watchlist Statistics")
        print(f"{'='*60}")

        e = stats.get('growth2h_events', {})
        if e.get('total'):
            print(f"  Growth2H Events:")
            print(f"    Total symbols:      {e.get('total', 0)}")
            print(f"    Avg growth:         {e.get('avg_growth', 0):.1f}%")
            print(f"    Avg spikes:         {e.get('avg_spikes', 0):.1f}")
            print(f"    Repeaters (>=2):    {e.get('repeaters', 0)}")
            print(f"    Fresh (recent):     {e.get('fresh', 0)}")

        w = stats.get('scanner_watchlist', {})
        if w.get('watchlist_size'):
            print(f"\n  Active Watchlist:")
            print(f"    Size:               {w.get('watchlist_size', 0)}")
            print(f"    Avg score:          {w.get('avg_score', 0):.1f}")
            print(f"    Avg spike count:    {w.get('avg_spike_count', 0):.1f}")

    print()
    conn.close()


if __name__ == "__main__":
    main()
