#!/usr/bin/env python3
"""
Trading Data Analyzer for ScalperBot
Analyzes trade history and provides performance metrics and recommendations
"""
import sqlite3
import argparse
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Any, List, Optional
import sys

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))


class TradingAnalyzer:
    """Analyze trading performance from ScalperBot database"""

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

    def get_all_trades(self, days: Optional[int] = None) -> List[Dict]:
        """Get all trades, optionally filtered by recent days"""
        query = "SELECT * FROM trades ORDER BY created_at DESC"
        if days:
            cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat()
            query = f"SELECT * FROM trades WHERE created_at >= '{cutoff}' ORDER BY created_at DESC"

        cursor = self.conn.execute(query)
        return [dict(row) for row in cursor.fetchall()]

    def get_all_positions(self, days: Optional[int] = None) -> List[Dict]:
        """Get all positions, optionally filtered by recent days"""
        query = "SELECT * FROM positions ORDER BY opened_at DESC"
        if days:
            cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat()
            query = f"SELECT * FROM positions WHERE opened_at >= '{cutoff}' ORDER BY opened_at DESC"

        cursor = self.conn.execute(query)
        return [dict(row) for row in cursor.fetchall()]

    def get_closed_positions(self, days: Optional[int] = None) -> List[Dict]:
        """Get closed positions with PnL data"""
        query = """
            SELECT * FROM positions
            WHERE status = 'CLOSED'
            ORDER BY closed_at DESC
        """
        if days:
            cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat()
            query = f"""
                SELECT * FROM positions
                WHERE status = 'CLOSED' AND closed_at >= '{cutoff}'
                ORDER BY closed_at DESC
            """

        cursor = self.conn.execute(query)
        return [dict(row) for row in cursor.fetchall()]

    def calculate_overall_metrics(self, days: Optional[int] = None) -> Dict[str, Any]:
        """Calculate overall trading metrics"""
        positions = self.get_closed_positions(days)
        trades = self.get_all_trades(days)

        if not positions:
            return {
                'total_trades': len(trades),
                'closed_positions': 0,
                'open_positions': 0,
                'total_pnl': 0,
                'win_rate': 0,
                'wins': 0,
                'losses': 0,
                'avg_win': 0,
                'avg_loss': 0,
                'profit_factor': 0,
                'largest_win': 0,
                'largest_loss': 0,
                'avg_hold_time_hours': 0,
                'status_breakdown': self._get_status_breakdown(trades)
            }

        # Count open positions
        open_count = self.conn.execute(
            "SELECT COUNT(*) FROM positions WHERE status = 'OPEN'"
        ).fetchone()[0]

        # Calculate wins and losses
        wins = [p for p in positions if p['pnl'] > 0]
        losses = [p for p in positions if p['pnl'] <= 0]

        total_pnl = sum(p['pnl'] for p in positions)
        total_wins = sum(p['pnl'] for p in wins) if wins else 0
        total_losses = abs(sum(p['pnl'] for p in losses)) if losses else 0

        # Calculate hold times
        hold_times = []
        for p in positions:
            if p['opened_at'] and p['closed_at']:
                try:
                    opened = datetime.fromisoformat(p['opened_at'])
                    closed = datetime.fromisoformat(p['closed_at'])
                    hold_hours = (closed - opened).total_seconds() / 3600
                    hold_times.append(hold_hours)
                except ValueError:
                    pass

        return {
            'total_trades': len(trades),
            'closed_positions': len(positions),
            'open_positions': open_count,
            'total_pnl': total_pnl,
            'win_rate': (len(wins) / len(positions) * 100) if positions else 0,
            'wins': len(wins),
            'losses': len(losses),
            'avg_win': (total_wins / len(wins)) if wins else 0,
            'avg_loss': (total_losses / len(losses)) if losses else 0,
            'profit_factor': (total_wins / total_losses) if total_losses > 0 else float('inf'),
            'largest_win': max((p['pnl'] for p in positions), default=0),
            'largest_loss': min((p['pnl'] for p in positions), default=0),
            'avg_hold_time_hours': sum(hold_times) / len(hold_times) if hold_times else 0,
            'status_breakdown': self._get_status_breakdown(trades)
        }

    def _get_status_breakdown(self, trades: List[Dict]) -> Dict[str, int]:
        """Get breakdown of trade statuses"""
        breakdown = {}
        for trade in trades:
            status = trade.get('status', 'UNKNOWN')
            breakdown[status] = breakdown.get(status, 0) + 1
        return breakdown

    def analyze_by_symbol(self, days: Optional[int] = None) -> Dict[str, Dict]:
        """Analyze performance by trading symbol"""
        positions = self.get_closed_positions(days)

        symbol_stats = {}
        for pos in positions:
            symbol = pos['symbol']
            if symbol not in symbol_stats:
                symbol_stats[symbol] = {
                    'trades': 0,
                    'wins': 0,
                    'losses': 0,
                    'total_pnl': 0,
                    'pnl_list': []
                }

            symbol_stats[symbol]['trades'] += 1
            symbol_stats[symbol]['total_pnl'] += pos['pnl']
            symbol_stats[symbol]['pnl_list'].append(pos['pnl'])

            if pos['pnl'] > 0:
                symbol_stats[symbol]['wins'] += 1
            else:
                symbol_stats[symbol]['losses'] += 1

        # Calculate win rates and averages
        for symbol, stats in symbol_stats.items():
            stats['win_rate'] = (stats['wins'] / stats['trades'] * 100) if stats['trades'] > 0 else 0
            stats['avg_pnl'] = stats['total_pnl'] / stats['trades'] if stats['trades'] > 0 else 0
            del stats['pnl_list']  # Clean up temporary data

        return symbol_stats

    def analyze_by_exit_reason(self, days: Optional[int] = None) -> Dict[str, Dict]:
        """Analyze performance by exit reason"""
        positions = self.get_closed_positions(days)

        reason_stats = {}
        for pos in positions:
            reason = pos.get('exit_reason', 'UNKNOWN')
            if reason not in reason_stats:
                reason_stats[reason] = {
                    'count': 0,
                    'total_pnl': 0,
                    'wins': 0,
                    'losses': 0
                }

            reason_stats[reason]['count'] += 1
            reason_stats[reason]['total_pnl'] += pos['pnl']

            if pos['pnl'] > 0:
                reason_stats[reason]['wins'] += 1
            else:
                reason_stats[reason]['losses'] += 1

        # Calculate percentages
        for reason, stats in reason_stats.items():
            stats['win_rate'] = (stats['wins'] / stats['count'] * 100) if stats['count'] > 0 else 0
            stats['avg_pnl'] = stats['total_pnl'] / stats['count'] if stats['count'] > 0 else 0

        return reason_stats

    def analyze_by_hour(self, days: Optional[int] = None) -> Dict[int, Dict]:
        """Analyze performance by hour of day (UTC)"""
        positions = self.get_closed_positions(days)

        hour_stats = {h: {'count': 0, 'wins': 0, 'losses': 0, 'total_pnl': 0} for h in range(24)}

        for pos in positions:
            try:
                opened = datetime.fromisoformat(pos['opened_at'])
                hour = opened.hour
                hour_stats[hour]['count'] += 1
                hour_stats[hour]['total_pnl'] += pos['pnl']
                if pos['pnl'] > 0:
                    hour_stats[hour]['wins'] += 1
                else:
                    hour_stats[hour]['losses'] += 1
            except (ValueError, TypeError):
                pass

        # Calculate win rates
        for hour, stats in hour_stats.items():
            stats['win_rate'] = (stats['wins'] / stats['count'] * 100) if stats['count'] > 0 else 0

        return hour_stats

    def find_issues(self, trades: List[Dict], positions: List[Dict]) -> List[str]:
        """Identify potential issues with trading data"""
        issues = []

        # Check for trades without positions
        trade_ids_with_positions = set(p['trade_id'] for p in positions)
        filled_trades = [t for t in trades if t['status'] == 'FILLED']
        trades_without_positions = [t for t in filled_trades if t['id'] not in trade_ids_with_positions]

        if trades_without_positions:
            issues.append(f"CRITICAL: {len(trades_without_positions)} FILLED trades have no corresponding position record")
            issues.append("  -> This means positions were not tracked and exits were never executed")
            issues.append("  -> Trade IDs: " + ", ".join(str(t['id']) for t in trades_without_positions[:10]))

        # Check for positions with 0 PnL
        zero_pnl_closed = [p for p in positions if p['status'] == 'CLOSED' and p['pnl'] == 0]
        if zero_pnl_closed:
            issues.append(f"WARNING: {len(zero_pnl_closed)} closed positions have PnL = 0 (possible data issue)")

        # Check for failed trades
        failed_trades = [t for t in trades if t['status'] == 'FAILED']
        if failed_trades:
            issues.append(f"WARNING: {len(failed_trades)} trades failed to execute")
            # Check reasons
            for trade in failed_trades[:5]:
                issues.append(f"  -> Trade {trade['id']}: {trade['symbol']} @ {trade['price']}")

        # Check for very long hold times
        long_holds = []
        for p in positions:
            if p['opened_at'] and p['closed_at']:
                try:
                    opened = datetime.fromisoformat(p['opened_at'])
                    closed = datetime.fromisoformat(p['closed_at'])
                    hold_hours = (closed - opened).total_seconds() / 3600
                    if hold_hours > 24:
                        long_holds.append((p['symbol'], hold_hours))
                except ValueError:
                    pass

        if long_holds:
            issues.append(f"INFO: {len(long_holds)} positions held for over 24 hours")

        return issues

    def generate_recommendations(self, metrics: Dict, symbol_stats: Dict, exit_stats: Dict) -> List[str]:
        """Generate actionable recommendations based on analysis"""
        recommendations = []

        # Win rate analysis
        win_rate = metrics['win_rate']
        if win_rate < 33:
            recommendations.append("CRITICAL: Win rate below 33% - with 2:1 R:R ratio, you need at least 33% to break even")
            recommendations.append("  -> Consider: Stricter signal filters, better entry timing, or wider stop losses")
        elif win_rate < 50:
            recommendations.append("INFO: Win rate between 33-50% - acceptable but could be improved")
        else:
            recommendations.append("GOOD: Win rate above 50% is solid")

        # Profit factor analysis
        pf = metrics['profit_factor']
        if pf < 1.0:
            recommendations.append("CRITICAL: Profit factor < 1.0 means you're losing money overall")
            recommendations.append("  -> Average loss is larger than average win, or win rate is too low")
        elif pf < 1.5:
            recommendations.append("WARNING: Profit factor between 1.0-1.5 - marginally profitable but risky")
        elif pf >= 1.5:
            recommendations.append("GOOD: Profit factor >= 1.5 indicates healthy profitability")

        # Average hold time analysis
        avg_hold = metrics['avg_hold_time_hours']
        if avg_hold > 4:
            recommendations.append(f"INFO: Average hold time is {avg_hold:.1f}h - consider tighter stops or lower max hold time")

        # Symbol-specific recommendations
        if symbol_stats:
            worst_symbols = sorted(symbol_stats.items(), key=lambda x: x[1]['total_pnl'])[:3]
            best_symbols = sorted(symbol_stats.items(), key=lambda x: x[1]['total_pnl'], reverse=True)[:3]

            if worst_symbols and worst_symbols[0][1]['total_pnl'] < -5:
                recommendations.append(f"CONSIDER: Remove worst performing symbols: {', '.join(s[0] for s in worst_symbols if s[1]['total_pnl'] < 0)}")

            if best_symbols and best_symbols[0][1]['total_pnl'] > 5:
                recommendations.append(f"GOOD: Best performing symbols: {', '.join(s[0] for s in best_symbols if s[1]['total_pnl'] > 0)}")

        # Exit reason analysis
        if exit_stats:
            for reason, stats in exit_stats.items():
                if reason == 'STOP_LOSS' and stats['count'] > 0:
                    sl_pct = (stats['count'] / metrics['closed_positions'] * 100) if metrics['closed_positions'] > 0 else 0
                    if sl_pct > 60:
                        recommendations.append(f"WARNING: {sl_pct:.0f}% of exits are stop losses - entries may be too late or stops too tight")
                elif reason == 'TIME_EXIT' and stats['count'] > 0:
                    te_pct = (stats['count'] / metrics['closed_positions'] * 100) if metrics['closed_positions'] > 0 else 0
                    if te_pct > 30:
                        recommendations.append(f"INFO: {te_pct:.0f}% of exits are time-based - positions not reaching TP/SL")

        return recommendations

    def print_report(self, days: Optional[int] = None):
        """Print comprehensive analysis report"""
        print("=" * 80)
        print("SCALPERBOT TRADING ANALYSIS REPORT")
        print(f"Generated: {datetime.utcnow().isoformat()}")
        if days:
            print(f"Period: Last {days} days")
        print("=" * 80)

        # Get data
        trades = self.get_all_trades(days)
        positions = self.get_all_positions(days)
        metrics = self.calculate_overall_metrics(days)
        symbol_stats = self.analyze_by_symbol(days)
        exit_stats = self.analyze_by_exit_reason(days)

        # Overall Metrics
        print("\n" + "-" * 40)
        print("OVERALL METRICS")
        print("-" * 40)
        print(f"Total Trades Logged:     {metrics['total_trades']}")
        print(f"Closed Positions:        {metrics['closed_positions']}")
        print(f"Open Positions:          {metrics['open_positions']}")
        print(f"Total PnL:               ${metrics['total_pnl']:.2f}")
        print(f"Win Rate:                {metrics['win_rate']:.1f}%")
        print(f"Wins / Losses:           {metrics['wins']} / {metrics['losses']}")
        print(f"Average Win:             ${metrics['avg_win']:.2f}")
        print(f"Average Loss:            ${metrics['avg_loss']:.2f}")
        print(f"Profit Factor:           {metrics['profit_factor']:.2f}")
        print(f"Largest Win:             ${metrics['largest_win']:.2f}")
        print(f"Largest Loss:            ${metrics['largest_loss']:.2f}")
        print(f"Avg Hold Time:           {metrics['avg_hold_time_hours']:.1f} hours")

        # Trade Status Breakdown
        print("\n" + "-" * 40)
        print("TRADE STATUS BREAKDOWN")
        print("-" * 40)
        for status, count in sorted(metrics['status_breakdown'].items(), key=lambda x: -x[1]):
            print(f"  {status}: {count}")

        # Symbol Performance
        if symbol_stats:
            print("\n" + "-" * 40)
            print("PERFORMANCE BY SYMBOL")
            print("-" * 40)
            sorted_symbols = sorted(symbol_stats.items(), key=lambda x: x[1]['total_pnl'], reverse=True)
            for symbol, stats in sorted_symbols:
                pnl_emoji = "+" if stats['total_pnl'] >= 0 else ""
                print(f"  {symbol:12} | Trades: {stats['trades']:3} | Win Rate: {stats['win_rate']:5.1f}% | PnL: {pnl_emoji}${stats['total_pnl']:.2f}")

        # Exit Reason Analysis
        if exit_stats:
            print("\n" + "-" * 40)
            print("PERFORMANCE BY EXIT REASON")
            print("-" * 40)
            for reason, stats in sorted(exit_stats.items(), key=lambda x: -x[1]['count']):
                print(f"  {reason:15} | Count: {stats['count']:3} | Win Rate: {stats['win_rate']:5.1f}% | Avg PnL: ${stats['avg_pnl']:.2f}")

        # Issues
        issues = self.find_issues(trades, positions)
        if issues:
            print("\n" + "-" * 40)
            print("IDENTIFIED ISSUES")
            print("-" * 40)
            for issue in issues:
                print(f"  {issue}")

        # Recommendations
        recommendations = self.generate_recommendations(metrics, symbol_stats, exit_stats)
        print("\n" + "-" * 40)
        print("RECOMMENDATIONS")
        print("-" * 40)
        for rec in recommendations:
            print(f"  {rec}")

        print("\n" + "=" * 80)


def main():
    """Main entry point"""
    parser = argparse.ArgumentParser(description='Analyze ScalperBot trading data')
    parser.add_argument('--db', type=str, default='scalperbot/trades.db',
                        help='Path to trades database (default: scalperbot/trades.db)')
    parser.add_argument('--days', type=int, default=None,
                        help='Only analyze last N days (default: all)')
    parser.add_argument('--json', action='store_true',
                        help='Output as JSON instead of formatted text')

    args = parser.parse_args()

    try:
        analyzer = TradingAnalyzer(args.db)

        if args.json:
            import json
            metrics = analyzer.calculate_overall_metrics(args.days)
            symbol_stats = analyzer.analyze_by_symbol(args.days)
            exit_stats = analyzer.analyze_by_exit_reason(args.days)

            output = {
                'metrics': metrics,
                'by_symbol': symbol_stats,
                'by_exit_reason': exit_stats,
                'generated_at': datetime.utcnow().isoformat()
            }
            print(json.dumps(output, indent=2, default=str))
        else:
            analyzer.print_report(args.days)

        analyzer.close()

    except FileNotFoundError as e:
        print(f"Error: {e}")
        print("Make sure the database path is correct.")
        sys.exit(1)
    except Exception as e:
        print(f"Error analyzing data: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
