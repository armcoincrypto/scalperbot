"""
REST API Poller
Polls MEXC market data every N seconds
Feeds data into CandleStore and OrderBook
"""
import asyncio
import time
from typing import List
import logging
from scalperbot.exchanges.adapter import MEXCAdapter
from scalperbot.datafeed.candle_store import CandleStore
from scalperbot.datafeed.orderbook import OrderBook

logger = logging.getLogger(__name__)


class RESTPoller:
    """
    Polls market data from MEXC REST API
    - Fetches OHLCV candles
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

    async def initialize_historical(self):
        """Fetch historical candles on startup to speed up warmup"""
        logger.info("🔄 Fetching historical candles for warmup...")

        for symbol in self.symbols:
            try:
                # Fetch last 500 1m candles (about 8 hours)
                ohlcv = self.exchange.fetch_ohlcv(symbol, '1m', limit=500)

                if ohlcv:
                    self.candle_store.initialize_historical(symbol, ohlcv)
                    logger.info(f"✅ Loaded {len(ohlcv)} historical candles for {symbol}")
                else:
                    logger.warning(f"⚠️ No historical data for {symbol}")

            except Exception as e:
                logger.error(f"❌ Error fetching historical data for {symbol}: {e}")

            # Small delay to avoid rate limits
            await asyncio.sleep(0.5)

    async def poll_once(self):
        """Poll all symbols once"""
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

            except Exception as e:
                logger.error(f"❌ Error polling {symbol}: {e}")

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
