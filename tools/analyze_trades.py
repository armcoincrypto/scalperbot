"""
Trade Performance Analyzer
Analyzes trade logs and computes key metrics: Win Rate, Profit Factor, R:R, etc.

Usage:
    python tools/analyze_trades.py trades.csv

CSV Format:
    symbol,entry_ts,entry_price,exit_ts,exit_price,notional_usdt,fees_usdt,side
"""
import csv
import sys
from decimal import Decimal, getcontext
from pathlib import Path

getcontext().prec = 12


def load_trades(path="trades.csv"):
    """Load trades from CSV file"""
    trades = []
    with open(path, newline='') as f:
        reader = csv.DictReader(f)
        for r in reader:
            entry_price = Decimal(r['entry_price'])
            exit_price = Decimal(r['exit_price'])
            notional = Decimal(r['notional_usdt'])
            fees = Decimal(r.get('fees_usdt', '0') or '0')
            side = r.get('side', 'LONG').upper()

            # Calculate PnL
            if side == 'LONG':
                pnl_pct = (exit_price - entry_price) / entry_price
            else:
                pnl_pct = (entry_price - exit_price) / entry_price

            pnl_usdt = (pnl_pct * notional) - fees

            trades.append({
                'symbol': r['symbol'],
                'entry_ts': r['entry_ts'],
                'exit_ts': r['exit_ts'],
                'entry_price': float(entry_price),
                'exit_price': float(exit_price),
                'notional': float(notional),
                'pnl_usdt': float(pnl_usdt),
                'return_pct': float(pnl_pct * 100),
                'fees': float(fees),
                'side': side,
            })
    return trades


def metrics(trades):
    """Calculate performance metrics"""
    if not trades:
        return {
            'trades': 0,
            'wins': 0,
            'losses': 0,
            'win_rate_pct': 0,
            'total_pnl_usdt': 0,
            'profit_factor': 0,
            'avg_win_usdt': 0,
            'avg_loss_usdt': 0,
            'avg_rr': 0,
            'max_win': 0,
            'max_loss': 0,
        }

    wins = [t for t in trades if t['pnl_usdt'] > 0]
    losses = [t for t in trades if t['pnl_usdt'] <= 0]

    total_pnl = sum(t['pnl_usdt'] for t in trades)
    gross_win = sum(t['pnl_usdt'] for t in wins)
    gross_loss = abs(sum(t['pnl_usdt'] for t in losses))  # positive number

    pf = (gross_win / gross_loss) if gross_loss > 0 else float('inf')
    avg_win = (gross_win / len(wins)) if wins else 0
    avg_loss = (gross_loss / len(losses)) if losses else 0
    win_rate = (len(wins) / len(trades) * 100) if trades else 0
    avg_rr = (avg_win / avg_loss) if avg_loss > 0 else 0

    max_win = max([t['pnl_usdt'] for t in wins]) if wins else 0
    max_loss = min([t['pnl_usdt'] for t in losses]) if losses else 0

    return {
        'trades': len(trades),
        'wins': len(wins),
        'losses': len(losses),
        'win_rate_pct': round(win_rate, 2),
        'total_pnl_usdt': round(total_pnl, 6),
        'profit_factor': round(pf, 3),
        'avg_win_usdt': round(avg_win, 6),
        'avg_loss_usdt': round(avg_loss, 6),
        'avg_rr': round(avg_rr, 3),
        'max_win': round(max_win, 6),
        'max_loss': round(max_loss, 6),
    }


def per_pair_metrics(trades):
    """Calculate metrics per trading pair"""
    pairs = {}
    for t in trades:
        symbol = t['symbol']
        if symbol not in pairs:
            pairs[symbol] = []
        pairs[symbol].append(t)

    return {symbol: metrics(pair_trades) for symbol, pair_trades in pairs.items()}


def print_report(trades):
    """Print formatted performance report"""
    m = metrics(trades)

    print("\n" + "="*70)
    print("📊 TRADE PERFORMANCE REPORT")
    print("="*70)
    print(f"Total Trades: {m['trades']}")
    print(f"Wins: {m['wins']} | Losses: {m['losses']}")
    print(f"Win Rate: {m['win_rate_pct']}%")
    print(f"Total PnL: ${m['total_pnl_usdt']:.2f} USDT")
    print(f"Profit Factor: {m['profit_factor']}")
    print(f"Avg Win: ${m['avg_win_usdt']:.2f} | Avg Loss: ${m['avg_loss_usdt']:.2f}")
    print(f"Avg R:R: {m['avg_rr']:.2f}")
    print(f"Max Win: ${m['max_win']:.2f} | Max Loss: ${m['max_loss']:.2f}")
    print("="*70)

    # Per-pair breakdown
    pair_metrics = per_pair_metrics(trades)
    if len(pair_metrics) > 1:
        print("\n📈 PER-PAIR BREAKDOWN:")
        print("-"*70)
        for symbol, pm in pair_metrics.items():
            print(f"\n{symbol}:")
            print(f"  Trades: {pm['trades']} | WR: {pm['win_rate_pct']}% | PF: {pm['profit_factor']}")
            print(f"  Total PnL: ${pm['total_pnl_usdt']:.2f} | Avg R:R: {pm['avg_rr']:.2f}")
        print("-"*70)

    # Decision guidance
    print("\n🎯 DECISION GUIDANCE:")
    print("-"*70)
    if m['trades'] < 20:
        print("⚠️  Sample size too small (<20 trades). Keep collecting data.")
    elif m['win_rate_pct'] >= 60 and m['profit_factor'] >= 1.25:
        print("✅ SCALE UP: Win rate ≥60% and PF ≥1.25 → Increase position size 10-25%")
    elif m['win_rate_pct'] >= 40 and m['profit_factor'] >= 1.1:
        print("⏸️  HOLD STEADY: Performance acceptable. Keep collecting data.")
    elif m['win_rate_pct'] < 40 or m['profit_factor'] < 1.0:
        print("🔧 TIGHTEN FILTERS: WR <40% or PF <1.0 → Increase expansion threshold by 3-5 bps")
    else:
        print("📊 Monitor closely. Performance borderline.")
    print("="*70 + "\n")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python tools/analyze_trades.py <trades.csv>")
        print("\nCSV format:")
        print("symbol,entry_ts,entry_price,exit_ts,exit_price,notional_usdt,fees_usdt,side")
        sys.exit(1)

    csv_path = sys.argv[1]

    if not Path(csv_path).exists():
        print(f"❌ File not found: {csv_path}")
        sys.exit(1)

    trades = load_trades(csv_path)
    print_report(trades)

    # Show sample trades
    print("\n📝 SAMPLE TRADES (first 10):")
    print("-"*70)
    for i, t in enumerate(trades[:10], 1):
        pnl_emoji = "🟢" if t['pnl_usdt'] > 0 else "🔴"
        print(f"{i}. {pnl_emoji} {t['symbol']}: ${t['pnl_usdt']:+.2f} ({t['return_pct']:+.2f}%) "
              f"| Entry: {t['entry_price']:.4f} → Exit: {t['exit_price']:.4f}")
    print("-"*70 + "\n")
