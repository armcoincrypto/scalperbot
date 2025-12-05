"""
REST API Poller
Polls MEXC market data every N seconds
Feeds data into CandleStore and OrderBook
"""
import asyncio
import time
from typing import List
import logging
from exchanges.adapter import MEXCAdapter
from datafeed.candle_store import CandleStore
from datafeed.orderbook import OrderBook

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

        # Track 1h candle updates (don't need to update every cycle)
        self.last_1h_update = 0
        self.update_1h_interval = 1800  # Update 1h candles every 30 minutes

    async def initialize_historical(self):
        """Fetch historical candles on startup to speed up warmup"""
        logger.info("🔄 Fetching historical candles for warmup...")

        for symbol in self.symbols:
            try:
                # Fetch last 500 1m candles (about 8 hours)
                ohlcv_1m = self.exchange.fetch_ohlcv(symbol, '1m', limit=500)

                if ohlcv_1m:
                    self.candle_store.initialize_historical(symbol, ohlcv_1m)
                    logger.info(f"✅ Loaded {len(ohlcv_1m)} 1m candles for {symbol}")
                else:
                    logger.warning(f"⚠️ No 1m data for {symbol}")

                # Small delay to avoid rate limits
                await asyncio.sleep(0.3)

                # Fetch last 50 1h candles (for Smart Breakout HTF filter)
                ohlcv_1h = self.exchange.fetch_ohlcv(symbol, '1h', limit=50)

                if ohlcv_1h:
                    self.candle_store.add_candles_1h_bulk(symbol, ohlcv_1h)
                    logger.info(f"✅ Loaded {len(ohlcv_1h)} 1h candles for {symbol}")
                else:
                    logger.warning(f"⚠️ No 1h data for {symbol}")

            except Exception as e:
                logger.error(f"❌ Error fetching historical data for {symbol}: {e}")

            # Small delay to avoid rate limits
            await asyncio.sleep(0.3)

    async def poll_once(self):
        """Poll all symbols once"""
        current_time = time.time()

        # Check if we should update 1h candles
        update_1h = (current_time - self.last_1h_update) >= self.update_1h_interval

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

                # Periodically update 1h candles
                if update_1h:
                    ohlcv_1h = self.exchange.fetch_ohlcv(symbol, '1h', limit=2)
                    if ohlcv_1h:
                        for candle in ohlcv_1h:
                            ts, op, hi, lo, cl, vol = candle
                            self.candle_store.add_candle_1h(symbol, ts, op, hi, lo, cl, vol)

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

        # Update last 1h update time
        if update_1h:
            self.last_1h_update = current_time
            logger.debug("🔄 Updated 1h candles for all symbols")

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
