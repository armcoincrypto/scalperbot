"""
MEXC Exchange Adapter
CCXT-based wrapper for MEXC exchange API
"""
import ccxt
from typing import Dict, List, Optional, Any
import logging

logger = logging.getLogger(__name__)


class MEXCAdapter:
    """MEXC exchange adapter using CCXT"""

    def __init__(self, api_key: str, api_secret: str, dry_run: bool = True):
        self.api_key = api_key
        self.api_secret = api_secret
        self.dry_run = dry_run

        # Initialize CCXT exchange
        self.exchange = ccxt.mexc({
            'apiKey': api_key,
            'secret': api_secret,
            'enableRateLimit': True,
            'options': {
                'defaultType': 'spot',
            }
        })

        if dry_run:
            self.exchange.set_sandbox_mode(True)
            logger.info("🔶 MEXC Adapter initialized in DRY_RUN mode")
        else:
            logger.info("🟢 MEXC Adapter initialized in LIVE mode")

    def fetch_ticker(self, symbol: str) -> Dict[str, Any]:
        """Fetch current ticker data"""
        try:
            ticker = self.exchange.fetch_ticker(symbol)
            return {
                'symbol': symbol,
                'bid': ticker.get('bid'),
                'ask': ticker.get('ask'),
                'last': ticker.get('last'),
                'volume': ticker.get('quoteVolume', 0),
                'timestamp': ticker.get('timestamp')
            }
        except Exception as e:
            logger.error(f"Error fetching ticker for {symbol}: {e}")
            return {}

    def fetch_ohlcv(
        self,
        symbol: str,
        timeframe: str = '1m',
        limit: int = 100,
        since: Optional[int] = None
    ) -> List[List]:
        """
        Fetch OHLCV candle data
        Returns: List of [timestamp, open, high, low, close, volume]
        """
        try:
            ohlcv = self.exchange.fetch_ohlcv(symbol, timeframe, since=since, limit=limit)
            return ohlcv
        except Exception as e:
            logger.error(f"Error fetching OHLCV for {symbol}: {e}")
            return []

    def fetch_order_book(self, symbol: str, limit: int = 10) -> Dict[str, Any]:
        """Fetch order book (bids/asks)"""
        try:
            orderbook = self.exchange.fetch_order_book(symbol, limit)
            return {
                'symbol': symbol,
                'bids': orderbook.get('bids', []),
                'asks': orderbook.get('asks', []),
                'timestamp': orderbook.get('timestamp')
            }
        except Exception as e:
            logger.error(f"Error fetching orderbook for {symbol}: {e}")
            return {}

    def fetch_balance(self) -> Dict[str, Any]:
        """Fetch account balance"""
        try:
            balance = self.exchange.fetch_balance()
            return balance
        except Exception as e:
            logger.error(f"Error fetching balance: {e}")
            return {}

    def create_market_order(
        self,
        symbol: str,
        side: str,
        amount: float,
        params: Optional[Dict] = None
    ) -> Dict[str, Any]:
        """
        Create a market order
        side: 'buy' or 'sell'
        """
        if self.dry_run:
            logger.info(f"[DRY_RUN] Would create market {side} order: {amount} {symbol}")
            return {
                'id': 'DRY_RUN_ORDER',
                'symbol': symbol,
                'side': side,
                'type': 'market',
                'amount': amount,
                'status': 'closed',
                'filled': amount,
                'dry_run': True
            }

        try:
            order = self.exchange.create_market_order(symbol, side, amount, params)
            logger.info(f"✅ Market {side} order created: {order.get('id')} {amount} {symbol}")
            return order
        except Exception as e:
            logger.error(f"❌ Error creating market order for {symbol}: {e}")
            raise

    def create_limit_order(
        self,
        symbol: str,
        side: str,
        amount: float,
        price: float,
        params: Optional[Dict] = None
    ) -> Dict[str, Any]:
        """
        Create a limit order
        side: 'buy' or 'sell'
        """
        if self.dry_run:
            logger.info(f"[DRY_RUN] Would create limit {side} order: {amount} {symbol} @ {price}")
            return {
                'id': 'DRY_RUN_ORDER',
                'symbol': symbol,
                'side': side,
                'type': 'limit',
                'amount': amount,
                'price': price,
                'status': 'open',
                'dry_run': True
            }

        try:
            order = self.exchange.create_limit_order(symbol, side, amount, price, params)
            logger.info(f"✅ Limit {side} order created: {order.get('id')} {amount} {symbol} @ {price}")
            return order
        except Exception as e:
            logger.error(f"❌ Error creating limit order for {symbol}: {e}")
            raise

    def fetch_order(self, order_id: str, symbol: str) -> Dict[str, Any]:
        """Fetch order status"""
        try:
            order = self.exchange.fetch_order(order_id, symbol)
            return order
        except Exception as e:
            logger.error(f"Error fetching order {order_id}: {e}")
            return {}

    def cancel_order(self, order_id: str, symbol: str) -> bool:
        """Cancel an open order"""
        if self.dry_run:
            logger.info(f"[DRY_RUN] Would cancel order: {order_id}")
            return True

        try:
            self.exchange.cancel_order(order_id, symbol)
            logger.info(f"✅ Order cancelled: {order_id}")
            return True
        except Exception as e:
            logger.error(f"❌ Error cancelling order {order_id}: {e}")
            return False

    def get_market_info(self, symbol: str) -> Dict[str, Any]:
        """Get market information (min/max order size, tick size, etc.)"""
        try:
            markets = self.exchange.load_markets()
            market = markets.get(symbol, {})
            return {
                'symbol': symbol,
                'min_amount': market.get('limits', {}).get('amount', {}).get('min', 0),
                'max_amount': market.get('limits', {}).get('amount', {}).get('max', 0),
                'min_cost': market.get('limits', {}).get('cost', {}).get('min', 0),
                'price_precision': market.get('precision', {}).get('price', 8),
                'amount_precision': market.get('precision', {}).get('amount', 8),
            }
        except Exception as e:
            logger.error(f"Error fetching market info for {symbol}: {e}")
            return {}
