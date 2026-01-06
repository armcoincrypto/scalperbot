"""
MEXC Spot REST API client with async support.
Handles authentication, rate limiting, and retries.
"""

import asyncio
import aiohttp
from typing import Dict, List, Optional, Any
from datetime import datetime

from scalperbot.config import settings
from scalperbot.log import get_logger
from scalperbot.mexc.signing import sign_params, get_auth_headers
from scalperbot.mexc.models import (
    Kline, Ticker24h, BookTicker, OrderBook, Order, AccountBalance
)

logger = get_logger(__name__)


class MEXCClientError(Exception):
    """MEXC API error."""
    def __init__(self, code: int, message: str):
        self.code = code
        self.message = message
        super().__init__(f"MEXC Error {code}: {message}")


class MEXCClient:
    """
    Async MEXC Spot API client.

    Features:
    - Automatic request signing
    - Exponential backoff retry
    - Rate limit handling
    - DRY_RUN mode support
    """

    def __init__(
        self,
        api_key: str = "",
        api_secret: str = "",
        base_url: str = "",
        dry_run: bool = True
    ):
        self.api_key = api_key or settings.mexc_api_key
        self.api_secret = api_secret or settings.mexc_api_secret
        self.base_url = (base_url or settings.mexc_base_url).rstrip("/")
        self.dry_run = dry_run if dry_run is not None else settings.dry_run

        self._session: Optional[aiohttp.ClientSession] = None
        self._symbol_info: Dict[str, dict] = {}  # Cache for symbol precision

    async def _get_session(self) -> aiohttp.ClientSession:
        """Get or create aiohttp session."""
        if self._session is None or self._session.closed:
            timeout = aiohttp.ClientTimeout(total=30)
            self._session = aiohttp.ClientSession(timeout=timeout)
        return self._session

    async def close(self):
        """Close the client session."""
        if self._session and not self._session.closed:
            await self._session.close()

    async def _request(
        self,
        method: str,
        endpoint: str,
        params: Optional[Dict] = None,
        signed: bool = False,
        max_retries: int = 3
    ) -> Any:
        """
        Make HTTP request with retry logic.

        Args:
            method: HTTP method (GET, POST, DELETE)
            endpoint: API endpoint
            params: Query parameters
            signed: Whether to sign the request
            max_retries: Max retry attempts

        Returns:
            JSON response data
        """
        url = f"{self.base_url}{endpoint}"
        headers = {}
        params = params or {}

        if signed:
            if not self.api_key or not self.api_secret:
                raise MEXCClientError(0, "API credentials not configured")
            params = sign_params(self.api_key, self.api_secret, params)
            headers = get_auth_headers(self.api_key)

        session = await self._get_session()

        for attempt in range(max_retries):
            try:
                async with session.request(
                    method,
                    url,
                    params=params if method == "GET" else None,
                    json=params if method != "GET" else None,
                    headers=headers
                ) as response:
                    data = await response.json()

                    # Handle rate limit (429)
                    if response.status == 429:
                        wait_time = 2 ** attempt
                        logger.warning(f"Rate limited, waiting {wait_time}s...")
                        await asyncio.sleep(wait_time)
                        continue

                    # Handle server errors (5xx)
                    if response.status >= 500:
                        wait_time = 2 ** attempt
                        logger.warning(f"Server error {response.status}, retry in {wait_time}s...")
                        await asyncio.sleep(wait_time)
                        continue

                    # Handle API errors
                    if isinstance(data, dict) and data.get("code"):
                        code = data.get("code", 0)
                        msg = data.get("msg", "Unknown error")
                        raise MEXCClientError(code, msg)

                    return data

            except aiohttp.ClientError as e:
                wait_time = 2 ** attempt
                logger.warning(f"Request failed: {e}, retry in {wait_time}s...")
                await asyncio.sleep(wait_time)

        raise MEXCClientError(0, f"Max retries ({max_retries}) exceeded")

    # === Public Endpoints ===

    async def ping(self) -> bool:
        """Test API connectivity."""
        try:
            await self._request("GET", "/api/v3/ping")
            return True
        except Exception:
            return False

    async def get_server_time(self) -> int:
        """Get server time in milliseconds."""
        data = await self._request("GET", "/api/v3/time")
        return data.get("serverTime", 0)

    async def get_exchange_info(self, symbol: Optional[str] = None) -> dict:
        """Get exchange information and trading rules."""
        params = {}
        if symbol:
            params["symbol"] = symbol
        return await self._request("GET", "/api/v3/exchangeInfo", params)

    async def get_symbol_info(self, symbol: str) -> dict:
        """Get symbol trading rules (cached)."""
        if symbol not in self._symbol_info:
            info = await self.get_exchange_info(symbol)
            for s in info.get("symbols", []):
                self._symbol_info[s["symbol"]] = s
        return self._symbol_info.get(symbol, {})

    async def get_klines(
        self,
        symbol: str,
        interval: str = "1m",
        limit: int = 30
    ) -> List[Kline]:
        """
        Get candlestick data.

        Args:
            symbol: Trading pair (e.g., "BTCUSDT")
            interval: Kline interval (1m, 5m, 15m, 1h, etc.)
            limit: Number of candles

        Returns:
            List of Kline objects
        """
        params = {
            "symbol": symbol,
            "interval": interval,
            "limit": limit
        }
        data = await self._request("GET", "/api/v3/klines", params)
        return [Kline.from_list(k) for k in data]

    async def get_ticker_24h(self, symbol: str) -> Ticker24h:
        """Get 24h ticker statistics."""
        params = {"symbol": symbol}
        data = await self._request("GET", "/api/v3/ticker/24hr", params)
        return Ticker24h.from_dict(data)

    async def get_book_ticker(self, symbol: str) -> BookTicker:
        """Get best bid/ask prices."""
        params = {"symbol": symbol}
        data = await self._request("GET", "/api/v3/ticker/bookTicker", params)
        return BookTicker.from_dict(data)

    async def get_orderbook(self, symbol: str, limit: int = 20) -> OrderBook:
        """Get order book depth."""
        params = {"symbol": symbol, "limit": limit}
        data = await self._request("GET", "/api/v3/depth", params)
        return OrderBook.from_dict(data, symbol)

    # === Private Endpoints (Signed) ===

    async def get_account(self) -> Dict[str, AccountBalance]:
        """
        Get account balances.

        Returns:
            Dict mapping asset to AccountBalance
        """
        data = await self._request("GET", "/api/v3/account", signed=True)
        balances = {}
        for b in data.get("balances", []):
            balance = AccountBalance.from_dict(b)
            if balance.total > 0:
                balances[balance.asset] = balance
        return balances

    async def get_usdt_balance(self) -> float:
        """Get available USDT balance."""
        balances = await self.get_account()
        usdt = balances.get("USDT")
        return usdt.free if usdt else 0.0

    async def create_order(
        self,
        symbol: str,
        side: str,
        order_type: str,
        quantity: float,
        price: Optional[float] = None,
        time_in_force: str = "GTC"
    ) -> Order:
        """
        Create a new order.

        Args:
            symbol: Trading pair
            side: BUY or SELL
            order_type: LIMIT or MARKET
            quantity: Order quantity
            price: Limit price (required for LIMIT orders)
            time_in_force: GTC, IOC, FOK

        Returns:
            Order object
        """
        # DRY_RUN: Log but don't execute
        if self.dry_run:
            logger.info(f"[DRY_RUN] Would create {side} {order_type} order: "
                       f"{quantity} {symbol} @ {price or 'MARKET'}")
            # Return fake order
            return Order(
                order_id=f"DRY_{int(datetime.utcnow().timestamp()*1000)}",
                symbol=symbol,
                side=side,
                type=order_type,
                status="FILLED" if order_type == "MARKET" else "NEW",
                price=price or 0,
                orig_qty=quantity,
                executed_qty=quantity,
                cummulative_quote_qty=quantity * (price or 0),
                time=int(datetime.utcnow().timestamp() * 1000)
            )

        # Get symbol info for precision
        symbol_info = await self.get_symbol_info(symbol)
        quantity = self._quantize(quantity, symbol_info, "quantity")

        params = {
            "symbol": symbol,
            "side": side,
            "type": order_type,
            "quantity": str(quantity)
        }

        if order_type == "LIMIT":
            if price is None:
                raise ValueError("Price required for LIMIT orders")
            price = self._quantize(price, symbol_info, "price")
            params["price"] = str(price)
            params["timeInForce"] = time_in_force

        data = await self._request("POST", "/api/v3/order", params, signed=True)
        order = Order.from_dict(data)
        logger.info(f"Order created: {order.order_id} {side} {quantity} {symbol}")
        return order

    async def cancel_order(self, symbol: str, order_id: str) -> Order:
        """Cancel an open order."""
        if self.dry_run:
            logger.info(f"[DRY_RUN] Would cancel order {order_id}")
            return Order(
                order_id=order_id,
                symbol=symbol,
                side="",
                type="",
                status="CANCELED",
                price=0,
                orig_qty=0,
                executed_qty=0,
                cummulative_quote_qty=0,
                time=0
            )

        params = {"symbol": symbol, "orderId": order_id}
        data = await self._request("DELETE", "/api/v3/order", params, signed=True)
        return Order.from_dict(data)

    async def get_order(self, symbol: str, order_id: str) -> Order:
        """Get order status."""
        params = {"symbol": symbol, "orderId": order_id}
        data = await self._request("GET", "/api/v3/order", params, signed=True)
        return Order.from_dict(data)

    async def get_open_orders(self, symbol: Optional[str] = None) -> List[Order]:
        """Get all open orders."""
        params = {}
        if symbol:
            params["symbol"] = symbol
        data = await self._request("GET", "/api/v3/openOrders", params, signed=True)
        return [Order.from_dict(o) for o in data]

    # === Helper Methods ===

    def _quantize(self, value: float, symbol_info: dict, field: str) -> float:
        """
        Quantize value to symbol precision.

        Args:
            value: Value to quantize
            symbol_info: Symbol info from exchange
            field: 'price' or 'quantity'

        Returns:
            Quantized value
        """
        filters = {f["filterType"]: f for f in symbol_info.get("filters", [])}

        if field == "price":
            price_filter = filters.get("PRICE_FILTER", {})
            tick_size = float(price_filter.get("tickSize", "0.00000001"))
            precision = len(str(tick_size).rstrip('0').split('.')[-1])
            return round(value, precision)

        elif field == "quantity":
            lot_filter = filters.get("LOT_SIZE", {})
            step_size = float(lot_filter.get("stepSize", "0.00000001"))
            precision = len(str(step_size).rstrip('0').split('.')[-1])
            return round(value, precision)

        return value

    async def buy_market(self, symbol: str, quantity: float) -> Order:
        """Place market buy order."""
        return await self.create_order(symbol, "BUY", "MARKET", quantity)

    async def sell_market(self, symbol: str, quantity: float) -> Order:
        """Place market sell order."""
        return await self.create_order(symbol, "SELL", "MARKET", quantity)

    async def buy_limit(
        self,
        symbol: str,
        quantity: float,
        price: float,
        time_in_force: str = "GTC"
    ) -> Order:
        """Place limit buy order."""
        return await self.create_order(
            symbol, "BUY", "LIMIT", quantity, price, time_in_force
        )

    async def sell_limit(
        self,
        symbol: str,
        quantity: float,
        price: float,
        time_in_force: str = "GTC"
    ) -> Order:
        """Place limit sell order."""
        return await self.create_order(
            symbol, "SELL", "LIMIT", quantity, price, time_in_force
        )
