#!/usr/bin/env python3
"""
Backtesting Tool for ScalperBot Strategies
Tests strategies on historical data to validate profitability

Usage:
    python tools/backtest.py                      # Default: 7 days, all pairs
    python tools/backtest.py --days 30            # Test 30 days of data
    python tools/backtest.py --symbol SOL/USDT   # Test single pair
    python tools/backtest.py --export results.json
"""
import argparse
import json
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Any, Optional, Tuple
from dataclasses import dataclass, field
import pandas as pd
import numpy as np

# Add parent directory
sys.path.insert(0, str(Path(__file__).parent.parent))

try:
    import ccxt
except ImportError:
    print("Error: ccxt not installed. Run: pip install ccxt")
    sys.exit(1)


@dataclass
class Trade:
    """Represents a simulated trade"""
    symbol: str
    entry_time: datetime
    entry_price: float
    quantity: float
    take_profit: float
    stop_loss: float
    exit_time: Optional[datetime] = None
    exit_price: Optional[float] = None
    exit_reason: Optional[str] = None
    pnl: float = 0.0
    pnl_pct: float = 0.0
    highest_price: float = 0.0
    bars_held: int = 0


@dataclass
class BacktestResult:
    """Results from backtesting"""
    symbol: str
    period_start: datetime
    period_end: datetime
    total_bars: int
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    total_pnl: float = 0.0
    total_pnl_pct: float = 0.0
    win_rate: float = 0.0
    profit_factor: float = 0.0
    avg_win: float = 0.0
    avg_loss: float = 0.0
    avg_bars_held: float = 0.0
    max_drawdown: float = 0.0
    trades: List[Trade] = field(default_factory=list)


class SmartBreakoutBacktester:
    """Backtester for Smart Breakout Strategy"""

    def __init__(self):
        self.exchange = ccxt.mexc()

        # Strategy parameters (matching smart_breakout.py)
        self.ema_period = 20
        self.rsi_period = 14
        self.atr_period = 14
        self.volume_multiplier = 1.2
        self.breakout_lookback = 20
        self.rsi_oversold = 35
        self.rsi_overbought = 75
        self.htf_rsi_limit = 80

        # Position sizing
        self.position_size_usd = 100

        # Exit settings
        self.trailing_enabled = True
        self.trailing_activation_pct = 0.5
        self.trailing_stop_pct = 0.3
        self.max_hold_bars = 360  # 6 hours at 1m bars

    def fetch_historical_data(self, symbol: str, timeframe: str, days: int) -> pd.DataFrame:
        """Fetch historical OHLCV data"""
        print(f"  Fetching {days} days of {timeframe} data for {symbol}...")

        all_candles = []
        since = int((datetime.utcnow() - timedelta(days=days)).timestamp() * 1000)

        while True:
            try:
                candles = self.exchange.fetch_ohlcv(symbol, timeframe, since=since, limit=1000)
                if not candles:
                    break

                all_candles.extend(candles)
                since = candles[-1][0] + 1

                # Rate limiting
                time.sleep(0.1)

                if len(candles) < 1000:
                    break

            except Exception as e:
                print(f"  Error fetching data: {e}")
                break

        if not all_candles:
            return pd.DataFrame()

        df = pd.DataFrame(all_candles, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
        df = df.drop_duplicates(subset=['timestamp'])
        df = df.sort_values('timestamp').reset_index(drop=True)

        print(f"  Fetched {len(df)} {timeframe} candles")
        return df

    def calculate_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """Calculate all technical indicators"""
        df = df.copy()

        # EMA
        df['ema20'] = df['close'].ewm(span=20, adjust=False).mean()

        # RSI
        delta = df['close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
        rs = gain / loss
        df['rsi'] = 100 - (100 / (1 + rs))

        # ATR
        high = df['high']
        low = df['low']
        close_prev = df['close'].shift(1)
        tr1 = high - low
        tr2 = abs(high - close_prev)
        tr3 = abs(low - close_prev)
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        df['atr'] = tr.rolling(window=14).mean()

        # Volume average
        df['vol_avg'] = df['volume'].rolling(window=20).mean()

        # Resistance (20-bar high, excluding last 2)
        df['resistance'] = df['high'].shift(2).rolling(window=20).max()

        # Is green candle
        df['is_green'] = df['close'] > df['open']

        return df

    def resample_to_hourly(self, df_1m: pd.DataFrame) -> pd.DataFrame:
        """Resample 1m data to 1h"""
        df = df_1m.set_index('timestamp')
        df_1h = df.resample('1h').agg({
            'open': 'first',
            'high': 'max',
            'low': 'min',
            'close': 'last',
            'volume': 'sum'
        }).dropna()
        df_1h = df_1h.reset_index()
        return self.calculate_indicators(df_1h)

    def check_entry_conditions(self, df_1m: pd.DataFrame, df_1h: pd.DataFrame, idx: int) -> Tuple[bool, str]:
        """Check if all entry conditions are met at given index"""

        if idx < 50:  # Need enough data
            return False, "Not enough data"

        row = df_1m.iloc[idx]
        prev_row = df_1m.iloc[idx - 1]

        # Get corresponding 1H bar
        current_time = row['timestamp']
        hour_start = current_time.replace(minute=0, second=0, microsecond=0)

        htf_data = df_1h[df_1h['timestamp'] <= hour_start]
        if len(htf_data) < 25:
            return False, "Not enough HTF data"

        htf_row = htf_data.iloc[-1]

        # FILTER 1: HTF Trend (price within 1% of EMA, RSI not overbought)
        htf_price = htf_row['close']
        htf_ema = htf_row['ema20']
        htf_rsi = htf_row['rsi']

        if pd.isna(htf_ema) or pd.isna(htf_rsi):
            return False, "HTF indicators not ready"

        price_near_ema = htf_price >= htf_ema * 0.99
        rsi_ok = htf_rsi < self.htf_rsi_limit

        if not (price_near_ema and rsi_ok):
            return False, "HTF trend not OK"

        # FILTER 2: RSI Momentum
        current_rsi = row['rsi']
        if pd.isna(current_rsi):
            return False, "RSI not ready"

        rsi_3bars_ago = df_1m.iloc[idx - 3]['rsi'] if idx >= 3 else current_rsi

        in_range = self.rsi_oversold <= current_rsi <= self.rsi_overbought
        trending_up = current_rsi > rsi_3bars_ago

        if not (in_range and trending_up):
            return False, "RSI conditions not met"

        # FILTER 3: Volume confirms direction
        vol_ratio = row['volume'] / row['vol_avg'] if row['vol_avg'] > 0 else 0
        volume_surge = vol_ratio >= self.volume_multiplier
        is_green = row['is_green']

        if not (volume_surge and is_green):
            return False, "Volume not confirming"

        # FILTER 4: Breakout with confirmation
        resistance = row['resistance']
        if pd.isna(resistance):
            return False, "Resistance not calculated"

        prev_broke_out = prev_row['close'] > resistance
        current_holds = row['low'] > resistance * 0.998
        price_above = row['close'] > resistance

        if not (prev_broke_out and current_holds and price_above):
            return False, "Breakout not confirmed"

        # FILTER 5: Not extended
        atr = row['atr']
        ema = row['ema20']
        if pd.isna(atr) or pd.isna(ema) or atr == 0:
            return False, "ATR not ready"

        distance_from_ema = (row['close'] - ema) / atr

        if distance_from_ema >= 2.0:
            return False, "Price too extended"

        return True, "All filters passed"

    def simulate_trade(self, df: pd.DataFrame, entry_idx: int, entry_price: float) -> Trade:
        """Simulate a trade from entry to exit"""

        atr = df.iloc[entry_idx]['atr']

        # Calculate TP/SL based on ATR
        tp_price = entry_price + (2 * atr)
        sl_price = entry_price - (1 * atr)

        quantity = self.position_size_usd / entry_price

        trade = Trade(
            symbol=df.iloc[entry_idx].get('symbol', 'UNKNOWN'),
            entry_time=df.iloc[entry_idx]['timestamp'],
            entry_price=entry_price,
            quantity=quantity,
            take_profit=tp_price,
            stop_loss=sl_price,
            highest_price=entry_price
        )

        trailing_stop = None

        # Simulate bar by bar
        for i in range(entry_idx + 1, len(df)):
            bar = df.iloc[i]
            high = bar['high']
            low = bar['low']
            close = bar['close']

            trade.bars_held += 1

            # Update highest price
            if high > trade.highest_price:
                trade.highest_price = high

                # Check trailing stop activation
                if self.trailing_enabled:
                    profit_pct = ((high - entry_price) / entry_price) * 100
                    if profit_pct >= self.trailing_activation_pct:
                        trailing_stop = high * (1 - self.trailing_stop_pct / 100)
                        trade.stop_loss = max(trade.stop_loss, trailing_stop)

            # Check stop loss (use low of candle)
            if low <= trade.stop_loss:
                trade.exit_time = bar['timestamp']
                trade.exit_price = trade.stop_loss
                trade.exit_reason = 'STOP_LOSS' if trailing_stop is None else 'TRAILING_STOP'
                break

            # Check take profit (use high of candle)
            if high >= trade.take_profit:
                trade.exit_time = bar['timestamp']
                trade.exit_price = trade.take_profit
                trade.exit_reason = 'TAKE_PROFIT'
                break

            # Check max hold time
            if trade.bars_held >= self.max_hold_bars:
                trade.exit_time = bar['timestamp']
                trade.exit_price = close
                trade.exit_reason = 'TIMEOUT'
                break

        # If no exit, close at last bar
        if trade.exit_price is None:
            last_bar = df.iloc[-1]
            trade.exit_time = last_bar['timestamp']
            trade.exit_price = last_bar['close']
            trade.exit_reason = 'END_OF_DATA'

        # Calculate PnL
        trade.pnl = (trade.exit_price - trade.entry_price) * trade.quantity
        trade.pnl_pct = ((trade.exit_price - trade.entry_price) / trade.entry_price) * 100

        return trade

    def run_backtest(self, symbol: str, days: int = 7) -> Optional[BacktestResult]:
        """Run backtest for a single symbol"""
        print(f"\n{'='*60}")
        print(f"Backtesting {symbol} - {days} days")
        print(f"{'='*60}")

        # Fetch data
        df_1m = self.fetch_historical_data(symbol, '1m', days)
        if df_1m.empty:
            print(f"  No data for {symbol}")
            return None

        # Calculate indicators
        df_1m = self.calculate_indicators(df_1m)
        df_1m['symbol'] = symbol

        # Resample to 1H
        df_1h = self.resample_to_hourly(df_1m)

        result = BacktestResult(
            symbol=symbol,
            period_start=df_1m.iloc[0]['timestamp'],
            period_end=df_1m.iloc[-1]['timestamp'],
            total_bars=len(df_1m)
        )

        # Simulate trading
        in_position = False
        last_exit_idx = 0
        signals_checked = 0

        print(f"  Scanning {len(df_1m)} bars for signals...")

        for idx in range(50, len(df_1m) - 10):
            if in_position:
                continue
            if idx <= last_exit_idx:
                continue

            signals_checked += 1
            passed, reason = self.check_entry_conditions(df_1m, df_1h, idx)

            if passed:
                entry_price = df_1m.iloc[idx]['close']
                trade = self.simulate_trade(df_1m, idx, entry_price)
                result.trades.append(trade)

                # Find exit index
                exit_time = trade.exit_time
                exit_rows = df_1m[df_1m['timestamp'] >= exit_time]
                if not exit_rows.empty:
                    last_exit_idx = exit_rows.index[0]

                in_position = False  # Ready for next trade

        # Calculate statistics
        result.total_trades = len(result.trades)

        if result.total_trades > 0:
            wins = [t for t in result.trades if t.pnl > 0]
            losses = [t for t in result.trades if t.pnl <= 0]

            result.winning_trades = len(wins)
            result.losing_trades = len(losses)
            result.total_pnl = sum(t.pnl for t in result.trades)
            result.total_pnl_pct = sum(t.pnl_pct for t in result.trades)

            result.win_rate = (result.winning_trades / result.total_trades) * 100

            total_win_pnl = sum(t.pnl for t in wins) if wins else 0
            total_loss_pnl = abs(sum(t.pnl for t in losses)) if losses else 0

            if total_loss_pnl > 0:
                result.profit_factor = total_win_pnl / total_loss_pnl
            elif total_win_pnl > 0:
                result.profit_factor = float('inf')

            result.avg_win = total_win_pnl / len(wins) if wins else 0
            result.avg_loss = total_loss_pnl / len(losses) if losses else 0
            result.avg_bars_held = sum(t.bars_held for t in result.trades) / result.total_trades

            # Calculate max drawdown
            cumulative_pnl = 0
            peak = 0
            max_dd = 0
            for trade in result.trades:
                cumulative_pnl += trade.pnl
                peak = max(peak, cumulative_pnl)
                drawdown = peak - cumulative_pnl
                max_dd = max(max_dd, drawdown)
            result.max_drawdown = max_dd

        # Print results
        self.print_result(result)

        return result

    def print_result(self, result: BacktestResult):
        """Print backtest result"""
        print(f"\n  📊 RESULTS FOR {result.symbol}")
        print(f"  {'-'*50}")
        print(f"  Period: {result.period_start.strftime('%Y-%m-%d')} to {result.period_end.strftime('%Y-%m-%d')}")
        print(f"  Total Bars: {result.total_bars:,}")
        print(f"  Total Trades: {result.total_trades}")

        if result.total_trades > 0:
            print(f"  Winning Trades: {result.winning_trades}")
            print(f"  Losing Trades: {result.losing_trades}")
            print(f"  Win Rate: {result.win_rate:.1f}%")
            pf = f"{result.profit_factor:.2f}" if result.profit_factor != float('inf') else "∞"
            print(f"  Profit Factor: {pf}")
            print(f"  Total PnL: ${result.total_pnl:.2f}")
            print(f"  Avg Win: ${result.avg_win:.2f}")
            print(f"  Avg Loss: ${result.avg_loss:.2f}")
            print(f"  Avg Bars Held: {result.avg_bars_held:.0f} (~{result.avg_bars_held/60:.1f}h)")
            print(f"  Max Drawdown: ${result.max_drawdown:.2f}")

            # Show exit reasons
            exit_reasons = {}
            for t in result.trades:
                exit_reasons[t.exit_reason] = exit_reasons.get(t.exit_reason, 0) + 1
            print(f"  Exit Reasons: {exit_reasons}")
        else:
            print(f"  ❌ No trades generated")

    def run_multi_symbol_backtest(self, symbols: List[str], days: int = 7) -> Dict[str, Any]:
        """Run backtest for multiple symbols"""
        print("\n" + "="*60)
        print("SMART BREAKOUT STRATEGY BACKTEST")
        print(f"Symbols: {len(symbols)}")
        print(f"Period: {days} days")
        print("="*60)

        results = []
        for symbol in symbols:
            try:
                result = self.run_backtest(symbol, days)
                if result:
                    results.append(result)
                time.sleep(0.5)  # Rate limiting between symbols
            except Exception as e:
                print(f"Error backtesting {symbol}: {e}")

        # Aggregate results
        print("\n" + "="*60)
        print("📈 AGGREGATE RESULTS")
        print("="*60)

        total_trades = sum(r.total_trades for r in results)
        total_wins = sum(r.winning_trades for r in results)
        total_losses = sum(r.losing_trades for r in results)
        total_pnl = sum(r.total_pnl for r in results)

        all_trades = []
        for r in results:
            all_trades.extend(r.trades)

        win_pnl = sum(t.pnl for t in all_trades if t.pnl > 0)
        loss_pnl = abs(sum(t.pnl for t in all_trades if t.pnl <= 0))

        win_rate = (total_wins / total_trades * 100) if total_trades > 0 else 0
        profit_factor = (win_pnl / loss_pnl) if loss_pnl > 0 else float('inf')

        print(f"\nTotal Symbols Tested: {len(results)}")
        print(f"Total Trades: {total_trades}")
        print(f"Winning Trades: {total_wins}")
        print(f"Losing Trades: {total_losses}")
        print(f"Win Rate: {win_rate:.1f}%")
        pf_str = f"{profit_factor:.2f}" if profit_factor != float('inf') else "∞"
        print(f"Profit Factor: {pf_str}")
        print(f"Total PnL: ${total_pnl:.2f}")

        # Check acceptance criteria
        print("\n" + "-"*60)
        print("🎯 ACCEPTANCE CRITERIA CHECK:")
        print("-"*60)

        wr_ok = win_rate >= 40
        pf_ok = profit_factor >= 1.15
        trades_ok = total_trades >= 50  # Lower threshold for backtest

        print(f"  {'✅' if wr_ok else '❌'} Win Rate: {win_rate:.1f}% (target: ≥40%)")
        print(f"  {'✅' if pf_ok else '❌'} Profit Factor: {pf_str} (target: ≥1.15)")
        print(f"  {'✅' if trades_ok else '❌'} Trades: {total_trades} (target: ≥50 for backtest)")

        if wr_ok and pf_ok and trades_ok:
            print("\n  🚀 STRATEGY LOOKS PROMISING!")
        else:
            print("\n  ⚠️  STRATEGY NEEDS ADJUSTMENT")

        # Per-symbol summary
        print("\n" + "-"*60)
        print("📊 PER-SYMBOL SUMMARY:")
        print("-"*60)
        print(f"  {'Symbol':<12} {'Trades':<8} {'Wins':<6} {'Win%':<8} {'PnL':<12} {'PF':<8}")
        print(f"  {'-'*12} {'-'*8} {'-'*6} {'-'*8} {'-'*12} {'-'*8}")

        for r in sorted(results, key=lambda x: x.total_pnl, reverse=True):
            pf = f"{r.profit_factor:.2f}" if r.profit_factor != float('inf') else "∞"
            print(f"  {r.symbol:<12} {r.total_trades:<8} {r.winning_trades:<6} "
                  f"{r.win_rate:>5.1f}%   ${r.total_pnl:>8.2f}    {pf:<8}")

        return {
            'total_trades': total_trades,
            'win_rate': win_rate,
            'profit_factor': profit_factor,
            'total_pnl': total_pnl,
            'results': results
        }


def main():
    parser = argparse.ArgumentParser(description="Backtest Smart Breakout Strategy")
    parser.add_argument('--days', type=int, default=7, help='Number of days to backtest (default: 7)')
    parser.add_argument('--symbol', type=str, help='Single symbol to test (default: all pairs)')
    parser.add_argument('--export', type=str, help='Export results to JSON file')

    args = parser.parse_args()

    backtester = SmartBreakoutBacktester()

    # Default trading pairs
    default_symbols = [
        'SOL/USDT', 'BNB/USDT', 'BCH/USDT', 'LTC/USDT',
        'XLM/USDT', 'ADA/USDT', 'TRX/USDT', 'DOGE/USDT',
        'XRP/USDT', 'AVAX/USDT', 'LINK/USDT', 'POL/USDT'
    ]

    if args.symbol:
        symbols = [args.symbol]
    else:
        symbols = default_symbols

    results = backtester.run_multi_symbol_backtest(symbols, args.days)

    if args.export:
        # Export to JSON
        export_data = {
            'timestamp': datetime.now().isoformat(),
            'days': args.days,
            'symbols': symbols,
            'total_trades': results['total_trades'],
            'win_rate': results['win_rate'],
            'profit_factor': results['profit_factor'] if results['profit_factor'] != float('inf') else 'Infinity',
            'total_pnl': results['total_pnl'],
            'per_symbol': []
        }

        for r in results['results']:
            export_data['per_symbol'].append({
                'symbol': r.symbol,
                'trades': r.total_trades,
                'wins': r.winning_trades,
                'losses': r.losing_trades,
                'win_rate': r.win_rate,
                'profit_factor': r.profit_factor if r.profit_factor != float('inf') else 'Infinity',
                'pnl': r.total_pnl
            })

        with open(args.export, 'w') as f:
            json.dump(export_data, f, indent=2)
        print(f"\nResults exported to {args.export}")


if __name__ == "__main__":
    main()
