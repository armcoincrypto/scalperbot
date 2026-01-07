"""
Trade Simulator - Realistic execution modeling for DRY_RUN mode.

Simulates:
- Maker/taker fees
- Spread-based slippage
- Partial fills probability based on liquidity
"""

import random
from dataclasses import dataclass
from typing import Optional

from scalperbot.config import settings
from scalperbot.log import get_logger

logger = get_logger(__name__)


@dataclass
class SimulatedExecution:
    """Result of a simulated trade execution."""
    intended_price: float
    executed_price: float
    slippage_pct: float

    intended_qty: float
    filled_qty: float
    fill_ratio: float

    fee_rate: float
    fee_amount: float

    net_cost: float  # Total cost including fees and slippage

    is_partial: bool


class TradeSimulator:
    """
    Simulates realistic trade execution for DRY_RUN mode.

    Key features:
    - Applies maker/taker fees (MEXC: 0.1% maker, 0.1% taker for spot)
    - Models slippage based on spread and order size
    - Simulates partial fills for low-liquidity scenarios
    """

    # MEXC spot fees (can be overridden)
    DEFAULT_MAKER_FEE = 0.001  # 0.1%
    DEFAULT_TAKER_FEE = 0.001  # 0.1%

    def __init__(
        self,
        maker_fee: float = DEFAULT_MAKER_FEE,
        taker_fee: float = DEFAULT_TAKER_FEE
    ):
        self.maker_fee = maker_fee
        self.taker_fee = taker_fee

    def simulate_buy(
        self,
        ask_price: float,
        bid_price: float,
        quantity: float,
        order_type: str = "LIMIT",
        available_liquidity: Optional[float] = None,
        volume_24h: Optional[float] = None
    ) -> SimulatedExecution:
        """
        Simulate a buy order execution.

        Args:
            ask_price: Current ask (best offer)
            bid_price: Current bid (best bid)
            quantity: Intended quantity to buy
            order_type: LIMIT or MARKET
            available_liquidity: Available qty at ask level (for partial fill calc)
            volume_24h: 24h volume for liquidity estimation

        Returns:
            SimulatedExecution with realistic execution details
        """
        spread_pct = ((ask_price - bid_price) / bid_price) * 100 if bid_price > 0 else 0

        # Base price is ask for buys
        intended_price = ask_price

        # Calculate slippage
        slippage_pct = self._calculate_slippage(
            spread_pct=spread_pct,
            order_type=order_type,
            quantity=quantity,
            volume_24h=volume_24h,
            side="BUY"
        )

        # Apply slippage (buys get worse price = higher)
        executed_price = intended_price * (1 + slippage_pct / 100)

        # Calculate fill ratio
        fill_ratio = self._calculate_fill_ratio(
            quantity=quantity,
            available_liquidity=available_liquidity,
            volume_24h=volume_24h
        )

        filled_qty = quantity * fill_ratio

        # Fee calculation (taker for market, maker for limit)
        fee_rate = self.taker_fee if order_type == "MARKET" else self.maker_fee
        quote_amount = executed_price * filled_qty
        fee_amount = quote_amount * fee_rate

        # Net cost = quote + fees
        net_cost = quote_amount + fee_amount

        return SimulatedExecution(
            intended_price=intended_price,
            executed_price=executed_price,
            slippage_pct=slippage_pct,
            intended_qty=quantity,
            filled_qty=filled_qty,
            fill_ratio=fill_ratio,
            fee_rate=fee_rate,
            fee_amount=fee_amount,
            net_cost=net_cost,
            is_partial=(fill_ratio < 1.0)
        )

    def simulate_sell(
        self,
        bid_price: float,
        ask_price: float,
        quantity: float,
        order_type: str = "LIMIT",
        available_liquidity: Optional[float] = None,
        volume_24h: Optional[float] = None
    ) -> SimulatedExecution:
        """
        Simulate a sell order execution.

        Args:
            bid_price: Current bid (best bid)
            ask_price: Current ask (best offer)
            quantity: Intended quantity to sell
            order_type: LIMIT or MARKET
            available_liquidity: Available qty at bid level
            volume_24h: 24h volume for liquidity estimation

        Returns:
            SimulatedExecution with realistic execution details
        """
        spread_pct = ((ask_price - bid_price) / bid_price) * 100 if bid_price > 0 else 0

        # Base price is bid for sells
        intended_price = bid_price

        # Calculate slippage
        slippage_pct = self._calculate_slippage(
            spread_pct=spread_pct,
            order_type=order_type,
            quantity=quantity,
            volume_24h=volume_24h,
            side="SELL"
        )

        # Apply slippage (sells get worse price = lower)
        executed_price = intended_price * (1 - slippage_pct / 100)

        # Calculate fill ratio
        fill_ratio = self._calculate_fill_ratio(
            quantity=quantity,
            available_liquidity=available_liquidity,
            volume_24h=volume_24h
        )

        filled_qty = quantity * fill_ratio

        # Fee calculation
        fee_rate = self.taker_fee if order_type == "MARKET" else self.maker_fee
        quote_amount = executed_price * filled_qty
        fee_amount = quote_amount * fee_rate

        # Net proceeds = quote - fees
        net_cost = quote_amount - fee_amount  # Negative = proceeds

        return SimulatedExecution(
            intended_price=intended_price,
            executed_price=executed_price,
            slippage_pct=slippage_pct,
            intended_qty=quantity,
            filled_qty=filled_qty,
            fill_ratio=fill_ratio,
            fee_rate=fee_rate,
            fee_amount=fee_amount,
            net_cost=net_cost,
            is_partial=(fill_ratio < 1.0)
        )

    def _calculate_slippage(
        self,
        spread_pct: float,
        order_type: str,
        quantity: float,
        volume_24h: Optional[float],
        side: str
    ) -> float:
        """
        Calculate expected slippage percentage.

        Slippage model:
        - Base: 50% of spread for limit orders, 100% of spread for market
        - Size impact: Extra slippage if order is large relative to volume
        - Random variance: +/- 20% to simulate market conditions
        """
        # Base slippage from spread
        if order_type == "MARKET":
            base_slippage = spread_pct * 1.0  # Full spread for market
        else:
            base_slippage = spread_pct * 0.3  # Partial spread for limit

        # Size impact (if volume data available)
        size_impact = 0.0
        if volume_24h and volume_24h > 0:
            # Assume quantity is in base currency, estimate impact
            # If order is >1% of daily volume, add extra slippage
            order_pct_of_volume = (quantity / volume_24h) * 100
            if order_pct_of_volume > 1.0:
                size_impact = order_pct_of_volume * 0.1  # 0.1% extra per 1% of volume

        # Random variance (+/- 20%)
        variance = random.uniform(-0.2, 0.2)

        total_slippage = (base_slippage + size_impact) * (1 + variance)

        # Floor at 0 (can't have negative slippage in unfavorable direction)
        return max(0.0, total_slippage)

    def _calculate_fill_ratio(
        self,
        quantity: float,
        available_liquidity: Optional[float],
        volume_24h: Optional[float]
    ) -> float:
        """
        Calculate expected fill ratio (1.0 = full fill).

        Fill model:
        - If liquidity data available: ratio = min(1.0, liquidity/quantity)
        - If only volume: assume 95%+ fills for normal-sized orders
        - Add random variance for market conditions
        """
        base_fill_ratio = 1.0

        if available_liquidity and available_liquidity > 0:
            # Direct liquidity-based calculation
            base_fill_ratio = min(1.0, available_liquidity / quantity)
        elif volume_24h and volume_24h > 0:
            # Estimate from daily volume
            # Assume each level has ~0.5% of daily volume available
            estimated_liquidity = volume_24h * 0.005
            base_fill_ratio = min(1.0, estimated_liquidity / quantity)

        # For small orders relative to typical volume, assume full fill
        if base_fill_ratio > 0.95:
            base_fill_ratio = 1.0

        # Random variance (can reduce fill ratio slightly)
        if base_fill_ratio < 1.0:
            variance = random.uniform(-0.1, 0.05)
            base_fill_ratio = max(0.5, min(1.0, base_fill_ratio * (1 + variance)))

        return base_fill_ratio

    def calculate_realistic_pnl(
        self,
        entry_exec: SimulatedExecution,
        exit_exec: SimulatedExecution
    ) -> float:
        """
        Calculate realistic PnL accounting for all execution costs.

        Returns:
            Net PnL after fees and slippage
        """
        # Entry cost (what we paid)
        entry_cost = entry_exec.net_cost

        # Exit proceeds (what we received)
        exit_proceeds = exit_exec.net_cost

        # PnL = proceeds - cost
        # Note: exit_exec.net_cost is already reduced by fees
        pnl = exit_proceeds - entry_cost

        return pnl


# Singleton instance
_simulator: Optional[TradeSimulator] = None


def get_simulator() -> TradeSimulator:
    """Get the trade simulator singleton."""
    global _simulator
    if _simulator is None:
        _simulator = TradeSimulator()
    return _simulator
