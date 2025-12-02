#!/usr/bin/env python3
"""
ScalperBot Log & Trade Analysis Service
Reads bot.log and trades.db, produces diagnostics and recommendations

Usage:
    python tools/analyzer.py --log bot.log --db trades.db --outdir ./analysis
    python tools/analyzer.py --help
"""
import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, Optional

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from tools.log_parser import LogParser
from tools.db_reader import DBReader
from tools.recommendation_engine import RecommendationEngine, AnalysisConfig


def parse_args():
    """Parse command line arguments"""
    parser = argparse.ArgumentParser(
        description="ScalperBot Log & Trade Analysis Service",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    # Basic analysis
    python tools/analyzer.py --log bot.log --db scalperbot/trades.db

    # Custom output directory
    python tools/analyzer.py --log bot.log --db trades.db --outdir /root/scalperbot/analysis

    # Analyze last 14 days
    python tools/analyzer.py --log bot.log --db trades.db --days 14

    # Lower minimum trades threshold for testing
    python tools/analyzer.py --log bot.log --db trades.db --min-trades 5
        """
    )

    parser.add_argument(
        '--log', '-l',
        default='bot.log',
        help='Path to bot.log file (default: bot.log)'
    )

    parser.add_argument(
        '--db', '-d',
        default='scalperbot/trades.db',
        help='Path to trades.db file (default: scalperbot/trades.db)'
    )

    parser.add_argument(
        '--outdir', '-o',
        default='analysis',
        help='Output directory for reports (default: ./analysis)'
    )

    parser.add_argument(
        '--days',
        type=int,
        default=7,
        help='Number of days to analyze (default: 7)'
    )

    parser.add_argument(
        '--max-lines',
        type=int,
        default=200000,
        help='Maximum log lines to process (default: 200000)'
    )

    parser.add_argument(
        '--min-trades',
        type=int,
        default=20,
        help='Minimum trades per symbol for recommendations (default: 20)'
    )

    parser.add_argument(
        '--aggressive-nearmiss',
        type=float,
        default=15.0,
        help='Near-miss rate threshold for aggressive recommendation (default: 15%%)'
    )

    parser.add_argument(
        '--aggressive-winrate',
        type=float,
        default=55.0,
        help='Minimum win rate for aggressive recommendation (default: 55%%)'
    )

    parser.add_argument(
        '--aggressive-pf',
        type=float,
        default=1.15,
        help='Minimum profit factor for aggressive recommendation (default: 1.15)'
    )

    parser.add_argument(
        '--json-only',
        action='store_true',
        help='Only output JSON (no text report)'
    )

    parser.add_argument(
        '--quiet', '-q',
        action='store_true',
        help='Suppress terminal output (only write files)'
    )

    return parser.parse_args()


def ensure_output_dir(outdir: str) -> Path:
    """Ensure output directory exists"""
    path = Path(outdir)
    path.mkdir(parents=True, exist_ok=True)
    return path


def generate_text_report(analysis: Dict[str, Any]) -> str:
    """Generate human-readable text report"""

    lines = []
    ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    lines.append("=" * 70)
    lines.append("SCALPERBOT ANALYSIS REPORT")
    lines.append(f"Generated: {ts}")
    lines.append("=" * 70)
    lines.append("")

    # Time window
    time_window = analysis.get('log_analysis', {}).get('time_window', {})
    if time_window:
        lines.append(f"Analysis Period: {time_window.get('start', 'N/A')} to {time_window.get('end', 'N/A')}")
        lines.append(f"Total Strategy Cycles: {time_window.get('total_cycles', 0)}")
    lines.append("")

    # Global summary
    lines.append("-" * 70)
    lines.append("GLOBAL SUMMARY")
    lines.append("-" * 70)

    log_analysis = analysis.get('log_analysis', {})
    trade_analysis = analysis.get('trade_analysis', {})

    lines.append(f"Lines Processed: {log_analysis.get('lines_processed', 0):,}")
    lines.append(f"Total Signals: {log_analysis.get('total_signals', 0)}")
    lines.append(f"Total Orders: {log_analysis.get('total_orders', 0)}")
    lines.append(f"Total Trades in DB: {trade_analysis.get('total_trades', 0)}")

    global_summary = trade_analysis.get('global_summary', {})
    if global_summary:
        lines.append(f"Win Rate: {global_summary.get('win_rate', 0):.1f}%")
        lines.append(f"Total PnL: ${global_summary.get('total_pnl', 0):.2f}")
    lines.append("")

    # Recommendations summary
    recommendations = analysis.get('recommendations', {})
    summary = recommendations.get('summary', {})

    lines.append("-" * 70)
    lines.append("RECOMMENDATIONS SUMMARY")
    lines.append("-" * 70)

    aggressive = summary.get('aggressive_candidates', [])
    if aggressive:
        lines.append(f"AGGRESSIVE CANDIDATES: {', '.join(aggressive)}")
        lines.append("  -> These symbols have high near-miss rates with good performance.")
        lines.append("  -> Consider loosening breakout thresholds (test for 48h first).")
    else:
        lines.append("AGGRESSIVE CANDIDATES: None")

    lines.append("")

    conservative = summary.get('conservative_symbols', [])
    if conservative:
        lines.append(f"CONSERVATIVE SYMBOLS: {', '.join(conservative)}")
        lines.append("  -> These symbols have poor performance metrics.")
        lines.append("  -> Consider tightening thresholds or reducing exposure.")
    else:
        lines.append("CONSERVATIVE SYMBOLS: None")

    lines.append("")

    insufficient = summary.get('insufficient_data_symbols', [])
    if insufficient:
        lines.append(f"INSUFFICIENT DATA: {', '.join(insufficient)}")

    no_change = summary.get('no_change_symbols', [])
    if no_change:
        lines.append(f"NO CHANGE NEEDED: {', '.join(no_change)}")

    lines.append("")

    # Per-symbol details
    lines.append("-" * 70)
    lines.append("PER-SYMBOL ANALYSIS")
    lines.append("-" * 70)

    symbols_rec = recommendations.get('symbols', {})
    for symbol, rec in symbols_rec.items():
        lines.append("")
        lines.append(f"  {symbol}")
        lines.append(f"  Tag: {rec.get('tag', 'N/A')}")

        metrics = rec.get('metrics_summary', {})
        if metrics:
            lines.append(f"  Near-miss rate: {metrics.get('near_miss_rate', 0):.1f}%")
            lines.append(f"  Win rate: {metrics.get('win_rate', 0):.1f}%")
            lines.append(f"  Profit factor: {metrics.get('profit_factor', 'N/A')}")
            lines.append(f"  Total trades: {metrics.get('total_trades', 0)}")
            lines.append(f"  Signals: {metrics.get('signal_count', 0)}")

        lines.append(f"  Rationale: {rec.get('rationale', 'N/A')}")

        suggestions = rec.get('suggestions', [])
        if suggestions:
            lines.append("  Suggestions:")
            for sug in suggestions:
                lines.append(f"    - {sug['parameter']}: {sug['current']} -> {sug['suggested']}")
                lines.append(f"      Reason: {sug['reasoning']}")
                lines.append(f"      Confidence: {sug['confidence']}")

    lines.append("")

    # Action items
    action_items = recommendations.get('action_items', [])
    if action_items:
        lines.append("-" * 70)
        lines.append("ACTION ITEMS")
        lines.append("-" * 70)
        for i, action in enumerate(action_items, 1):
            lines.append(f"{i}. {action}")
        lines.append("")

    # Errors
    errors = log_analysis.get('recent_errors', [])
    if errors:
        lines.append("-" * 70)
        lines.append("RECENT ERRORS (last 10)")
        lines.append("-" * 70)
        for err in errors[-10:]:
            lines.append(f"  {err[:100]}")
        lines.append("")

    lines.append("=" * 70)
    lines.append("END OF REPORT")
    lines.append("=" * 70)

    return "\n".join(lines)


def print_terminal_summary(analysis: Dict[str, Any]):
    """Print brief summary to terminal"""

    print("\n" + "=" * 60)
    print("SCALPERBOT ANALYSIS COMPLETE")
    print("=" * 60)

    recommendations = analysis.get('recommendations', {})
    summary = recommendations.get('summary', {})

    aggressive = summary.get('aggressive_candidates', [])
    conservative = summary.get('conservative_symbols', [])

    if aggressive:
        print(f"\nAGGRESSIVE CANDIDATES: {', '.join(aggressive)}")
        print("  These have high near-miss rates - consider loosening thresholds")

    if conservative:
        print(f"\nCONSERVATIVE: {', '.join(conservative)}")
        print("  Poor performance - consider tightening or removing")

    if not aggressive and not conservative:
        print("\nNo significant recommendations - metrics within acceptable range")

    # Print action items
    action_items = recommendations.get('action_items', [])
    if action_items:
        print("\nACTION ITEMS:")
        for action in action_items:
            print(f"  - {action[:80]}...")

    print("\n" + "=" * 60)


def main():
    """Main entry point"""

    args = parse_args()

    # Validate inputs
    if not os.path.exists(args.log):
        print(f"ERROR: Log file not found: {args.log}")
        sys.exit(1)

    if not os.path.exists(args.db):
        print(f"WARNING: Database not found: {args.db}")
        print("Proceeding with log-only analysis...")

    # Create output directory
    outdir = ensure_output_dir(args.outdir)
    timestamp = datetime.now().strftime('%Y%m%d_%H%M')

    if not args.quiet:
        print(f"Analyzing log: {args.log}")
        print(f"Database: {args.db}")
        print(f"Output: {outdir}")
        print(f"Days: {args.days}, Max lines: {args.max_lines}")
        print()

    # Parse logs
    if not args.quiet:
        print("Parsing log file...")

    log_parser = LogParser(max_lines=args.max_lines)
    log_data = log_parser.parse_file(args.log, days=args.days)

    if not args.quiet:
        print(f"  Processed {log_data.get('lines_processed', 0):,} lines")
        print(f"  Found {log_data.get('total_cycles', 0)} cycles, {log_data.get('total_signals', 0)} signals")

    # Read trades
    if not args.quiet:
        print("Reading trades database...")

    db_reader = DBReader(args.db)
    trade_data = db_reader.read_trades(days=args.days)
    db_reader.close()

    if 'error' in trade_data and trade_data['error']:
        if not args.quiet:
            print(f"  Warning: {trade_data['error']}")
    else:
        if not args.quiet:
            print(f"  Found {trade_data.get('total_trades', 0)} trades")

    # Generate recommendations
    if not args.quiet:
        print("Generating recommendations...")

    config = AnalysisConfig(
        min_trades=args.min_trades,
        aggressive_nearmiss_pct=args.aggressive_nearmiss,
        aggressive_min_winrate=args.aggressive_winrate,
        aggressive_min_pf=args.aggressive_pf,
    )

    engine = RecommendationEngine(config)
    recommendations = engine.analyze(log_data, trade_data)

    # Build full analysis result
    analysis = {
        'generated_at': datetime.now().isoformat(),
        'config': {
            'log_file': args.log,
            'db_file': args.db,
            'days': args.days,
            'max_lines': args.max_lines,
            'min_trades': args.min_trades,
        },
        'log_analysis': log_data,
        'trade_analysis': trade_data,
        'recommendations': recommendations,
    }

    # Write JSON output
    json_path = outdir / f"analysis_{timestamp}.json"
    with open(json_path, 'w') as f:
        json.dump(analysis, f, indent=2, default=str)

    if not args.quiet:
        print(f"  JSON written: {json_path}")

    # Write text report
    if not args.json_only:
        text_report = generate_text_report(analysis)
        text_path = outdir / f"analysis_{timestamp}.txt"
        with open(text_path, 'w') as f:
            f.write(text_report)

        if not args.quiet:
            print(f"  Text written: {text_path}")

    # Print terminal summary
    if not args.quiet:
        print_terminal_summary(analysis)

    # Exit code based on findings
    aggressive = recommendations.get('summary', {}).get('aggressive_candidates', [])
    conservative = recommendations.get('summary', {}).get('conservative_symbols', [])

    if conservative:
        sys.exit(2)  # Warning: conservative symbols detected
    elif aggressive:
        sys.exit(0)  # Success with recommendations
    else:
        sys.exit(0)  # Success


if __name__ == "__main__":
    main()
