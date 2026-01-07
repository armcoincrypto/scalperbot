"""
Safety filters for trade validation.
Critical for low-liquidity environments.
"""

from dataclasses import dataclass
from typing import Optional

from scalperbot.mexc.models import BookTicker, Ticker24h, OrderBook
from scalperbot.config import settings
from scalperbot.log import get_logger

logger = get_logger(__name__)


@dataclass
class FilterResult:
    """Result of safety filter checks."""
    symbol: str
    passed: bool
    spread_pct: float
    volume_24h: float
    bid_depth: float
    ask_depth: float
    spread_ok: bool = True
    volume_ok: bool = True
    depth_ok: bool = True
    rejection_reason: Optional[str] = None


class SafetyFilters:
    """
    Safety filters to reject dangerous trades.

    Checks:
    - Spread: Reject if too wide
    - Volume: Reject if too low (illiquid)
    - Depth: Reject if orderbook can't absorb our size
    """

    def __init__(self):
        self.max_spread_pct = settings.max_spread_pct
        self.min_volume_usdt = settings.min_24h_volume_usdt
        self.position_size = settings.position_size_usdt

    def check(
        self,
        symbol: str,
        book_ticker: BookTicker,
        ticker_24h: Optional[Ticker24h] = None,
        orderbook: Optional[OrderBook] = None
    ) -> FilterResult:
        """
        Run all safety filters.

        Args:
            symbol: Trading symbol
            book_ticker: Best bid/ask data
            ticker_24h: 24h statistics (optional)
            orderbook: Orderbook depth (optional)

        Returns:
            FilterResult with pass/fail and details
        """
        spread_pct = book_ticker.spread_pct
        volume_24h = ticker_24h.quote_volume if ticker_24h else 0

        # Calculate depth if orderbook provided
        bid_depth = 0.0
        ask_depth = 0.0
        if orderbook:
            bid_depth = sum(p * q for p, q in orderbook.bids[:10])
            ask_depth = sum(p * q for p, q in orderbook.asks[:10])

        # === Spread Filter ===
        if spread_pct > self.max_spread_pct:
            return FilterResult(
                symbol=symbol,
                passed=False,
                spread_pct=spread_pct,
                volume_24h=volume_24h,
                bid_depth=bid_depth,
                ask_depth=ask_depth,
                spread_ok=False,
                rejection_reason=f"Spread too wide: {spread_pct:.3f}% > {self.max_spread_pct}%"
            )

        # === Volume Filter ===
        if ticker_24h and volume_24h < self.min_volume_usdt:
            return FilterResult(
                symbol=symbol,
                passed=False,
                spread_pct=spread_pct,
                volume_24h=volume_24h,
                bid_depth=bid_depth,
                ask_depth=ask_depth,
                volume_ok=False,
                rejection_reason=f"Volume too low: ${volume_24h:.0f} < ${self.min_volume_usdt:.0f}"
            )

        # === Depth Filter ===
        # Ensure orderbook can absorb our position size
        if orderbook:
            # For buying, check ask depth
            if ask_depth < self.position_size * 2:
                return FilterResult(
                    symbol=symbol,
                    passed=False,
                    spread_pct=spread_pct,
                    volume_24h=volume_24h,
                    bid_depth=bid_depth,
                    ask_depth=ask_depth,
                    depth_ok=False,
                    rejection_reason=f"Ask depth too shallow: ${ask_depth:.0f}"
                )

        # All filters passed
        return FilterResult(
            symbol=symbol,
            passed=True,
            spread_pct=spread_pct,
            volume_24h=volume_24h,
            bid_depth=bid_depth,
            ask_depth=ask_depth
        )

    def calculate_safe_quantity(
        self,
        symbol: str,
        price: float,
        orderbook: Optional[OrderBook] = None
    ) -> float:
        """
        Calculate safe position size based on liquidity.

        Args:
            symbol: Trading symbol
            price: Current price
            orderbook: Orderbook data

        Returns:
            Safe quantity to trade
        """
        base_quantity = self.position_size / price

        if not orderbook:
            return base_quantity

        # Don't take more than 10% of top-5 ask depth
        ask_volume = sum(q for p, q in orderbook.asks[:5])
        max_safe = ask_volume * 0.1

        return min(base_quantity, max_safe)

    def estimate_slippage(
        self,
        quantity: float,
        orderbook: OrderBook,
        side: str = "BUY"
    ) -> float:
        """
        Estimate slippage for given quantity.

        Args:
            quantity: Order quantity
            orderbook: Current orderbook
            side: BUY or SELL

        Returns:
            Estimated slippage percentage
        """
        levels = orderbook.asks if side == "BUY" else orderbook.bids
        if not levels:
            return float('inf')

        best_price = levels[0][0]
        remaining = quantity
        total_cost = 0.0

        for price, qty in levels:
            fill = min(remaining, qty)
            total_cost += fill * price
            remaining -= fill
            if remaining <= 0:
                break

        if remaining > 0:
            # Not enough liquidity
            return float('inf')

        avg_price = total_cost / quantity
        slippage_pct = abs(avg_price - best_price) / best_price * 100

        return slippage_pct
