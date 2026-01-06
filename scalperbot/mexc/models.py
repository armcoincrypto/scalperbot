"""
Data models for MEXC API responses.
"""

from dataclasses import dataclass, field
from typing import List, Optional
from datetime import datetime


@dataclass
class Kline:
    """OHLCV candlestick data."""
    timestamp: int  # Open time in ms
    open: float
    high: float
    low: float
    close: float
    volume: float
    close_time: int
    quote_volume: float

    @property
    def datetime(self) -> datetime:
        """Get timestamp as datetime."""
        return datetime.utcfromtimestamp(self.timestamp / 1000)

    @classmethod
    def from_list(cls, data: list) -> "Kline":
        """Parse from MEXC kline array format."""
        return cls(
            timestamp=int(data[0]),
            open=float(data[1]),
            high=float(data[2]),
            low=float(data[3]),
            close=float(data[4]),
            volume=float(data[5]),
            close_time=int(data[6]),
            quote_volume=float(data[7])
        )


@dataclass
class Ticker24h:
    """24-hour ticker statistics."""
    symbol: str
    price_change: float
    price_change_percent: float
    last_price: float
    high_price: float
    low_price: float
    volume: float
    quote_volume: float
    open_price: float

    @classmethod
    def from_dict(cls, data: dict) -> "Ticker24h":
        """Parse from MEXC API response."""
        return cls(
            symbol=data.get("symbol", ""),
            price_change=float(data.get("priceChange", 0)),
            price_change_percent=float(data.get("priceChangePercent", 0)),
            last_price=float(data.get("lastPrice", 0)),
            high_price=float(data.get("highPrice", 0)),
            low_price=float(data.get("lowPrice", 0)),
            volume=float(data.get("volume", 0)),
            quote_volume=float(data.get("quoteVolume", 0)),
            open_price=float(data.get("openPrice", 0))
        )


@dataclass
class BookTicker:
    """Best bid/ask prices."""
    symbol: str
    bid_price: float
    bid_qty: float
    ask_price: float
    ask_qty: float

    @property
    def spread(self) -> float:
        """Get absolute spread."""
        return self.ask_price - self.bid_price

    @property
    def spread_pct(self) -> float:
        """Get spread as percentage of mid price."""
        mid = (self.bid_price + self.ask_price) / 2
        if mid == 0:
            return float('inf')
        return (self.spread / mid) * 100

    @property
    def mid_price(self) -> float:
        """Get mid price."""
        return (self.bid_price + self.ask_price) / 2

    @classmethod
    def from_dict(cls, data: dict) -> "BookTicker":
        """Parse from MEXC API response."""
        return cls(
            symbol=data.get("symbol", ""),
            bid_price=float(data.get("bidPrice", 0)),
            bid_qty=float(data.get("bidQty", 0)),
            ask_price=float(data.get("askPrice", 0)),
            ask_qty=float(data.get("askQty", 0))
        )


@dataclass
class OrderBook:
    """Order book depth."""
    symbol: str
    bids: List[tuple]  # [(price, qty), ...]
    asks: List[tuple]  # [(price, qty), ...]
    last_update_id: int

    def get_bid_depth(self, levels: int = 5) -> float:
        """Get total bid volume for top N levels."""
        return sum(float(qty) for price, qty in self.bids[:levels])

    def get_ask_depth(self, levels: int = 5) -> float:
        """Get total ask volume for top N levels."""
        return sum(float(qty) for price, qty in self.asks[:levels])

    @classmethod
    def from_dict(cls, data: dict, symbol: str = "") -> "OrderBook":
        """Parse from MEXC API response."""
        return cls(
            symbol=symbol,
            bids=[(float(p), float(q)) for p, q in data.get("bids", [])],
            asks=[(float(p), float(q)) for p, q in data.get("asks", [])],
            last_update_id=data.get("lastUpdateId", 0)
        )


@dataclass
class Order:
    """Order information."""
    order_id: str
    symbol: str
    side: str  # BUY or SELL
    type: str  # LIMIT, MARKET
    status: str  # NEW, FILLED, PARTIALLY_FILLED, CANCELED
    price: float
    orig_qty: float
    executed_qty: float
    cummulative_quote_qty: float
    time: int

    @property
    def is_filled(self) -> bool:
        return self.status == "FILLED"

    @property
    def is_open(self) -> bool:
        return self.status in ("NEW", "PARTIALLY_FILLED")

    @property
    def avg_price(self) -> float:
        """Get average fill price."""
        if self.executed_qty == 0:
            return 0
        return self.cummulative_quote_qty / self.executed_qty

    @classmethod
    def from_dict(cls, data: dict) -> "Order":
        """Parse from MEXC API response."""
        return cls(
            order_id=str(data.get("orderId", "")),
            symbol=data.get("symbol", ""),
            side=data.get("side", ""),
            type=data.get("type", ""),
            status=data.get("status", ""),
            price=float(data.get("price", 0)),
            orig_qty=float(data.get("origQty", 0)),
            executed_qty=float(data.get("executedQty", 0)),
            cummulative_quote_qty=float(data.get("cummulativeQuoteQty", 0)),
            time=int(data.get("time", 0))
        )


@dataclass
class AccountBalance:
    """Account balance for a single asset."""
    asset: str
    free: float
    locked: float

    @property
    def total(self) -> float:
        return self.free + self.locked

    @classmethod
    def from_dict(cls, data: dict) -> "AccountBalance":
        """Parse from MEXC API response."""
        return cls(
            asset=data.get("asset", ""),
            free=float(data.get("free", 0)),
            locked=float(data.get("locked", 0))
        )
