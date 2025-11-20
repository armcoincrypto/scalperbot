"""
Order Book - Bid/Ask tracking
Stores latest bid/ask prices for each symbol
"""
from typing import Dict, List, Optional, Tuple
import logging

logger = logging.getLogger(__name__)


class OrderBook:
    """
    Tracks current order book (bids/asks) for each symbol
    Used for execution price estimation
    """

    def __init__(self):
        # {symbol: {'bids': [[price, size], ...], 'asks': [[price, size], ...], 'timestamp': int}}
        self.books: Dict[str, Dict] = {}

    def update(self, symbol: str, bids: List[List], asks: List[List], timestamp: int):
        """Update order book for a symbol"""
        self.books[symbol] = {
            'bids': bids,  # [[price, size], ...]
            'asks': asks,
            'timestamp': timestamp
        }
        logger.debug(f"OrderBook updated for {symbol}: {len(bids)} bids, {len(asks)} asks")

    def get_best_bid(self, symbol: str) -> Optional[float]:
        """Get best bid price"""
        if symbol not in self.books or not self.books[symbol]['bids']:
            return None
        return self.books[symbol]['bids'][0][0]

    def get_best_ask(self, symbol: str) -> Optional[float]:
        """Get best ask price"""
        if symbol not in self.books or not self.books[symbol]['asks']:
            return None
        return self.books[symbol]['asks'][0][0]

    def get_mid_price(self, symbol: str) -> Optional[float]:
        """Get mid price (average of best bid and ask)"""
        bid = self.get_best_bid(symbol)
        ask = self.get_best_ask(symbol)

        if bid is None or ask is None:
            return None

        return (bid + ask) / 2.0

    def get_spread(self, symbol: str) -> Optional[float]:
        """Get bid-ask spread"""
        bid = self.get_best_bid(symbol)
        ask = self.get_best_ask(symbol)

        if bid is None or ask is None:
            return None

        return ask - bid

    def get_spread_bps(self, symbol: str) -> Optional[float]:
        """Get bid-ask spread in basis points"""
        bid = self.get_best_bid(symbol)
        ask = self.get_best_ask(symbol)

        if bid is None or ask is None or bid == 0:
            return None

        spread = ask - bid
        return (spread / bid) * 10000  # basis points

    def has_data(self, symbol: str) -> bool:
        """Check if we have order book data for symbol"""
        return symbol in self.books and bool(self.books[symbol]['bids']) and bool(self.books[symbol]['asks'])

    def summary(self) -> str:
        """Get summary of order book data"""
        lines = ["📖 OrderBook Summary:"]
        for symbol, book in self.books.items():
            bid = self.get_best_bid(symbol)
            ask = self.get_best_ask(symbol)
            spread_bps = self.get_spread_bps(symbol)
            lines.append(f"  {symbol}: Bid={bid:.4f}, Ask={ask:.4f}, Spread={spread_bps:.1f}bps")
        return "\n".join(lines)
