"""
REST API Poller
Polls MEXC market data every N seconds
Feeds data into CandleStore and OrderBook
Includes HTF (1H) data for trend confirmation
"""
import asyncio
import time
from typing import List, Dict
import logging
from exchanges.adapter import MEXCAdapter
from datafeed.candle_store import CandleStore
from datafeed.orderbook import OrderBook

logger = logging.getLogger(__name__)


class RESTPoller:
    """
    Polls market data from MEXC REST API
    - Fetches OHLCV candles (1m and 1h)
    - Fetches order book
    - Runs on configurable interval
    """

    def __init__(
        self,
        exchange: MEXCAdapter,
        candle_store: CandleStore,
        orderbook: OrderBook,
        symbols: List[str],
        poll_interval: int = 10
    ):
        self.exchange = exchange
        self.candle_store = candle_store
        self.orderbook = orderbook
        self.symbols = symbols
        self.poll_interval = poll_interval
        self.running = False

        # Track last poll time per symbol
        self.last_poll: dict = {}

        # Store 1H candles separately
        self.candles_1h: Dict[str, list] = {}

        # HTF poll counter (poll 1H less frequently)
        self.htf_poll_counter = 0

    async def initialize_historical(self):
        """Fetch historical candles on startup to speed up warmup"""
        logger.info("Fetching historical candles for warmup...")

        for symbol in self.symbols:
            try:
                # Fetch last 500 1m candles (about 8 hours)
                ohlcv_1m = self.exchange.fetch_ohlcv(symbol, '1m', limit=500)

                if ohlcv_1m:
                    self.candle_store.initialize_historical(symbol, ohlcv_1m)
                    logger.info(f"Loaded {len(ohlcv_1m)} 1m candles for {symbol}")
                else:
                    logger.warning(f"No 1m historical data for {symbol}")

                # Also fetch 1H candles for HTF confirmation
                await asyncio.sleep(0.2)
                ohlcv_1h = self.exchange.fetch_ohlcv(symbol, '1h', limit=100)

                if ohlcv_1h:
                    self.candles_1h[symbol] = ohlcv_1h
                    # Add to candle store for resampling
                    self.candle_store.add_candles_bulk(symbol, ohlcv_1h)
                    logger.info(f"Loaded {len(ohlcv_1h)} 1h candles for {symbol}")

            except Exception as e:
                logger.error(f"Error fetching historical data for {symbol}: {e}")

            # Small delay to avoid rate limits
            await asyncio.sleep(0.5)

    async def poll_once(self):
        """Poll all symbols once"""
        self.htf_poll_counter += 1

        for symbol in self.symbols:
            try:
                # Fetch latest OHLCV (1m)
                ohlcv = self.exchange.fetch_ohlcv(symbol, '1m', limit=2)

                if ohlcv:
                    # Add latest candle(s)
                    for candle in ohlcv:
                        timestamp, open_p, high, low, close, volume = candle
                        self.candle_store.add_candle(
                            symbol, timestamp, open_p, high, low, close, volume
                        )

                # Fetch order book
                ob_data = self.exchange.fetch_order_book(symbol, limit=10)

                if ob_data:
                    self.orderbook.update(
                        symbol,
                        ob_data.get('bids', []),
                        ob_data.get('asks', []),
                        ob_data.get('timestamp', int(time.time() * 1000))
                    )

                # Fetch 1H candles less frequently (every 6 cycles = ~1 min)
                if self.htf_poll_counter % 6 == 0:
                    ohlcv_1h = self.exchange.fetch_ohlcv(symbol, '1h', limit=5)
                    if ohlcv_1h:
                        self.candles_1h[symbol] = ohlcv_1h
                        logger.debug(f"Updated 1h candles for {symbol}")

            except Exception as e:
                logger.error(f"Error polling {symbol}: {e}")

    async def run(self):
        """Main polling loop"""
        self.running = True
        logger.info(f"🚀 REST Poller started (interval: {self.poll_interval}s)")

        # Initialize with historical data first
        await self.initialize_historical()

        cycle = 0
        while self.running:
            cycle += 1
            start_time = time.time()

            logger.debug(f"🔄 Poll cycle #{cycle}")

            await self.poll_once()

            # Sleep for remaining time
            elapsed = time.time() - start_time
            sleep_time = max(0, self.poll_interval - elapsed)

            if sleep_time > 0:
                await asyncio.sleep(sleep_time)
            else:
                logger.warning(f"⚠️ Poll cycle took {elapsed:.2f}s (longer than {self.poll_interval}s interval)")

    def stop(self):
        """Stop the poller"""
        self.running = False
        logger.info("⏹️ REST Poller stopped")
