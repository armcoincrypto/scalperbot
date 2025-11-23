"""
Dynamic TP/SL Target Manager

Calculates pair-specific take-profit and stop-loss targets based on ATR
and volatility characteristics.

Usage:
    targets = DynamicTargets()
    tp_price, sl_price = targets.calculate_targets('BTC/USDT', entry_price=86500.0)
"""
from typing import Tuple, Dict
import logging

logger = logging.getLogger(__name__)


class DynamicTargets:
    """
    Manages dynamic TP/SL targets per trading pair

    Targets based on professional scalping parameters:
    - BTC: TP 20-30 bps, SL 12-18 bps → Breakeven WR: 37.5%
    - ETH: TP 25-40 bps, SL 15-25 bps → Breakeven WR: 37.5%
    - SOL: TP 40-80 bps, SL 25-45 bps → Breakeven WR: 38.5%
    - XRP: TP 15-30 bps, SL 10-20 bps → Breakeven WR: 40.0%

    Where: 1 bps = 0.01% = 0.0001
    """

    # Target configurations (in basis points)
    TARGETS = {
        'BTC/USDT': {'tp_bps': 25, 'sl_bps': 15},  # Mid-range of 20-30 and 12-18
        'ETH/USDT': {'tp_bps': 30, 'sl_bps': 20},  # Mid-range of 25-40 and 15-25
        'SOL/USDT': {'tp_bps': 60, 'sl_bps': 35},  # Mid-range of 40-80 and 25-45
        'XRP/USDT': {'tp_bps': 20, 'sl_bps': 15},  # Mid-range of 15-30 and 10-20
    }

    def __init__(self):
        """Initialize dynamic targets manager"""
        logger.info("🎯 Dynamic Targets initialized")
        self._log_breakeven_rates()

    def calculate_targets(self, symbol: str, entry_price: float, side: str = 'LONG') -> Tuple[float, float]:
        """
        Calculate TP and SL prices for a trade

        Args:
            symbol: Trading pair (e.g., 'BTC/USDT')
            entry_price: Entry price
            side: 'LONG' or 'SHORT'

        Returns:
            (tp_price, sl_price)
        """
        config = self.TARGETS.get(symbol)

        if not config:
            logger.warning(f"⚠️ No target config for {symbol}, using BTC defaults")
            config = self.TARGETS['BTC/USDT']

        tp_bps = config['tp_bps']
        sl_bps = config['sl_bps']

        # Convert basis points to decimal
        tp_pct = tp_bps / 10000  # e.g., 25 bps = 0.0025 = 0.25%
        sl_pct = sl_bps / 10000  # e.g., 15 bps = 0.0015 = 0.15%

        if side.upper() == 'LONG':
            tp_price = entry_price * (1 + tp_pct)
            sl_price = entry_price * (1 - sl_pct)
        else:  # SHORT
            tp_price = entry_price * (1 - tp_pct)
            sl_price = entry_price * (1 + sl_pct)

        logger.info(f"🎯 {symbol} Targets: Entry={entry_price:.4f}, TP={tp_price:.4f} (+{tp_bps}bps), SL={sl_price:.4f} (-{sl_bps}bps)")

        return tp_price, sl_price

    def calculate_pnl(
        self,
        symbol: str,
        entry_price: float,
        exit_price: float,
        notional_usdt: float,
        fees_usdt: float = 0.0,
        side: str = 'LONG'
    ) -> Dict:
        """
        Calculate PnL for a trade

        Args:
            symbol: Trading pair
            entry_price: Entry price
            exit_price: Exit price
            notional_usdt: Position size in USDT
            fees_usdt: Total fees paid
            side: 'LONG' or 'SHORT'

        Returns:
            Dict with pnl_usdt, pnl_bps, return_pct, is_tp, is_sl
        """
        tp_price, sl_price = self.calculate_targets(symbol, entry_price, side)

        # Calculate return
        if side.upper() == 'LONG':
            return_pct = (exit_price - entry_price) / entry_price
        else:
            return_pct = (entry_price - exit_price) / entry_price

        # Calculate PnL
        gross_pnl = notional_usdt * return_pct
        net_pnl = gross_pnl - fees_usdt
        pnl_bps = return_pct * 10000

        # Check if TP or SL hit
        is_tp = False
        is_sl = False

        if side.upper() == 'LONG':
            is_tp = exit_price >= tp_price
            is_sl = exit_price <= sl_price
        else:
            is_tp = exit_price <= tp_price
            is_sl = exit_price >= sl_price

        return {
            'pnl_usdt': round(net_pnl, 2),
            'pnl_bps': round(pnl_bps, 1),
            'return_pct': round(return_pct * 100, 2),
            'is_tp': is_tp,
            'is_sl': is_sl,
            'tp_price': tp_price,
            'sl_price': sl_price,
        }

    def get_breakeven_winrate(self, symbol: str) -> float:
        """
        Calculate breakeven win rate for a symbol

        Formula: WR_breakeven = SL / (TP + SL)

        Args:
            symbol: Trading pair

        Returns:
            Breakeven win rate (0.0 to 1.0)
        """
        config = self.TARGETS.get(symbol, self.TARGETS['BTC/USDT'])
        tp_bps = config['tp_bps']
        sl_bps = config['sl_bps']

        breakeven_wr = sl_bps / (tp_bps + sl_bps)
        return breakeven_wr

    def _log_breakeven_rates(self):
        """Log breakeven win rates for all pairs"""
        logger.info("\n" + "="*70)
        logger.info("📊 BREAKEVEN WIN RATES (assuming perfect TP/SL execution)")
        logger.info("="*70)

        for symbol, config in self.TARGETS.items():
            tp = config['tp_bps']
            sl = config['sl_bps']
            be_wr = self.get_breakeven_winrate(symbol)

            logger.info(f"{symbol}: TP={tp}bps, SL={sl}bps → Breakeven WR: {be_wr*100:.1f}%")

        logger.info("="*70 + "\n")

    def print_targets_table(self):
        """Print formatted targets table"""
        print("\n" + "="*70)
        print("🎯 DYNAMIC TP/SL TARGETS")
        print("="*70)
        print(f"{'Pair':<12} {'TP (bps)':<12} {'SL (bps)':<12} {'Breakeven WR':<15}")
        print("-"*70)

        for symbol, config in self.TARGETS.items():
            tp = config['tp_bps']
            sl = config['sl_bps']
            be_wr = self.get_breakeven_winrate(symbol)
            print(f"{symbol:<12} {tp:<12} {sl:<12} {be_wr*100:.1f}%")

        print("="*70)
        print("Note: 1 bps = 0.01% = 0.0001")
        print("="*70 + "\n")


if __name__ == "__main__":
    # Test the targets
    targets = DynamicTargets()
    targets.print_targets_table()

    # Example calculation
    print("\n📝 EXAMPLE CALCULATION:")
    print("-"*70)
    symbol = 'BTC/USDT'
    entry = 86500.0
    notional = 2500.0
    fees = 2.0  # Assume $2 in fees

    tp, sl = targets.calculate_targets(symbol, entry)

    print(f"\nTrade Setup:")
    print(f"  Symbol: {symbol}")
    print(f"  Entry: ${entry:,.2f}")
    print(f"  TP: ${tp:,.2f}")
    print(f"  SL: ${sl:,.2f}")
    print(f"  Notional: ${notional:,.2f}")

    # Simulate TP hit
    pnl_tp = targets.calculate_pnl(symbol, entry, tp, notional, fees)
    print(f"\nIf TP Hit:")
    print(f"  PnL: ${pnl_tp['pnl_usdt']:+.2f} ({pnl_tp['return_pct']:+.2f}%)")

    # Simulate SL hit
    pnl_sl = targets.calculate_pnl(symbol, entry, sl, notional, fees)
    print(f"\nIf SL Hit:")
    print(f"  PnL: ${pnl_sl['pnl_usdt']:+.2f} ({pnl_sl['return_pct']:+.2f}%)")

    print("-"*70 + "\n")
