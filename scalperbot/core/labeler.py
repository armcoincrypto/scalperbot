"""
Forward Return Labeler - Labels tick data with actual outcomes.

Runs as a background task to:
1. Find ticks that are old enough (>15 min) and unlabeled
2. Fetch historical klines for those timestamps
3. Calculate forward returns (1m, 5m, 15m, 30m)
4. Calculate MFE/MAE (Maximum Favorable/Adverse Excursion)
5. Determine if TP/SL would have been hit
"""

import asyncio
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Any

from scalperbot.config import settings
from scalperbot.log import get_logger
from scalperbot.mexc import MEXCClient
from scalperbot.storage.db import get_database
from scalperbot.storage.repo import TickRepo, OutcomeRepo

logger = get_logger(__name__)


class OutcomeLabeler:
    """
    Labels historical ticks with forward return outcomes.

    This enables strategy analysis by answering:
    - What actually happened after each signal?
    - Would TP/SL have been hit?
    - What was the optimal exit point?
    """

    def __init__(self):
        self.client = MEXCClient(dry_run=True)  # Read-only
        self.db = None
        self.ticks: Optional[TickRepo] = None
        self.outcomes: Optional[OutcomeRepo] = None
        self.running = False

        # Default TP/SL percentages for analysis
        self.tp_pct = settings.take_profit_pct
        self.sl_pct = settings.base_sl_pct

    async def initialize(self):
        """Initialize database connections."""
        self.db = await get_database()
        self.ticks = TickRepo(self.db)
        self.outcomes = OutcomeRepo(self.db)
        logger.info("Outcome labeler initialized")

    async def start(self):
        """Start the labeling background task."""
        await self.initialize()
        self.running = True
        logger.info("Outcome labeler started")

        while self.running:
            try:
                await self._label_batch()
            except Exception as e:
                logger.error(f"Labeling error: {e}", exc_info=True)

            # Run every 5 minutes
            await asyncio.sleep(300)

    async def stop(self):
        """Stop the labeling task."""
        self.running = False
        await self.client.close()
        logger.info("Outcome labeler stopped")

    async def _label_batch(self, batch_size: int = 50):
        """Label a batch of unlabeled ticks."""
        # Get ticks older than 30 minutes (to have full forward data)
        unlabeled = await self.ticks.get_unlabeled(min_age_minutes=30, limit=batch_size)

        if not unlabeled:
            logger.debug("No unlabeled ticks to process")
            return

        logger.info(f"Labeling {len(unlabeled)} ticks")

        for tick in unlabeled:
            try:
                await self._label_tick(tick)
            except Exception as e:
                logger.error(f"Failed to label tick {tick['id']}: {e}")

    async def _label_tick(self, tick: Dict[str, Any]):
        """Label a single tick with outcome data."""
        symbol = tick["symbol"]
        tick_time = datetime.fromisoformat(tick["timestamp"].replace("Z", "+00:00"))
        entry_price = tick["price"]

        # Fetch 1-minute klines from tick time + 30 minutes
        # We need klines from tick_time to tick_time + 30 minutes
        start_time = int(tick_time.timestamp() * 1000)
        end_time = int((tick_time + timedelta(minutes=35)).timestamp() * 1000)

        try:
            klines = await self.client.get_klines(
                symbol=symbol,
                interval="1m",
                limit=40,
                start_time=start_time,
                end_time=end_time
            )
        except Exception as e:
            logger.warning(f"Could not fetch klines for {symbol} at {tick_time}: {e}")
            return

        if len(klines) < 5:
            logger.debug(f"Insufficient klines for {symbol} ({len(klines)} found)")
            return

        # Calculate forward returns
        returns = self._calculate_returns(entry_price, klines)

        # Calculate MFE/MAE
        mfe_mae = self._calculate_mfe_mae(entry_price, klines)

        # Check TP/SL hits
        tp_sl_hits = self._check_tp_sl_hits(entry_price, klines)

        # Determine which hit first
        first_hit, first_hit_time = self._find_first_hit(entry_price, klines)

        # Store outcome
        await self.outcomes.label(
            tick_id=tick["id"],
            returns=returns,
            mfe_mae=mfe_mae,
            tp_sl_hits=tp_sl_hits,
            first_hit=first_hit,
            first_hit_time_sec=first_hit_time
        )

        logger.debug(
            f"Labeled tick {tick['id']}: {symbol} "
            f"return_5m={returns.get('5m', 0):.2f}% first_hit={first_hit}"
        )

    def _calculate_returns(self, entry_price: float, klines: List) -> Dict[str, float]:
        """Calculate forward returns at different time horizons."""
        returns = {}

        # 1m return (index 1 = 1 minute after)
        if len(klines) > 1:
            returns["1m"] = ((klines[1].close - entry_price) / entry_price) * 100

        # 5m return
        if len(klines) > 5:
            returns["5m"] = ((klines[5].close - entry_price) / entry_price) * 100

        # 15m return
        if len(klines) > 15:
            returns["15m"] = ((klines[15].close - entry_price) / entry_price) * 100

        # 30m return
        if len(klines) > 30:
            returns["30m"] = ((klines[30].close - entry_price) / entry_price) * 100

        return returns

    def _calculate_mfe_mae(self, entry_price: float, klines: List) -> Dict[str, float]:
        """
        Calculate Maximum Favorable Excursion (MFE) and Maximum Adverse Excursion (MAE).

        MFE = highest point reached (potential profit)
        MAE = lowest point reached (worst drawdown)
        """
        mfe_mae = {}

        # 5-minute window
        if len(klines) > 5:
            highs = [k.high for k in klines[:6]]
            lows = [k.low for k in klines[:6]]
            mfe_mae["mfe_5m"] = ((max(highs) - entry_price) / entry_price) * 100
            mfe_mae["mae_5m"] = ((min(lows) - entry_price) / entry_price) * 100

        # 15-minute window
        if len(klines) > 15:
            highs = [k.high for k in klines[:16]]
            lows = [k.low for k in klines[:16]]
            mfe_mae["mfe_15m"] = ((max(highs) - entry_price) / entry_price) * 100
            mfe_mae["mae_15m"] = ((min(lows) - entry_price) / entry_price) * 100

        return mfe_mae

    def _check_tp_sl_hits(self, entry_price: float, klines: List) -> Dict[str, bool]:
        """Check if TP or SL would have been hit in each time window."""
        tp_price = entry_price * (1 + self.tp_pct / 100)
        sl_price = entry_price * (1 - self.sl_pct / 100)

        hits = {}

        # 5-minute window
        if len(klines) > 5:
            hits["tp_5m"] = any(k.high >= tp_price for k in klines[:6])
            hits["sl_5m"] = any(k.low <= sl_price for k in klines[:6])

        # 15-minute window
        if len(klines) > 15:
            hits["tp_15m"] = any(k.high >= tp_price for k in klines[:16])
            hits["sl_15m"] = any(k.low <= sl_price for k in klines[:16])

        return hits

    def _find_first_hit(self, entry_price: float, klines: List) -> tuple[str, Optional[int]]:
        """
        Determine which exit condition was hit first: TP, SL, or NEITHER.

        Returns:
            (hit_type, time_in_seconds)
        """
        tp_price = entry_price * (1 + self.tp_pct / 100)
        sl_price = entry_price * (1 - self.sl_pct / 100)

        for i, kline in enumerate(klines[:31]):  # Check first 30 minutes
            # Check if SL hit first (assume worst case - low checked before high)
            if kline.low <= sl_price:
                return "SL", i * 60  # minutes to seconds

            # Check if TP hit
            if kline.high >= tp_price:
                return "TP", i * 60

        return "NEITHER", None


async def run_labeler():
    """Run the outcome labeler as a standalone process."""
    labeler = OutcomeLabeler()
    try:
        await labeler.start()
    except KeyboardInterrupt:
        await labeler.stop()


if __name__ == "__main__":
    asyncio.run(run_labeler())
