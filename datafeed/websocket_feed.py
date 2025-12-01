"""
WebSocket Feed Module
Real-time market data from MEXC WebSocket API using pymexc
Handles Protobuf serialization automatically
"""
import asyncio
import time
import logging
import threading
from typing import List, Optional, Callable, Dict, Any

logger = logging.getLogger(__name__)

# Try to import pymexc library
try:
    from pymexc import spot
    WEBSOCKETS_AVAILABLE = True
    logger.info("pymexc library loaded - WebSocket with Protobuf support available")
except ImportError:
    WEBSOCKETS_AVAILABLE = False
    logger.warning("pymexc library not installed. WebSocket feed disabled. Install with: pip install pymexc")


class MEXCWebSocketFeed:
    """
    MEXC WebSocket Feed using pymexc library
    Handles Protobuf serialization automatically

    Streams:
    - Kline: Candlestick updates (1m)
    """

    def __init__(
        self,
        symbols: List[str],
        on_kline: Optional[Callable] = None,
        api_key: Optional[str] = None,
        api_secret: Optional[str] = None
    ):
        self.symbols = symbols
        self.on_kline = on_kline
        self.api_key = api_key
        self.api_secret = api_secret

        self.ws_client = None
        self.running = False
        self.connected = False

        # Stats
        self.messages_received = 0
        self.last_message_time: Optional[float] = None
        self.connection_time: Optional[float] = None

        # Convert symbols to MEXC format (BTC/USDT -> BTCUSDT)
        self.mexc_symbols = [s.replace('/', '') for s in symbols]

        # Thread for WebSocket (pymexc uses sync WebSocket)
        self._ws_thread: Optional[threading.Thread] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    def _format_symbol(self, symbol: str) -> str:
        """Convert MEXC symbol to standard format"""
        # BTCUSDT -> BTC/USDT
        if 'USDT' in symbol:
            base = symbol.replace('USDT', '')
            return f"{base}/USDT"
        return symbol

    def _handle_kline(self, message):
        """Handle kline message from pymexc (called in WS thread)"""
        try:
            self.messages_received += 1
            self.last_message_time = time.time()

            if self.messages_received % 100 == 0:
                logger.info(f"📨 WS kline messages received: {self.messages_received}")

            # pymexc returns protobuf decoded message
            # Extract kline data - format depends on pymexc version
            if hasattr(message, 'symbol'):
                # Protobuf object
                symbol = self._format_symbol(message.symbol)
                kline_data = {
                    'symbol': symbol,
                    'timestamp': int(message.windowstart) if hasattr(message, 'windowstart') else int(time.time() * 1000),
                    'open': float(message.openingprice) if hasattr(message, 'openingprice') else 0,
                    'high': float(message.highestprice) if hasattr(message, 'highestprice') else 0,
                    'low': float(message.lowestprice) if hasattr(message, 'lowestprice') else 0,
                    'close': float(message.closingprice) if hasattr(message, 'closingprice') else 0,
                    'volume': float(message.volume) if hasattr(message, 'volume') else 0,
                }
            elif isinstance(message, dict):
                # Dict format
                symbol = self._format_symbol(message.get('s', message.get('symbol', '')))
                kline_data = {
                    'symbol': symbol,
                    'timestamp': message.get('t', message.get('windowstart', int(time.time() * 1000))),
                    'open': float(message.get('o', message.get('openingprice', 0))),
                    'high': float(message.get('h', message.get('highestprice', 0))),
                    'low': float(message.get('l', message.get('lowestprice', 0))),
                    'close': float(message.get('c', message.get('closingprice', 0))),
                    'volume': float(message.get('v', message.get('volume', 0))),
                }
            else:
                logger.warning(f"Unknown kline message format: {type(message)}")
                return

            # Log first few messages for debugging
            if self.messages_received <= 3:
                logger.info(f"📊 Kline data: {symbol} close=${kline_data['close']:.4f}")

            # Call async handler from sync context
            if self.on_kline and self._loop:
                asyncio.run_coroutine_threadsafe(
                    self.on_kline(kline_data),
                    self._loop
                )

        except Exception as e:
            logger.error(f"Error handling kline message: {e}", exc_info=True)

    def _ws_thread_func(self):
        """WebSocket thread function"""
        try:
            logger.info("🔌 Starting pymexc WebSocket client...")
            # Pass API credentials if available (may help with subscription blocks)
            if self.api_key and self.api_secret:
                logger.info("🔑 Using authenticated WebSocket connection")
                self.ws_client = spot.WebSocket(api_key=self.api_key, api_secret=self.api_secret)
            else:
                logger.info("📡 Using public WebSocket connection")
                self.ws_client = spot.WebSocket()
            self.connected = True
            self.connection_time = time.time()

            # Subscribe to klines for each symbol
            for mexc_symbol in self.mexc_symbols:
                logger.info(f"📡 Subscribing to kline stream: {mexc_symbol}")
                self.ws_client.kline_stream(self._handle_kline, mexc_symbol, "Min1")

            logger.info(f"✅ WebSocket connected and subscribed to {len(self.mexc_symbols)} symbols")

            # Keep thread alive while running
            while self.running:
                time.sleep(1)

        except Exception as e:
            logger.error(f"❌ WebSocket thread error: {e}", exc_info=True)
            self.connected = False

    async def run(self):
        """Start WebSocket feed"""
        if not WEBSOCKETS_AVAILABLE:
            logger.error("pymexc library not available. Cannot start WebSocket feed.")
            return

        self.running = True
        self._loop = asyncio.get_event_loop()

        logger.info("🚀 WebSocket Feed starting with pymexc...")

        # Start WebSocket in background thread (pymexc uses sync WebSocket)
        self._ws_thread = threading.Thread(target=self._ws_thread_func, daemon=True)
        self._ws_thread.start()

        # Wait for connection
        await asyncio.sleep(2)

        if self.connected:
            logger.info("✅ pymexc WebSocket feed is running")
        else:
            logger.warning("⚠️ WebSocket connection may have failed")

    async def stop(self):
        """Stop WebSocket feed"""
        self.running = False
        self.connected = False

        if self.ws_client:
            try:
                self.ws_client.stop()
            except:
                pass

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
    - Uses WebSocket for real-time kline updates when available
    - Falls back to REST polling if WebSocket disconnects
    - Provides seamless data continuity
    """

    def __init__(
        self,
        exchange,
        candle_store,
        orderbook,
        symbols: List[str],
        rest_poll_interval: int = 10,
        api_key: Optional[str] = None,
        api_secret: Optional[str] = None
    ):
        self.exchange = exchange
        self.candle_store = candle_store
        self.orderbook = orderbook
        self.symbols = symbols
        self.rest_poll_interval = rest_poll_interval
        self.api_key = api_key
        self.api_secret = api_secret

        self.ws_feed: Optional[MEXCWebSocketFeed] = None
        self.rest_poller_task: Optional[asyncio.Task] = None
        self.running = False

        # Mode tracking
        self.ws_mode = False
        self.last_ws_update: Dict[str, float] = {}
        self.ws_stale_threshold = 5.0  # seconds

        # Track kline timestamps to detect candle closes
        self._last_kline_ts: Dict[str, int] = {}
        self._last_kline_data: Dict[str, Dict] = {}

    async def _on_kline(self, data: Dict):
        """Handle WebSocket kline update"""
        symbol = data['symbol']
        current_ts = data['timestamp']

        # Detect candle close by timestamp change
        last_ts = self._last_kline_ts.get(symbol)

        if last_ts is not None and current_ts != last_ts:
            # New candle started = previous candle is closed
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
                logger.info(f"🕯️ {symbol} candle closed @ ${prev_data['close']:.4f}")

        # Update tracking for current candle
        self._last_kline_ts[symbol] = current_ts
        self._last_kline_data[symbol] = data

        # Update latest candle in store (real-time price)
        self.candle_store.update_latest(
            symbol,
            data['high'],
            data['low'],
            data['close'],
            data['volume']
        )

        self.last_ws_update[symbol] = time.time()

    async def _rest_fallback_loop(self):
        """REST polling for orderbook and fallback candle updates"""
        while self.running:
            try:
                for symbol in self.symbols:
                    # Always fetch orderbook via REST (more reliable)
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

                    # Check if WebSocket data is stale
                    last_update = self.last_ws_update.get(symbol, 0)
                    age = time.time() - last_update

                    if age > 60:  # If no WS update for 60s, fetch candles via REST
                        try:
                            ohlcv = self.exchange.fetch_ohlcv(symbol, '1m', limit=5)
                            if ohlcv:
                                for candle in ohlcv[:-1]:  # Exclude current incomplete candle
                                    self.candle_store.add_candle(
                                        symbol,
                                        candle[0],  # timestamp
                                        candle[1],  # open
                                        candle[2],  # high
                                        candle[3],  # low
                                        candle[4],  # close
                                        candle[5]   # volume
                                    )
                                logger.debug(f"REST candle fallback for {symbol}")
                        except Exception as e:
                            logger.error(f"REST candle error for {symbol}: {e}")

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

        # Create WebSocket feed with pymexc
        if WEBSOCKETS_AVAILABLE:
            self.ws_feed = MEXCWebSocketFeed(
                symbols=self.symbols,
                on_kline=self._on_kline,
                api_key=self.api_key,
                api_secret=self.api_secret
            )

            # Start WebSocket
            await self.ws_feed.run()
            self.ws_mode = True
            logger.info("🚀 Hybrid Feed started (pymexc WebSocket + REST)")
        else:
            logger.warning("⚠️ pymexc not available, using REST-only mode")
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
            return "WebSocket (pymexc)"
        return "REST (polling)"
