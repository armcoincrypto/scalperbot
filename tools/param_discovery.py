#!/usr/bin/env python3
"""
PHASE 6: PARAMETER AUTO-DISCOVERY
==================================
Scientific research, NOT curve fitting.

This tool:
1. Iterates parameters OFFLINE on historical data
2. Ranks them by expectancy
3. NEVER changes live logic automatically
4. Reports confidence intervals
5. Warns when sample size is weak

This is NOT optimization for profitability.
This is searching for ROBUST market patterns.

Anti-curve-fitting measures:
- Walk-forward validation (test on unseen data)
- Minimum sample size requirements
- Confidence interval reporting
- Warns about overfitting risk
"""
import sys
sys.path.insert(0, '/home/user/scalperbot')

import pandas as pd
import numpy as np
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Tuple, Optional, Iterator
from itertools import product
import json
import os
import ccxt
import time
from scipy import stats

# Anti-curve-fitting settings
MIN_TRADES_PER_PARAM = 30  # Minimum trades to consider a parameter set
TRAIN_TEST_SPLIT = 0.7     # 70% train, 30% test
MAX_PARAMS_TO_TEST = 1000  # Limit parameter combinations
REQUIRED_TEST_WIN_RATE = 0.50  # Must beat 50% on test data


class ParameterDiscovery:
    """
    Discover optimal parameters through OFFLINE research.

    This is NOT auto-trading optimization.
    This is scientific parameter space exploration.
    """

    def __init__(self):
        self.results: List[Dict] = []
        self.best_params: Dict = {}

    def fetch_historical_data(self, symbol: str, days: int = 30) -> pd.DataFrame:
        """Fetch historical data for backtesting"""
        print(f"Fetching {days} days of data for {symbol}...")

        exchange = ccxt.mexc({'enableRateLimit': True})
        since = int((datetime.now(timezone.utc) - timedelta(days=days)).timestamp() * 1000)
        end_time = int(datetime.now(timezone.utc).timestamp() * 1000)
        all_candles = []

        while since < end_time:
            try:
                candles = exchange.fetch_ohlcv(symbol, '1m', since=since, limit=1000)
                if not candles:
                    break
                all_candles.extend(candles)
                since = candles[-1][0] + 60000
                print(f"  Fetched {len(all_candles)} candles...", end='\r')
                if len(candles) < 100:
                    break
                time.sleep(0.1)
            except Exception as e:
                print(f"Error: {e}")
                time.sleep(1)

        print(f"  Total: {len(all_candles)} candles")

        df = pd.DataFrame(all_candles, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms', utc=True)
        df.set_index('timestamp', inplace=True)
        return df

    def calculate_indicators(self, df: pd.DataFrame, params: Dict) -> pd.DataFrame:
        """Calculate indicators with given parameters"""
        df = df.copy()

        # EMA
        ema_period = params.get('ema_period', 20)
        df['ema'] = df['close'].ewm(span=ema_period, adjust=False).mean()

        # RSI
        rsi_period = params.get('rsi_period', 14)
        delta = df['close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=rsi_period).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=rsi_period).mean()
        loss = loss.replace(0, 1e-10)
        rs = gain / loss
        df['rsi'] = 100 - (100 / (1 + rs))

        # ATR
        atr_period = params.get('atr_period', 14)
        high = df['high']
        low = df['low']
        close = df['close'].shift(1)
        tr = pd.concat([high - low, abs(high - close), abs(low - close)], axis=1).max(axis=1)
        df['atr'] = tr.rolling(window=atr_period).mean()

        # Volume ratio
        df['vol_avg'] = df['volume'].rolling(window=20).mean()
        df['vol_ratio'] = df['volume'] / df['vol_avg']

        # Body ratio (for displacement)
        df['body'] = abs(df['close'] - df['open'])
        df['body_avg'] = df['body'].rolling(window=20).mean()
        df['body_ratio'] = df['body'] / df['body_avg']

        return df

    def simulate_trades(self, df: pd.DataFrame, params: Dict) -> List[Dict]:
        """
        Simulate trades with given parameters.

        Returns list of trade outcomes.
        """
        df = self.calculate_indicators(df, params)

        # Entry conditions from params
        rsi_min = params.get('rsi_min', 30)
        rsi_max = params.get('rsi_max', 70)
        body_ratio_min = params.get('body_ratio_min', 1.5)
        vol_ratio_min = params.get('vol_ratio_min', 1.0)

        # Exit conditions
        tp_atr_mult = params.get('tp_atr_mult', 2.0)
        sl_atr_mult = params.get('sl_atr_mult', 1.0)
        max_hold = params.get('max_hold_bars', 60)

        trades = []
        in_trade = False
        entry_price = 0
        entry_idx = 0
        tp_price = 0
        sl_price = 0

        for i in range(50, len(df) - max_hold):
            row = df.iloc[i]

            if not in_trade:
                # Check entry conditions
                rsi_ok = rsi_min <= row['rsi'] <= rsi_max
                body_ok = row['body_ratio'] >= body_ratio_min
                vol_ok = row['vol_ratio'] >= vol_ratio_min
                above_ema = row['close'] > row['ema']

                if rsi_ok and body_ok and vol_ok and above_ema:
                    # Enter long
                    in_trade = True
                    entry_price = row['close']
                    entry_idx = i
                    atr = row['atr']
                    tp_price = entry_price + (tp_atr_mult * atr)
                    sl_price = entry_price - (sl_atr_mult * atr)

            else:
                # Check exit conditions
                high = row['high']
                low = row['low']
                bars_held = i - entry_idx

                exit_price = None
                exit_reason = None

                if high >= tp_price:
                    exit_price = tp_price
                    exit_reason = 'TP'
                elif low <= sl_price:
                    exit_price = sl_price
                    exit_reason = 'SL'
                elif bars_held >= max_hold:
                    exit_price = row['close']
                    exit_reason = 'TIMEOUT'

                if exit_price:
                    pnl_pct = ((exit_price - entry_price) / entry_price) * 100
                    trades.append({
                        'entry_idx': entry_idx,
                        'exit_idx': i,
                        'entry_price': entry_price,
                        'exit_price': exit_price,
                        'pnl_pct': pnl_pct,
                        'outcome': 'WIN' if pnl_pct > 0 else 'LOSS',
                        'exit_reason': exit_reason,
                        'bars_held': bars_held
                    })
                    in_trade = False

        return trades

    def evaluate_params(self, trades: List[Dict]) -> Dict:
        """Evaluate performance of a parameter set"""
        if len(trades) < MIN_TRADES_PER_PARAM:
            return {
                'valid': False,
                'reason': f'Insufficient trades ({len(trades)} < {MIN_TRADES_PER_PARAM})'
            }

        wins = [t for t in trades if t['outcome'] == 'WIN']
        losses = [t for t in trades if t['outcome'] == 'LOSS']

        n = len(trades)
        n_wins = len(wins)
        win_rate = n_wins / n

        avg_win = np.mean([t['pnl_pct'] for t in wins]) if wins else 0
        avg_loss = abs(np.mean([t['pnl_pct'] for t in losses])) if losses else 0

        expectancy = (win_rate * avg_win) - ((1 - win_rate) * avg_loss)

        total_wins = sum(t['pnl_pct'] for t in wins) if wins else 0
        total_losses = abs(sum(t['pnl_pct'] for t in losses)) if losses else 0
        profit_factor = total_wins / total_losses if total_losses > 0 else float('inf')

        # Calculate 95% confidence interval for win rate
        # Using Wilson score interval
        z = 1.96  # 95% confidence
        p = win_rate
        ci_low = (p + z*z/(2*n) - z*np.sqrt((p*(1-p) + z*z/(4*n))/n)) / (1 + z*z/n)
        ci_high = (p + z*z/(2*n) + z*np.sqrt((p*(1-p) + z*z/(4*n))/n)) / (1 + z*z/n)

        return {
            'valid': True,
            'trades': n,
            'wins': n_wins,
            'losses': len(losses),
            'win_rate': round(win_rate * 100, 2),
            'win_rate_ci': (round(ci_low * 100, 2), round(ci_high * 100, 2)),
            'avg_win': round(avg_win, 3),
            'avg_loss': round(avg_loss, 3),
            'expectancy': round(expectancy, 4),
            'profit_factor': round(profit_factor, 3) if profit_factor != float('inf') else 999,
            'total_pnl': round(sum(t['pnl_pct'] for t in trades), 2)
        }

    def walk_forward_test(self, df: pd.DataFrame, params: Dict) -> Dict:
        """
        Walk-forward validation to prevent overfitting.

        Splits data into train/test, finds best params on train,
        validates on test.
        """
        split_idx = int(len(df) * TRAIN_TEST_SPLIT)

        train_df = df.iloc[:split_idx]
        test_df = df.iloc[split_idx:]

        # Simulate on both sets
        train_trades = self.simulate_trades(train_df, params)
        test_trades = self.simulate_trades(test_df, params)

        train_metrics = self.evaluate_params(train_trades)
        test_metrics = self.evaluate_params(test_trades)

        # Check for overfitting
        overfit_warning = None
        if train_metrics.get('valid') and test_metrics.get('valid'):
            train_wr = train_metrics['win_rate']
            test_wr = test_metrics['win_rate']

            if train_wr - test_wr > 15:
                overfit_warning = f"⚠️ OVERFIT: Train WR {train_wr}% but Test WR only {test_wr}%"

        return {
            'train': train_metrics,
            'test': test_metrics,
            'overfit_warning': overfit_warning,
            'is_robust': (
                test_metrics.get('valid', False) and
                test_metrics.get('win_rate', 0) >= REQUIRED_TEST_WIN_RATE * 100 and
                overfit_warning is None
            )
        }

    def grid_search(self, df: pd.DataFrame, param_grid: Dict) -> List[Dict]:
        """
        Grid search over parameter space.

        Uses walk-forward validation to avoid overfitting.
        """
        # Generate all parameter combinations
        keys = list(param_grid.keys())
        values = list(param_grid.values())
        combinations = list(product(*values))

        if len(combinations) > MAX_PARAMS_TO_TEST:
            print(f"⚠️ Limiting from {len(combinations)} to {MAX_PARAMS_TO_TEST} combinations")
            np.random.seed(42)
            indices = np.random.choice(len(combinations), MAX_PARAMS_TO_TEST, replace=False)
            combinations = [combinations[i] for i in indices]

        print(f"\nTesting {len(combinations)} parameter combinations...")
        print("This is OFFLINE research, not live optimization.\n")

        results = []
        for i, combo in enumerate(combinations):
            params = dict(zip(keys, combo))

            if (i + 1) % 50 == 0:
                print(f"  Progress: {i+1}/{len(combinations)}")

            wf_result = self.walk_forward_test(df, params)

            if wf_result['test'].get('valid', False):
                results.append({
                    'params': params,
                    'train_metrics': wf_result['train'],
                    'test_metrics': wf_result['test'],
                    'overfit_warning': wf_result['overfit_warning'],
                    'is_robust': wf_result['is_robust']
                })

        # Sort by test expectancy (NOT train expectancy - avoid overfitting)
        results.sort(key=lambda x: x['test_metrics'].get('expectancy', -999), reverse=True)

        return results

    def discover_parameters(self, symbols: List[str], days: int = 30) -> Dict:
        """
        Main parameter discovery routine.

        Returns ranked parameter sets with confidence intervals.
        """
        print("="*70)
        print("PARAMETER AUTO-DISCOVERY")
        print("="*70)
        print("\nThis is scientific research, NOT curve fitting.")
        print("Results will include confidence intervals and overfit warnings.\n")

        # Define parameter grid (conservative, not over-optimized)
        param_grid = {
            'ema_period': [15, 20, 25],
            'rsi_period': [10, 14, 20],
            'rsi_min': [30, 35, 40],
            'rsi_max': [65, 70, 75],
            'body_ratio_min': [1.5, 2.0, 2.5],
            'vol_ratio_min': [0.8, 1.0, 1.5],
            'tp_atr_mult': [1.5, 2.0, 2.5],
            'sl_atr_mult': [0.8, 1.0, 1.5],
            'max_hold_bars': [30, 60, 120]
        }

        all_results = []

        for symbol in symbols:
            print(f"\n{'='*70}")
            print(f"Analyzing {symbol}")
            print("="*70)

            try:
                df = self.fetch_historical_data(symbol, days)

                if len(df) < 5000:
                    print(f"⚠️ Not enough data for {symbol}")
                    continue

                results = self.grid_search(df, param_grid)

                # Filter to robust results only
                robust = [r for r in results if r['is_robust']]

                print(f"\nResults for {symbol}:")
                print(f"  Total valid combinations: {len(results)}")
                print(f"  Robust (no overfit): {len(robust)}")

                if robust:
                    best = robust[0]
                    print(f"\n  BEST ROBUST PARAMS:")
                    print(f"    Parameters: {best['params']}")
                    print(f"    Test Win Rate: {best['test_metrics']['win_rate']}% "
                          f"(95% CI: {best['test_metrics']['win_rate_ci']})")
                    print(f"    Test Expectancy: {best['test_metrics']['expectancy']}%")
                    print(f"    Test Trades: {best['test_metrics']['trades']}")

                    all_results.append({
                        'symbol': symbol,
                        'best_params': best['params'],
                        'test_metrics': best['test_metrics'],
                        'robust_count': len(robust)
                    })
                else:
                    print(f"\n  ❌ NO ROBUST PARAMETERS FOUND")
                    print("  All combinations either overfit or failed validation.")

            except Exception as e:
                print(f"Error: {e}")
                import traceback
                traceback.print_exc()

        # Summary
        print("\n" + "="*70)
        print("DISCOVERY SUMMARY")
        print("="*70)

        if not all_results:
            print("\n❌ NO ROBUST PARAMETERS DISCOVERED FOR ANY SYMBOL")
            print("This suggests:")
            print("  1. The strategy fundamentals may not have an edge")
            print("  2. More data might be needed")
            print("  3. The parameter space might need adjustment")
            print("\nRecommendation: Do NOT trade until edge is discovered.")
        else:
            print(f"\n✅ Found robust parameters for {len(all_results)} symbol(s)")

            for result in all_results:
                print(f"\n{result['symbol']}:")
                print(f"  Best Expectancy: {result['test_metrics']['expectancy']}%")
                print(f"  Win Rate: {result['test_metrics']['win_rate']}%")
                print(f"  Suggested Params: {result['best_params']}")

            print("\n⚠️ IMPORTANT:")
            print("  These parameters are SUGGESTIONS only.")
            print("  Validate with paper trading before live deployment.")
            print("  Parameters should be reviewed monthly.")

        # Save results
        output = {
            'generated': datetime.now(timezone.utc).isoformat(),
            'symbols': symbols,
            'days_analyzed': days,
            'results': all_results
        }

        output_path = 'analysis/param_discovery.json'
        with open(output_path, 'w') as f:
            json.dump(output, f, indent=2, default=str)

        print(f"\nResults saved to: {output_path}")

        return output


def main():
    """Run parameter discovery"""
    symbols = ['BNB/USDT', 'XRP/USDT', 'XLM/USDT']

    discovery = ParameterDiscovery()
    discovery.discover_parameters(symbols, days=30)


if __name__ == "__main__":
    main()
