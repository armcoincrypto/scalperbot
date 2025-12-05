#!/usr/bin/env python3
"""
Testing Dashboard for ScalperBot
Tracks performance metrics during DRY_RUN testing phase

Expert's acceptance criteria:
- Minimum 300 signals before going live
- Win Rate ≥ 40%
- Profit Factor ≥ 1.15

Usage:
    python tools/dashboard.py                    # Full dashboard
    python tools/dashboard.py --live             # Live updating (refresh every 60s)
    python tools/dashboard.py --export report    # Export to JSON
"""
import argparse
import json
import sqlite3
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Any, Optional

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))


class TestingDashboard:
    """Dashboard for tracking DRY_RUN testing performance"""

    # Expert's acceptance criteria
    MIN_SIGNALS = 300
    MIN_WIN_RATE = 40.0
    MIN_PROFIT_FACTOR = 1.15

    def __init__(self, db_path: str = "scalperbot/trades.db"):
        self.db_path = db_path
        self.conn = None

    def connect(self):
        """Connect to database"""
        if not Path(self.db_path).exists():
            print(f"Error: Database not found at {self.db_path}")
            sys.exit(1)
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row

    def close(self):
        """Close database connection"""
        if self.conn:
            self.conn.close()

    def get_all_trades(self) -> List[Dict]:
        """Get all trades from database"""
        cursor = self.conn.execute("""
            SELECT * FROM trades
            ORDER BY timestamp DESC
        """)
        return [dict(row) for row in cursor.fetchall()]

    def get_closed_positions(self) -> List[Dict]:
        """Get all closed positions with PnL data"""
        cursor = self.conn.execute("""
            SELECT p.*, t.signal_reason, t.timestamp as trade_timestamp
            FROM positions p
            LEFT JOIN trades t ON p.trade_id = t.id
            WHERE p.status = 'CLOSED'
            ORDER BY p.close_time DESC
        """)
        return [dict(row) for row in cursor.fetchall()]

    def get_open_positions(self) -> List[Dict]:
        """Get current open positions"""
        cursor = self.conn.execute("""
            SELECT p.*, t.signal_reason
            FROM positions p
            LEFT JOIN trades t ON p.trade_id = t.id
            WHERE p.status = 'OPEN'
        """)
        return [dict(row) for row in cursor.fetchall()]

    def calculate_metrics(self) -> Dict[str, Any]:
        """Calculate comprehensive performance metrics"""
        trades = self.get_all_trades()
        closed_positions = self.get_closed_positions()
        open_positions = self.get_open_positions()

        # Basic counts
        total_trades = len(trades)
        total_closed = len(closed_positions)
        total_open = len(open_positions)

        # Status breakdown
        status_counts = {}
        for trade in trades:
            status = trade.get('status', 'UNKNOWN')
            status_counts[status] = status_counts.get(status, 0) + 1

        # Calculate win/loss from closed positions
        wins = []
        losses = []
        for pos in closed_positions:
            pnl = pos.get('realized_pnl', 0) or 0
            if pnl > 0:
                wins.append(pnl)
            elif pnl < 0:
                losses.append(abs(pnl))

        win_count = len(wins)
        loss_count = len(losses)
        total_wins = sum(wins)
        total_losses = sum(losses)

        # Win rate
        if total_closed > 0:
            win_rate = (win_count / total_closed) * 100
        else:
            win_rate = 0.0

        # Profit factor
        if total_losses > 0:
            profit_factor = total_wins / total_losses
        elif total_wins > 0:
            profit_factor = float('inf')
        else:
            profit_factor = 0.0

        # Net PnL
        net_pnl = total_wins - total_losses

        # Average win/loss
        avg_win = total_wins / win_count if win_count > 0 else 0
        avg_loss = total_losses / loss_count if loss_count > 0 else 0

        # Risk/reward ratio
        if avg_loss > 0:
            risk_reward = avg_win / avg_loss
        else:
            risk_reward = 0.0

        # Per-symbol breakdown
        symbol_stats = self._calculate_symbol_stats(closed_positions)

        # Hourly distribution
        hourly_stats = self._calculate_hourly_stats(closed_positions)

        # Progress towards 300 signals
        signal_count = status_counts.get('FILLED', 0) + status_counts.get('DRY_RUN', 0)
        progress_pct = min(100, (signal_count / self.MIN_SIGNALS) * 100)

        # Ready for live?
        ready_for_live = (
            signal_count >= self.MIN_SIGNALS and
            win_rate >= self.MIN_WIN_RATE and
            profit_factor >= self.MIN_PROFIT_FACTOR
        )

        return {
            'timestamp': datetime.now().isoformat(),
            'summary': {
                'total_trades': total_trades,
                'total_closed': total_closed,
                'total_open': total_open,
                'signal_count': signal_count,
                'progress_pct': progress_pct,
                'status_breakdown': status_counts
            },
            'performance': {
                'win_count': win_count,
                'loss_count': loss_count,
                'win_rate': win_rate,
                'profit_factor': profit_factor,
                'net_pnl': net_pnl,
                'total_wins': total_wins,
                'total_losses': total_losses,
                'avg_win': avg_win,
                'avg_loss': avg_loss,
                'risk_reward': risk_reward
            },
            'acceptance_criteria': {
                'min_signals': self.MIN_SIGNALS,
                'signal_count': signal_count,
                'signals_remaining': max(0, self.MIN_SIGNALS - signal_count),
                'min_win_rate': self.MIN_WIN_RATE,
                'current_win_rate': win_rate,
                'win_rate_ok': win_rate >= self.MIN_WIN_RATE,
                'min_profit_factor': self.MIN_PROFIT_FACTOR,
                'current_profit_factor': profit_factor,
                'profit_factor_ok': profit_factor >= self.MIN_PROFIT_FACTOR,
                'ready_for_live': ready_for_live
            },
            'per_symbol': symbol_stats,
            'hourly_distribution': hourly_stats,
            'open_positions': [{
                'symbol': p['symbol'],
                'entry_price': p['entry_price'],
                'quantity': p['quantity'],
                'unrealized_pnl': p.get('unrealized_pnl', 0)
            } for p in open_positions]
        }

    def _calculate_symbol_stats(self, closed_positions: List[Dict]) -> Dict:
        """Calculate per-symbol statistics"""
        symbol_data = {}

        for pos in closed_positions:
            symbol = pos['symbol']
            pnl = pos.get('realized_pnl', 0) or 0

            if symbol not in symbol_data:
                symbol_data[symbol] = {
                    'trades': 0,
                    'wins': 0,
                    'losses': 0,
                    'total_pnl': 0,
                    'win_pnl': 0,
                    'loss_pnl': 0
                }

            symbol_data[symbol]['trades'] += 1
            symbol_data[symbol]['total_pnl'] += pnl

            if pnl > 0:
                symbol_data[symbol]['wins'] += 1
                symbol_data[symbol]['win_pnl'] += pnl
            elif pnl < 0:
                symbol_data[symbol]['losses'] += 1
                symbol_data[symbol]['loss_pnl'] += abs(pnl)

        # Calculate derived metrics
        for symbol, data in symbol_data.items():
            trades = data['trades']
            wins = data['wins']

            data['win_rate'] = (wins / trades * 100) if trades > 0 else 0

            if data['loss_pnl'] > 0:
                data['profit_factor'] = data['win_pnl'] / data['loss_pnl']
            elif data['win_pnl'] > 0:
                data['profit_factor'] = float('inf')
            else:
                data['profit_factor'] = 0

        return symbol_data

    def _calculate_hourly_stats(self, closed_positions: List[Dict]) -> Dict:
        """Calculate performance by hour of day (UTC)"""
        hourly = {str(h).zfill(2): {'trades': 0, 'wins': 0, 'pnl': 0} for h in range(24)}

        for pos in closed_positions:
            try:
                close_time = pos.get('close_time', '')
                if close_time:
                    dt = datetime.fromisoformat(close_time.replace('Z', '+00:00'))
                    hour = str(dt.hour).zfill(2)
                    pnl = pos.get('realized_pnl', 0) or 0

                    hourly[hour]['trades'] += 1
                    hourly[hour]['pnl'] += pnl
                    if pnl > 0:
                        hourly[hour]['wins'] += 1
            except Exception:
                continue

        # Calculate win rates
        for hour, data in hourly.items():
            if data['trades'] > 0:
                data['win_rate'] = (data['wins'] / data['trades']) * 100
            else:
                data['win_rate'] = 0

        return hourly

    def print_dashboard(self, metrics: Dict):
        """Print formatted dashboard to console"""
        print("\n" + "="*80)
        print("📊 SCALPERBOT TESTING DASHBOARD")
        print(f"   Generated: {metrics['timestamp']}")
        print("="*80)

        # Progress bar
        summary = metrics['summary']
        acceptance = metrics['acceptance_criteria']
        progress = acceptance['signal_count']
        target = acceptance['min_signals']
        progress_pct = acceptance['signal_count'] / target * 100

        bar_width = 40
        filled = int(bar_width * min(1, progress_pct / 100))
        bar = "█" * filled + "░" * (bar_width - filled)

        print(f"\n📈 PROGRESS TO {target} SIGNALS:")
        print(f"   [{bar}] {progress}/{target} ({progress_pct:.1f}%)")

        if progress < target:
            print(f"   ⏳ {target - progress} signals remaining")
        else:
            print(f"   ✅ Target reached!")

        # Acceptance criteria
        print("\n" + "-"*80)
        print("🎯 ACCEPTANCE CRITERIA:")
        print("-"*80)

        perf = metrics['performance']

        # Win rate check
        wr_status = "✅" if acceptance['win_rate_ok'] else "❌"
        print(f"   {wr_status} Win Rate:      {perf['win_rate']:.1f}% (target: ≥{acceptance['min_win_rate']}%)")

        # Profit factor check
        pf_status = "✅" if acceptance['profit_factor_ok'] else "❌"
        pf_display = f"{perf['profit_factor']:.2f}" if perf['profit_factor'] != float('inf') else "∞"
        print(f"   {pf_status} Profit Factor: {pf_display} (target: ≥{acceptance['min_profit_factor']})")

        # Signals check
        sig_status = "✅" if progress >= target else "❌"
        print(f"   {sig_status} Signal Count:  {progress} (target: ≥{target})")

        # Ready for live?
        print("\n" + "-"*80)
        if acceptance['ready_for_live']:
            print("🚀 STATUS: READY FOR LIVE TRADING!")
        else:
            print("⏸️  STATUS: Continue DRY_RUN testing")
        print("-"*80)

        # Performance summary
        print("\n📊 PERFORMANCE SUMMARY:")
        print("-"*80)
        print(f"   Total Trades:    {summary['total_trades']}")
        print(f"   Closed:          {summary['total_closed']}")
        print(f"   Open:            {summary['total_open']}")
        print(f"   Wins:            {perf['win_count']}")
        print(f"   Losses:          {perf['loss_count']}")
        print(f"   Net PnL:         ${perf['net_pnl']:.2f}")
        print(f"   Total Wins:      ${perf['total_wins']:.2f}")
        print(f"   Total Losses:    ${perf['total_losses']:.2f}")
        print(f"   Avg Win:         ${perf['avg_win']:.2f}")
        print(f"   Avg Loss:        ${perf['avg_loss']:.2f}")
        print(f"   Risk/Reward:     {perf['risk_reward']:.2f}")

        # Trade status breakdown
        print("\n📋 TRADE STATUS BREAKDOWN:")
        print("-"*80)
        for status, count in sorted(summary['status_breakdown'].items()):
            print(f"   {status}: {count}")

        # Per-symbol stats
        if metrics['per_symbol']:
            print("\n📈 PER-SYMBOL PERFORMANCE:")
            print("-"*80)
            print(f"   {'Symbol':<12} {'Trades':<8} {'Wins':<6} {'Losses':<8} {'Win Rate':<10} {'PnL':<12} {'PF':<8}")
            print(f"   {'-'*12} {'-'*8} {'-'*6} {'-'*8} {'-'*10} {'-'*12} {'-'*8}")

            for symbol, stats in sorted(metrics['per_symbol'].items()):
                pf = f"{stats['profit_factor']:.2f}" if stats['profit_factor'] != float('inf') else "∞"
                print(f"   {symbol:<12} {stats['trades']:<8} {stats['wins']:<6} {stats['losses']:<8} "
                      f"{stats['win_rate']:>6.1f}%    ${stats['total_pnl']:>8.2f}    {pf:<8}")

        # Open positions
        if metrics['open_positions']:
            print("\n📍 OPEN POSITIONS:")
            print("-"*80)
            for pos in metrics['open_positions']:
                print(f"   {pos['symbol']}: {pos['quantity']:.6f} @ {pos['entry_price']:.4f}")

        # Best/worst hours (if enough data)
        hourly = metrics['hourly_distribution']
        active_hours = {h: d for h, d in hourly.items() if d['trades'] >= 3}

        if active_hours:
            print("\n⏰ PERFORMANCE BY HOUR (UTC) [min 3 trades]:")
            print("-"*80)

            # Sort by win rate
            sorted_hours = sorted(active_hours.items(), key=lambda x: x[1]['win_rate'], reverse=True)

            best = sorted_hours[:3]
            worst = sorted_hours[-3:]

            print("   Best hours:")
            for hour, data in best:
                print(f"      {hour}:00 - Win Rate: {data['win_rate']:.1f}% ({data['trades']} trades)")

            print("   Worst hours:")
            for hour, data in worst:
                print(f"      {hour}:00 - Win Rate: {data['win_rate']:.1f}% ({data['trades']} trades)")

        print("\n" + "="*80)

    def export_json(self, metrics: Dict, filename: str):
        """Export metrics to JSON file"""
        # Handle infinity values
        def clean_for_json(obj):
            if isinstance(obj, dict):
                return {k: clean_for_json(v) for k, v in obj.items()}
            elif isinstance(obj, list):
                return [clean_for_json(v) for v in obj]
            elif obj == float('inf'):
                return "Infinity"
            elif obj == float('-inf'):
                return "-Infinity"
            else:
                return obj

        clean_metrics = clean_for_json(metrics)

        with open(filename, 'w') as f:
            json.dump(clean_metrics, f, indent=2)

        print(f"Exported to {filename}")

    def run_live(self, refresh_interval: int = 60):
        """Run dashboard in live mode with periodic refresh"""
        print("Starting live dashboard (Ctrl+C to exit)...")
        print(f"Refreshing every {refresh_interval} seconds")

        try:
            while True:
                # Clear screen
                print("\033[2J\033[H", end="")

                metrics = self.calculate_metrics()
                self.print_dashboard(metrics)

                print(f"\n⏳ Next refresh in {refresh_interval} seconds... (Ctrl+C to exit)")
                time.sleep(refresh_interval)

        except KeyboardInterrupt:
            print("\nExiting live dashboard...")


def main():
    parser = argparse.ArgumentParser(description="ScalperBot Testing Dashboard")
    parser.add_argument('--db', default='scalperbot/trades.db', help='Path to trades database')
    parser.add_argument('--live', action='store_true', help='Run in live mode with auto-refresh')
    parser.add_argument('--refresh', type=int, default=60, help='Refresh interval in seconds (for live mode)')
    parser.add_argument('--export', type=str, help='Export metrics to JSON file')
    parser.add_argument('--json', action='store_true', help='Output as JSON instead of formatted dashboard')

    args = parser.parse_args()

    dashboard = TestingDashboard(args.db)
    dashboard.connect()

    try:
        if args.live:
            dashboard.run_live(args.refresh)
        else:
            metrics = dashboard.calculate_metrics()

            if args.export:
                dashboard.export_json(metrics, args.export)

            if args.json:
                # Output as JSON
                def clean_inf(obj):
                    if isinstance(obj, dict):
                        return {k: clean_inf(v) for k, v in obj.items()}
                    elif isinstance(obj, list):
                        return [clean_inf(v) for v in obj]
                    elif obj == float('inf'):
                        return "Infinity"
                    return obj
                print(json.dumps(clean_inf(metrics), indent=2))
            else:
                dashboard.print_dashboard(metrics)

    finally:
        dashboard.close()


if __name__ == "__main__":
    main()
