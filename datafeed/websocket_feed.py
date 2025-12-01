"""
WebSocket Feed Module
Real-time market data from MEXC WebSocket API
Provides low-latency ticker, orderbook, and kline updates
"""
import asyncio
import json
import time
import logging
from typing import List, Optional, Callable, Dict, Any
from datetime import datetime

logger = logging.getLogger(__name__)

# Try to import websockets library
try:
    import websockets
    WEBSOCKETS_AVAILABLE = True
except ImportError:
    WEBSOCKETS_AVAILABLE = False
    logger.warning("websockets library not installed. WebSocket feed disabled. Install with: pip install websockets")


class MEXCWebSocketFeed:
    """
    MEXC WebSocket Feed for real-time market data

    Streams:
    - Ticker: Real-time price updates (every trade)
    - Mini Ticker: Aggregated price updates (every second)
    - Orderbook: Depth updates
    - Kline: Candlestick updates
    """

    # MEXC Spot WebSocket endpoint
    WS_URL = "wss://wbs.mexc.com/ws"

    def __init__(
        self,
        symbols: List[str],
        on_ticker: Optional[Callable] = None,
        on_orderbook: Optional[Callable] = None,
        on_kline: Optional[Callable] = None
    ):
        self.symbols = symbols
        self.on_ticker = on_ticker
        self.on_orderbook = on_orderbook
        self.on_kline = on_kline

        self.ws = None
        self.running = False
        self.connected = False
        self.reconnect_delay = 1  # Start with 1 second
        self.max_reconnect_delay = 60  # Max 60 seconds

        # Stats
        self.messages_received = 0
        self.last_message_time: Optional[float] = None
        self.connection_time: Optional[float] = None

        # Convert symbols to MEXC format (BTC/USDT -> BTCUSDT)
        self.mexc_symbols = [s.replace('/', '') for s in symbols]

    def _format_symbol(self, symbol: str) -> str:
        """Convert MEXC symbol to standard format"""
        # BTCUSDT -> BTC/USDT
        if 'USDT' in symbol:
            base = symbol.replace('USDT', '')
            return f"{base}/USDT"
        return symbol

    async def connect(self):
        """Establish WebSocket connection"""
        if not WEBSOCKETS_AVAILABLE:
            logger.error("websockets library not available. Cannot start WebSocket feed.")
            return False

        try:
            logger.info(f"🔌 Connecting to MEXC WebSocket: {self.WS_URL}")
            self.ws = await websockets.connect(
                self.WS_URL,
                ping_interval=20,
                ping_timeout=10,
                close_timeout=5
            )
            self.connected = True
            self.connection_time = time.time()
            self.reconnect_delay = 1  # Reset on successful connect
            logger.info("✅ WebSocket connected successfully")
            return True

        except Exception as e:
            logger.error(f"❌ WebSocket connection failed: {e}")
            self.connected = False
            return False

    async def subscribe(self):
        """Subscribe to market data streams"""
        if not self.ws or not self.connected:
            logger.error("Cannot subscribe: WebSocket not connected")
            return

        # Subscribe to mini tickers (aggregated, lower bandwidth)
        # Format: spot@public.miniTickers.v3.api@UTC+0
        ticker_sub = {
            "method": "SUBSCRIPTION",
            "params": [f"spot@public.miniTicker.v3.api@{sym}" for sym in self.mexc_symbols]
        }

        # Subscribe to orderbook depth (level 5)
        depth_sub = {
            "method": "SUBSCRIPTION",
            "params": [f"spot@public.increase.depth.v3.api@{sym}" for sym in self.mexc_symbols]
        }

        # Subscribe to 1m klines
        kline_sub = {
            "method": "SUBSCRIPTION",
            "params": [f"spot@public.kline.v3.api@{sym}@Min1" for sym in self.mexc_symbols]
        }

        try:
            await self.ws.send(json.dumps(ticker_sub))
            logger.info(f"📡 Subscribed to tickers for {len(self.mexc_symbols)} symbols")

            await self.ws.send(json.dumps(depth_sub))
            logger.info(f"📡 Subscribed to orderbook depth for {len(self.mexc_symbols)} symbols")

            await self.ws.send(json.dumps(kline_sub))
            logger.info(f"📡 Subscribed to klines for {len(self.mexc_symbols)} symbols")

        except Exception as e:
            logger.error(f"❌ Subscription error: {e}")

    async def _handle_message(self, message: str):
        """Process incoming WebSocket message"""
        try:
            data = json.loads(message)
            self.messages_received += 1
            self.last_message_time = time.time()

            # Check message type
            channel = data.get('c', '')  # Channel
            symbol = data.get('s', '')  # Symbol
            msg_data = data.get('d', {})  # Data payload

            # Debug: log message count every 100 messages
            if self.messages_received % 100 == 0:
                logger.info(f"📨 WS messages received: {self.messages_received}")

            # Debug: log kline messages
            if 'kline' in channel.lower():
                logger.info(f"📊 Kline received: {channel} - data keys: {list(msg_data.keys())}")

            if not channel or not msg_data:
                return

            std_symbol = self._format_symbol(symbol)

            # Mini Ticker updates
            if 'miniTicker' in channel:
                if self.on_ticker:
                    ticker_data = {
                        'symbol': std_symbol,
                        'price': float(msg_data.get('c', 0)),  # Last price
                        'open': float(msg_data.get('o', 0)),
                        'high': float(msg_data.get('h', 0)),
                        'low': float(msg_data.get('l', 0)),
                        'volume': float(msg_data.get('v', 0)),
                        'timestamp': msg_data.get('t', int(time.time() * 1000))
                    }
                    await self.on_ticker(ticker_data)

            # Orderbook depth updates
            elif 'depth' in channel:
                if self.on_orderbook:
                    orderbook_data = {
                        'symbol': std_symbol,
                        'bids': [[float(b['p']), float(b['v'])] for b in msg_data.get('bids', [])],
                        'asks': [[float(a['p']), float(a['v'])] for a in msg_data.get('asks', [])],
                        'timestamp': int(time.time() * 1000)
                    }
                    await self.on_orderbook(orderbook_data)

            # Kline updates
            elif 'kline' in channel:
                if self.on_kline:
                    k = msg_data.get('k', {})
                    kline_data = {
                        'symbol': std_symbol,
                        'timestamp': k.get('t', int(time.time() * 1000)),
                        'open': float(k.get('o', 0)),
                        'high': float(k.get('h', 0)),
                        'low': float(k.get('l', 0)),
                        'close': float(k.get('c', 0)),
                        'volume': float(k.get('v', 0)),
                        'is_closed': k.get('x', False)  # True if candle is closed
                    }
                    await self.on_kline(kline_data)

        except json.JSONDecodeError:
            logger.warning(f"Invalid JSON received: {message[:100]}")
        except Exception as e:
            logger.error(f"Error handling message: {e}")

    async def run(self):
        """Main WebSocket loop with auto-reconnect"""
        self.running = True
        logger.info("🚀 WebSocket Feed starting...")

        while self.running:
            try:
                # Connect
                if not await self.connect():
                    logger.warning(f"⏳ Reconnecting in {self.reconnect_delay}s...")
                    await asyncio.sleep(self.reconnect_delay)
                    self.reconnect_delay = min(self.reconnect_delay * 2, self.max_reconnect_delay)
                    continue

                # Subscribe to streams
                await self.subscribe()

                # Message loop
                msg_count = 0
                async for message in self.ws:
                    if not self.running:
                        break
                    msg_count += 1
                    if msg_count <= 5 or msg_count % 50 == 0:
                        logger.info(f"📩 WS msg #{msg_count}: {message[:150] if len(message) > 150 else message}")
                    await self._handle_message(message)

                logger.warning(f"⚠️ WS message loop ended after {msg_count} messages")

            except websockets.exceptions.ConnectionClosed as e:
                logger.warning(f"⚠️ WebSocket connection closed: {e}")
                self.connected = False

            except Exception as e:
                logger.error(f"❌ WebSocket error: {e}")
                self.connected = False

            # Reconnect delay
            if self.running:
                logger.info(f"⏳ Reconnecting in {self.reconnect_delay}s...")
                await asyncio.sleep(self.reconnect_delay)
                self.reconnect_delay = min(self.reconnect_delay * 2, self.max_reconnect_delay)

    async def stop(self):
        """Stop WebSocket feed"""
        self.running = False
        if self.ws:
            await self.ws.close()
        self.connected = False
        logger.info("⏹️ WebSocket Feed stopped")

    def get_stats(self) -> Dict[str, Any]:
        """Get connection statistics"""
        uptime = 0
        if self.connection_time:
            uptime = time.time() - self.connection_time

        return {
            'connected': self.connected,
            'messages_received': self.messages_received,
            'uptime_seconds': uptime,
            'last_message_age': time.time() - self.last_message_time if self.last_message_time else None
        }


class HybridDataFeed:
    """
    Hybrid data feed that combines WebSocket (real-time) with REST (fallback)
    - Uses WebSocket for real-time updates when available
    - Falls back to REST polling if WebSocket disconnects
    - Provides seamless data continuity
    """

    def __init__(
        self,
        exchange,
        candle_store,
        orderbook,
        symbols: List[str],
        rest_poll_interval: int = 10
    ):
        self.exchange = exchange
        self.candle_store = candle_store
        self.orderbook = orderbook
        self.symbols = symbols
        self.rest_poll_interval = rest_poll_interval

        self.ws_feed: Optional[MEXCWebSocketFeed] = None
        self.rest_poller_task: Optional[asyncio.Task] = None
        self.running = False

        # Mode tracking
        self.ws_mode = False
        self.last_ws_update: Dict[str, float] = {}
        self.ws_stale_threshold = 5.0  # seconds

        # Track kline timestamps to detect candle closes (MEXC doesn't have is_closed)
        self._last_kline_ts: Dict[str, int] = {}
        self._last_kline_data: Dict[str, Dict] = {}

    async def _on_ticker(self, data: Dict):
        """Handle WebSocket ticker update"""
        symbol = data['symbol']
        self.last_ws_update[symbol] = time.time()

        # Update orderbook with ticker price (as mid price approximation)
        # Real orderbook updates come from depth stream
        logger.debug(f"WS Ticker: {symbol} = ${data['price']:.4f}")

    async def _on_orderbook(self, data: Dict):
        """Handle WebSocket orderbook update"""
        symbol = data['symbol']
        self.orderbook.update(
            symbol,
            data['bids'],
            data['asks'],
            data['timestamp']
        )
        self.last_ws_update[symbol] = time.time()
        logger.debug(f"WS Orderbook: {symbol} updated")

    async def _on_kline(self, data: Dict):
        """Handle WebSocket kline update"""
        symbol = data['symbol']
        current_ts = data['timestamp']

        # MEXC doesn't provide is_closed, so detect by timestamp change
        last_ts = self._last_kline_ts.get(symbol)

        if last_ts is not None and current_ts != last_ts:
            # New candle started = previous candle is closed
            # Add the PREVIOUS candle (which is now complete)
            prev_data = self._last_kline_data.get(symbol)
            if prev_data:
                self.candle_store.add_candle(
                    symbol,
                    prev_data['timestamp'],
                    prev_data['open'],
                    prev_data['high'],
                    prev_data['low'],
                    prev_data['close'],
                    prev_data['volume']
                )
                logger.info(f"WS Kline: {symbol} candle closed @ ${prev_data['close']:.4f}")

        # Update tracking for current candle
        self._last_kline_ts[symbol] = current_ts
        self._last_kline_data[symbol] = data

        # Always update latest candle in store (real-time price)
        self.candle_store.update_latest(
            symbol,
            data['high'],
            data['low'],
            data['close'],
            data['volume']
        )

        self.last_ws_update[symbol] = time.time()

    async def _rest_fallback_loop(self):
        """REST polling fallback when WebSocket is stale"""
        while self.running:
            try:
                # Check each symbol for stale WS data
                for symbol in self.symbols:
                    last_update = self.last_ws_update.get(symbol, 0)
                    age = time.time() - last_update

                    # If WS data is stale, use REST
                    if age > self.ws_stale_threshold:
                        logger.debug(f"REST fallback for {symbol} (WS stale: {age:.1f}s)")

                        # Fetch orderbook via REST
                        try:
                            ob_data = self.exchange.fetch_order_book(symbol, limit=10)
                            if ob_data:
                                self.orderbook.update(
                                    symbol,
                                    ob_data.get('bids', []),
                                    ob_data.get('asks', []),
                                    ob_data.get('timestamp', int(time.time() * 1000))
                                )
                        except Exception as e:
                            logger.error(f"REST orderbook error for {symbol}: {e}")

            except Exception as e:
                logger.error(f"REST fallback loop error: {e}")

            await asyncio.sleep(self.rest_poll_interval)

    async def initialize_historical(self):
        """Fetch historical candles on startup"""
        logger.info("🔄 Fetching historical candles for warmup...")

        for symbol in self.symbols:
            try:
                ohlcv = self.exchange.fetch_ohlcv(symbol, '1m', limit=500)
                if ohlcv:
                    self.candle_store.initialize_historical(symbol, ohlcv)
                    logger.info(f"✅ Loaded {len(ohlcv)} historical candles for {symbol}")
            except Exception as e:
                logger.error(f"❌ Error fetching historical data for {symbol}: {e}")
            await asyncio.sleep(0.5)

    async def run(self):
        """Start hybrid data feed"""
        self.running = True

        # Initialize historical data first
        await self.initialize_historical()

        # Create WebSocket feed
        if WEBSOCKETS_AVAILABLE:
            self.ws_feed = MEXCWebSocketFeed(
                symbols=self.symbols,
                on_ticker=self._on_ticker,
                on_orderbook=self._on_orderbook,
                on_kline=self._on_kline
            )

            # Start WebSocket in background
            ws_task = asyncio.create_task(self.ws_feed.run())
            self.ws_mode = True
            logger.info("🚀 Hybrid Feed started (WebSocket + REST fallback)")
        else:
            logger.warning("⚠️ WebSocket not available, using REST-only mode")
            self.ws_mode = False

        # Start REST fallback loop
        self.rest_poller_task = asyncio.create_task(self._rest_fallback_loop())

        # Keep running
        while self.running:
            await asyncio.sleep(1)

    def stop(self):
        """Stop hybrid data feed"""
        self.running = False
        if self.ws_feed:
            asyncio.create_task(self.ws_feed.stop())
        if self.rest_poller_task:
            self.rest_poller_task.cancel()
        logger.info("⏹️ Hybrid Feed stopped")

    def get_mode(self) -> str:
        """Get current data feed mode"""
        if self.ws_mode and self.ws_feed and self.ws_feed.connected:
            return "WebSocket (real-time)"
        return "REST (polling)"
