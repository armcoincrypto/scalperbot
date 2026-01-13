"""
MEXC Daily Momentum Watchlist Scanner

Scans all MEXC USDT pairs daily to find coins with strong momentum.
Automatically updates ScalperBot watchlist with top performers.

Features:
- Fetches all tickers in 1 API call (fast, API-limit friendly)
- Stores 15-day rolling window of daily snapshots
- Calculates 10-day momentum
- Prioritizes new listings
- Filters out majors, stables, and dead coins
- Auto-updates watchlist in database
"""

import asyncio
import aiohttp
import json
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass
from typing import List, Dict, Optional, Set
import time

from scalperbot.config import settings
from scalperbot.log import get_logger
from scalperbot.storage.db import get_database

logger = get_logger(__name__)


# ============================================================
# STATIC EXCLUSIONS
# ============================================================

# Major coins (too stable for scalping)
EXCLUDED_MAJORS = {
    "BTCUSDT", "ETHUSDT", "BNBUSDT", "XRPUSDT", "ADAUSDT",
    "SOLUSDT", "DOTUSDT", "MATICUSDT", "AVAXUSDT", "LINKUSDT",
    "LTCUSDT", "BCHUSDT", "XLMUSDT", "ATOMUSDT", "ETCUSDT",
    "TRXUSDT", "NEARUSDT", "ALGOUSDT", "VETUSDT", "ICPUSDT"
}

# Stablecoins and pegged assets
EXCLUDED_STABLES = {
    "USDCUSDT", "USDTUSDT", "BUSDUSDT", "DAIUSDT", "TUSDUSDT",
    "USDPUSDT", "GUSDUSDT", "PAXUSDT", "EURUSDT", "GBPUSDT",
    "USTUSDT", "FRAXUSDT", "LUSDUSDT", "SUSDUSDT", "CUSDUSDT"
}

# Known problematic/scam tokens (add as discovered)
EXCLUDED_SCAMS: Set[str] = set()


@dataclass
class TickerData:
    """Parsed ticker data from MEXC API."""
    symbol: str
    last_price: float
    quote_volume: float
    price_change_pct: float
    trade_count: int


@dataclass
class CoinCandidate:
    """A coin that passed filters and is a candidate for watchlist."""
    symbol: str
    momentum_10d: float
    volume_24h: float
    price: float
    is_new_listing: bool
    days_since_listing: int
    score: float
    spread_pct: float = 0.0      # Bid-ask spread percentage
    bid_depth: float = 0.0       # Bid side depth in USDT
    ask_depth: float = 0.0       # Ask side depth in USDT
    market_quality_ok: bool = True  # Passed spread/depth check


class CoinScanner:
    """
    Daily momentum scanner for MEXC coins.

    Workflow:
    1. Fetch all tickers (1 API call)
    2. Filter out excluded symbols
    3. Store daily snapshot
    4. Calculate 10-day momentum from historical data
    5. Rank and select top candidates
    6. Update watchlist
    """

    # API endpoints
    MEXC_TICKER_URL = "https://api.mexc.com/api/v3/ticker/24hr"
    MEXC_DEPTH_URL = "https://api.mexc.com/api/v3/depth"

    # Fixed parameters (not configurable)
    MAX_SPIKE_PCT = 500.0       # Max 24h change (filter extreme pumps)
    LOOKBACK_DAYS = 10          # Days to calculate momentum
    RETENTION_DAYS = 15         # Days to keep in database
    NEW_LISTING_DAYS = 10       # Consider "new" if first seen within N days
    NEW_LISTING_BONUS = 20.0    # Score bonus for new listings
    CHECK_DEPTH_TOP_N = 50      # Check spread/depth for top N candidates

    def __init__(self):
        self.db = None
        self._session: Optional[aiohttp.ClientSession] = None

        # Load from config (can override via .env)
        self.MIN_VOLUME_USDT = settings.scanner_min_volume
        self.MAX_VOLUME_USDT = settings.scanner_max_volume
        self.MIN_MOMENTUM_PCT = settings.scanner_min_momentum
        self.MAX_SPREAD_PCT = settings.scanner_max_spread
        self.MIN_DEPTH_USDT = settings.scanner_min_depth
        self.TOP_N_COINS = settings.scanner_top_n

    async def initialize(self):
        """Initialize database and HTTP session."""
        self.db = await get_database()
        self._session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=30)
        )
        logger.info("Coin scanner initialized")

    async def close(self):
        """Close HTTP session."""
        if self._session:
            await self._session.close()
            self._session = None

    async def run_daily_scan(self) -> Dict:
        """
        Execute full daily scan.

        Returns dict with scan results:
        {
            'success': bool,
            'total_symbols': int,
            'excluded': int,
            'filtered': int,
            'candidates': int,
            'watchlist': [symbols],
            'duration_sec': float,
            'error': str or None
        }
        """
        start_time = time.time()
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        result = {
            'success': False,
            'total_symbols': 0,
            'excluded': 0,
            'filtered': 0,
            'candidates': 0,
            'watchlist': [],
            'duration_sec': 0,
            'error': None
        }

        try:
            logger.info(f"Starting daily coin scan for {today}")

            # Step 1: Fetch all tickers
            tickers = await self._fetch_all_tickers()
            if not tickers:
                raise Exception("Failed to fetch tickers from MEXC")

            result['total_symbols'] = len(tickers)
            logger.info(f"Fetched {len(tickers)} tickers from MEXC")

            # Step 2: Filter out excluded symbols
            tickers, excluded_count = self._apply_exclusions(tickers)
            result['excluded'] = excluded_count
            logger.info(f"After exclusions: {len(tickers)} symbols")

            # Step 3: Store daily snapshot
            await self._store_daily_snapshot(today, tickers)

            # Step 4: Cleanup old data
            await self._cleanup_old_data()

            # Step 5: Calculate momentum and filter
            candidates = await self._calculate_momentum(today, tickers)
            result['filtered'] = len(tickers) - len(candidates)
            result['candidates'] = len(candidates)
            logger.info(f"Found {len(candidates)} candidates with >30% momentum")

            # Step 6: Rank and select top N
            top_coins = await self._rank_candidates(candidates)
            result['watchlist'] = [c.symbol for c in top_coins]

            # Step 7: Update watchlist in database
            old_watchlist = await self._get_current_watchlist()
            await self._update_watchlist(top_coins)

            # Step 8: Log the run
            await self._log_scan_run(today, result, old_watchlist)

            result['success'] = True
            result['duration_sec'] = time.time() - start_time

            logger.info(
                f"Scan complete in {result['duration_sec']:.1f}s. "
                f"Watchlist: {result['watchlist']}"
            )

        except Exception as e:
            result['error'] = str(e)
            result['duration_sec'] = time.time() - start_time
            logger.error(f"Scan failed: {e}", exc_info=True)

        return result

    async def _fetch_all_tickers(self) -> List[TickerData]:
        """Fetch all tickers from MEXC in one API call."""
        if not self._session:
            await self.initialize()

        for attempt in range(3):
            try:
                logger.info(f"Fetching tickers from {self.MEXC_TICKER_URL} (attempt {attempt + 1})")
                async with self._session.get(self.MEXC_TICKER_URL) as resp:
                    if resp.status == 429:
                        # Rate limited, wait and retry
                        wait = 2 ** attempt
                        logger.warning(f"Rate limited, waiting {wait}s")
                        await asyncio.sleep(wait)
                        continue

                    if resp.status != 200:
                        body = await resp.text()
                        logger.error(f"MEXC API error: status={resp.status}, body={body[:500]}")
                        continue

                    data = await resp.json()
                    logger.info(f"Received {len(data)} raw tickers from MEXC")

                    # Parse and filter USDT pairs only
                    tickers = []
                    usdt_count = 0
                    parse_errors = 0
                    for item in data:
                        symbol = item.get('symbol', '')
                        if not symbol.endswith('USDT'):
                            continue
                        usdt_count += 1

                        try:
                            tickers.append(TickerData(
                                symbol=symbol,
                                last_price=float(item.get('lastPrice') or 0),
                                quote_volume=float(item.get('quoteVolume') or 0),
                                price_change_pct=float(item.get('priceChangePercent') or 0),
                                trade_count=int(item.get('count') or 0)
                            ))
                        except (ValueError, TypeError) as e:
                            parse_errors += 1
                            if parse_errors <= 3:  # Log first few errors
                                logger.warning(f"Parse error for {symbol}: {e}")
                            continue

                    logger.info(f"Parsed {len(tickers)} USDT tickers (found {usdt_count}, errors: {parse_errors})")
                    return tickers

            except asyncio.TimeoutError:
                logger.warning(f"Timeout fetching tickers, attempt {attempt + 1}/3")
                await asyncio.sleep(2 ** attempt)
            except aiohttp.ClientError as e:
                logger.error(f"HTTP client error: {type(e).__name__}: {e}")
                await asyncio.sleep(2 ** attempt)
            except Exception as e:
                logger.error(f"Unexpected error fetching tickers: {type(e).__name__}: {e}", exc_info=True)
                await asyncio.sleep(2 ** attempt)

        logger.error("All 3 attempts failed to fetch tickers")
        return []

    def _apply_exclusions(
        self,
        tickers: List[TickerData]
    ) -> tuple[List[TickerData], int]:
        """Remove excluded symbols (majors, stables, blacklist)."""
        excluded = EXCLUDED_MAJORS | EXCLUDED_STABLES | EXCLUDED_SCAMS

        original_count = len(tickers)
        filtered = [t for t in tickers if t.symbol not in excluded]
        excluded_count = original_count - len(filtered)

        return filtered, excluded_count

    async def _store_daily_snapshot(
        self,
        date: str,
        tickers: List[TickerData]
    ):
        """Store today's ticker data to database."""
        # Get existing first_seen dates
        existing = await self.db.fetch_all(
            "SELECT symbol, first_seen_date FROM daily_tickers "
            "WHERE first_seen_date IS NOT NULL GROUP BY symbol"
        )
        first_seen_map = {r['symbol']: r['first_seen_date'] for r in existing}

        # Insert/update today's data
        for ticker in tickers:
            first_seen = first_seen_map.get(ticker.symbol, date)

            await self.db.execute("""
                INSERT INTO daily_tickers
                (date, symbol, last_price, quote_volume_24h, price_change_pct_24h,
                 trade_count_24h, first_seen_date, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))
                ON CONFLICT(date, symbol) DO UPDATE SET
                    last_price = excluded.last_price,
                    quote_volume_24h = excluded.quote_volume_24h,
                    price_change_pct_24h = excluded.price_change_pct_24h,
                    trade_count_24h = excluded.trade_count_24h,
                    updated_at = datetime('now')
            """, (
                date, ticker.symbol, ticker.last_price,
                ticker.quote_volume, ticker.price_change_pct,
                ticker.trade_count, first_seen
            ))

        logger.info(f"Stored {len(tickers)} ticker snapshots for {date}")

    async def _cleanup_old_data(self):
        """Remove data older than RETENTION_DAYS."""
        cutoff = (
            datetime.now(timezone.utc) - timedelta(days=self.RETENTION_DAYS)
        ).strftime("%Y-%m-%d")

        await self.db.execute(
            "DELETE FROM daily_tickers WHERE date < ?", (cutoff,)
        )

    async def _calculate_momentum(
        self,
        today: str,
        tickers: List[TickerData]
    ) -> List[CoinCandidate]:
        """
        Calculate momentum for each ticker with bootstrapping support.

        Bootstrapping mode (adapts to available history):
        - 10+ days history: Use 10-day momentum, threshold 30%
        - 3-9 days history: Use 3-day momentum, threshold 15%
        - 0-2 days history: Use 24h change, threshold 10%
        """
        # Determine how many days of history we have
        history_stats = await self.db.fetch_one("""
            SELECT COUNT(DISTINCT date) as days, MIN(date) as oldest
            FROM daily_tickers
        """)
        history_days = history_stats['days'] if history_stats else 0
        oldest_date = history_stats['oldest'] if history_stats else today

        # Determine lookback period and threshold based on available history
        if history_days >= 10:
            lookback_days = 10
            momentum_threshold = self.MIN_MOMENTUM_PCT  # 30%
            mode = "10d"
        elif history_days >= 3:
            lookback_days = 3
            momentum_threshold = 15.0  # Lower threshold for 3-day
            mode = "3d"
        else:
            lookback_days = 0  # Use 24h change
            momentum_threshold = 10.0  # Much lower for 24h
            mode = "24h"

        logger.info(f"Momentum mode: {mode} (history: {history_days} days, threshold: {momentum_threshold}%)")

        # Get historical prices if we have enough history
        price_history = {}
        if lookback_days > 0:
            lookback_date = (
                datetime.now(timezone.utc) - timedelta(days=lookback_days)
            ).strftime("%Y-%m-%d")

            historical = await self.db.fetch_all("""
                SELECT symbol, last_price
                FROM daily_tickers
                WHERE date = ?
            """, (lookback_date,))
            price_history = {r['symbol']: r['last_price'] for r in historical}

        # Get first_seen dates for all symbols
        all_first_seen = await self.db.fetch_all("""
            SELECT symbol, MIN(first_seen_date) as first_seen_date
            FROM daily_tickers
            GROUP BY symbol
        """)
        first_seen_dates = {r['symbol']: r['first_seen_date'] for r in all_first_seen}

        candidates = []
        today_dt = datetime.strptime(today, "%Y-%m-%d")

        volume_filtered = 0
        momentum_filtered = 0

        for ticker in tickers:
            # Volume window filter (target low-mid volume "pumpy" coins)
            if ticker.quote_volume < self.MIN_VOLUME_USDT:
                volume_filtered += 1
                continue  # Too dead, avoid
            if ticker.quote_volume > self.MAX_VOLUME_USDT:
                volume_filtered += 1
                continue  # Too stable/efficient, skip

            # Extreme spike filter
            if abs(ticker.price_change_pct) > self.MAX_SPIKE_PCT:
                continue

            # Calculate momentum based on available history
            if lookback_days > 0:
                old_price = price_history.get(ticker.symbol)
                if old_price and old_price > 0:
                    momentum = ((ticker.last_price / old_price) - 1) * 100
                else:
                    # No history for this symbol, use 24h as fallback
                    momentum = ticker.price_change_pct
            else:
                # Bootstrapping: use 24h change directly
                momentum = ticker.price_change_pct

            # Momentum filter (using mode-appropriate threshold)
            if momentum < momentum_threshold:
                momentum_filtered += 1
                continue

            # Check if new listing
            first_seen = first_seen_dates.get(ticker.symbol, today)
            try:
                first_seen_dt = datetime.strptime(first_seen, "%Y-%m-%d")
                days_since = (today_dt - first_seen_dt).days
            except:
                days_since = 0

            is_new = days_since <= self.NEW_LISTING_DAYS

            candidates.append(CoinCandidate(
                symbol=ticker.symbol,
                momentum_10d=momentum,
                volume_24h=ticker.quote_volume,
                price=ticker.last_price,
                is_new_listing=is_new,
                days_since_listing=days_since,
                score=0  # Will be calculated in ranking
            ))

        logger.info(f"Filters: {volume_filtered} by volume, {momentum_filtered} by momentum (<{momentum_threshold}%)")

        return candidates

    async def _check_market_quality(
        self,
        candidates: List[CoinCandidate]
    ) -> List[CoinCandidate]:
        """
        Check spread and depth for top candidates.
        This requires individual API calls, so only check top N by momentum.
        """
        if not candidates:
            return []

        # Pre-sort by momentum to check only the most promising
        candidates.sort(key=lambda x: x.momentum_10d, reverse=True)
        to_check = candidates[:self.CHECK_DEPTH_TOP_N]

        logger.info(f"Checking spread/depth for top {len(to_check)} candidates")

        passed = []
        for c in to_check:
            try:
                spread, bid_depth, ask_depth = await self._fetch_order_book(c.symbol)

                c.spread_pct = spread
                c.bid_depth = bid_depth
                c.ask_depth = ask_depth

                # Check market quality
                if spread > self.MAX_SPREAD_PCT:
                    c.market_quality_ok = False
                    logger.debug(f"{c.symbol}: spread {spread:.2f}% > max {self.MAX_SPREAD_PCT}%")
                    continue

                if bid_depth < self.MIN_DEPTH_USDT or ask_depth < self.MIN_DEPTH_USDT:
                    c.market_quality_ok = False
                    logger.debug(
                        f"{c.symbol}: depth ${bid_depth:.0f}/${ask_depth:.0f} "
                        f"< min ${self.MIN_DEPTH_USDT}"
                    )
                    continue

                c.market_quality_ok = True
                passed.append(c)

                # Small delay to avoid rate limits
                await asyncio.sleep(0.1)

            except Exception as e:
                logger.warning(f"Failed to check {c.symbol}: {e}")
                # Include anyway if we can't check (fail open)
                c.market_quality_ok = True
                passed.append(c)

        logger.info(f"{len(passed)}/{len(to_check)} passed market quality filter")
        return passed

    async def _fetch_order_book(self, symbol: str) -> tuple[float, float, float]:
        """
        Fetch order book and calculate spread + depth.
        Returns: (spread_pct, bid_depth_usdt, ask_depth_usdt)
        """
        if not self._session:
            await self.initialize()

        url = f"{self.MEXC_DEPTH_URL}?symbol={symbol}&limit=20"

        async with self._session.get(url) as resp:
            if resp.status != 200:
                raise Exception(f"API error: {resp.status}")

            data = await resp.json()

            bids = data.get('bids', [])
            asks = data.get('asks', [])

            if not bids or not asks:
                return 999.0, 0.0, 0.0  # No liquidity

            # Best bid/ask prices
            best_bid = float(bids[0][0])
            best_ask = float(asks[0][0])

            # Calculate spread
            mid_price = (best_bid + best_ask) / 2
            spread_pct = ((best_ask - best_bid) / mid_price) * 100 if mid_price > 0 else 999.0

            # Calculate depth (sum of top 5 levels in USDT)
            bid_depth = sum(float(b[0]) * float(b[1]) for b in bids[:5])
            ask_depth = sum(float(a[0]) * float(a[1]) for a in asks[:5])

            return spread_pct, bid_depth, ask_depth

    async def _rank_candidates(
        self,
        candidates: List[CoinCandidate]
    ) -> List[CoinCandidate]:
        """
        Rank candidates by score and return top N.

        For "pumpy" low-volume coins:
        Score = momentum_10d + new_listing_bonus + spread_bonus (lower spread = better)
        No bonus for high volume (we want low-mid volume coins)
        """
        # First, check market quality for top candidates
        candidates = await self._check_market_quality(candidates)

        for c in candidates:
            # Base score is momentum
            score = c.momentum_10d

            # New listing bonus
            if c.is_new_listing:
                score += self.NEW_LISTING_BONUS

            # Spread bonus: lower spread = higher bonus (max +15)
            # 0.1% spread = +15, 0.6% spread = 0
            if c.spread_pct > 0:
                spread_bonus = max(0, (self.MAX_SPREAD_PCT - c.spread_pct) * 25)
                score += spread_bonus

            # Volume "sweet spot" bonus: prefer 50k-100k range
            # This is the goldilocks zone for pumpy coins
            if 50_000 <= c.volume_24h <= 100_000:
                score += 5  # Small bonus for ideal volume range

            c.score = score

        # Sort by score descending
        candidates.sort(key=lambda x: x.score, reverse=True)

        # Return top N
        return candidates[:self.TOP_N_COINS]

    async def _get_current_watchlist(self) -> List[str]:
        """Get current watchlist from database."""
        rows = await self.db.fetch_all(
            "SELECT symbol FROM scanner_watchlist ORDER BY score DESC"
        )
        return [r['symbol'] for r in rows]

    async def _update_watchlist(self, candidates: List[CoinCandidate]):
        """Update watchlist in database."""
        # Clear old watchlist
        await self.db.execute("DELETE FROM scanner_watchlist")

        # Insert new candidates
        for c in candidates:
            await self.db.execute("""
                INSERT INTO scanner_watchlist
                (symbol, momentum_10d, volume_24h, score, is_new_listing,
                 days_since_listing, added_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'))
            """, (
                c.symbol, c.momentum_10d, c.volume_24h, c.score,
                1 if c.is_new_listing else 0, c.days_since_listing
            ))

        logger.info(f"Updated watchlist with {len(candidates)} coins")

    async def _log_scan_run(
        self,
        date: str,
        result: Dict,
        old_watchlist: List[str]
    ):
        """Log scan run to database."""
        await self.db.execute("""
            INSERT INTO scanner_runs
            (run_date, total_symbols, excluded_count, filtered_count,
             candidates_count, watchlist_updated, old_watchlist, new_watchlist,
             duration_sec, error, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
        """, (
            date, result['total_symbols'], result['excluded'],
            result['filtered'], result['candidates'],
            1 if result['watchlist'] != old_watchlist else 0,
            json.dumps(old_watchlist),
            json.dumps(result['watchlist']),
            result['duration_sec'],
            result['error']
        ))

    async def get_watchlist(self) -> List[str]:
        """Get current watchlist symbols for ScalperBot."""
        if not self.db:
            self.db = await get_database()

        rows = await self.db.fetch_all(
            "SELECT symbol FROM scanner_watchlist ORDER BY score DESC"
        )

        if rows:
            return [r['symbol'] for r in rows]

        # Fallback to config if no scanner watchlist
        return settings.watchlist_symbols

    async def add_to_blacklist(self, symbol: str, reason: str = "manual"):
        """Add symbol to blacklist."""
        await self.db.execute("""
            INSERT OR REPLACE INTO scanner_blacklist (symbol, reason, added_at)
            VALUES (?, ?, datetime('now'))
        """, (symbol, reason))

        # Also add to in-memory set
        EXCLUDED_SCAMS.add(symbol)
        logger.info(f"Added {symbol} to blacklist: {reason}")


# Singleton instance
_scanner: Optional[CoinScanner] = None


async def get_scanner() -> CoinScanner:
    """Get or create scanner instance."""
    global _scanner
    if _scanner is None:
        _scanner = CoinScanner()
        await _scanner.initialize()
    return _scanner
