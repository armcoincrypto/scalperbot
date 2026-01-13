"""
Growth 2H Scanner - Detects >=20% growth within any 2-hour window.

Scans last 10 days of 5-minute klines for each symbol and finds
ALL 2-hour growth windows that exceed the threshold.

Features:
- Recency filter: Only include spikes from last N days
- Frequency detection: Count spikes per symbol
- Sanity filter: Cap maximum growth to filter manipulation
- Watchlist integration: Auto-update trading watchlist

Usage:
    python -m scalperbot.tools.scan_growth2h --db scalperbot.db --update-watchlist
"""

from __future__ import annotations

import asyncio
import math
import time
from dataclasses import dataclass, field
from typing import Any, Optional, List

import aiohttp


@dataclass
class SpikeEvent:
    """A single detected spike event."""
    symbol: str
    growth_pct: float
    window_start_ms: int
    window_end_ms: int


@dataclass
class SymbolAnalysis:
    """Complete analysis for a single symbol."""
    symbol: str
    quote_volume_24h: float
    best_growth_pct: float
    best_window_start_ms: int
    best_window_end_ms: int
    spike_count: int  # Number of spikes >= threshold in lookback period
    recent_spike_count: int  # Spikes in recency window
    last_spike_ms: int  # Most recent spike timestamp
    all_spikes: List[SpikeEvent] = field(default_factory=list)
    score: float = 0.0  # Calculated watchlist score


class MexcPublicClient:
    """
    MEXC Spot public REST client.
    Uses Binance-style endpoints on MEXC.
    """

    def __init__(self, base_url: str = "https://api.mexc.com", timeout_sec: int = 20):
        self.base_url = base_url.rstrip("/")
        self.timeout = aiohttp.ClientTimeout(total=timeout_sec)

    async def _get(
        self,
        session: aiohttp.ClientSession,
        path: str,
        params: dict[str, Any] | None = None
    ) -> Any:
        url = f"{self.base_url}{path}"
        async with session.get(url, params=params) as resp:
            text = await resp.text()
            if resp.status != 200:
                raise RuntimeError(f"HTTP {resp.status} GET {path} -> {text[:400]}")
            return await resp.json()

    async def exchange_info(self, session: aiohttp.ClientSession) -> dict[str, Any]:
        return await self._get(session, "/api/v3/exchangeInfo")

    async def ticker_24hr(self, session: aiohttp.ClientSession) -> list[dict[str, Any]]:
        data = await self._get(session, "/api/v3/ticker/24hr")
        if not isinstance(data, list):
            raise RuntimeError("Unexpected ticker/24hr response")
        return data

    async def klines(
        self,
        session: aiohttp.ClientSession,
        symbol: str,
        interval: str,
        start_time_ms: int,
        end_time_ms: int,
        limit: int = 1000,
    ) -> list[list[Any]]:
        return await self._get(
            session,
            "/api/v3/klines",
            params={
                "symbol": symbol,
                "interval": interval,
                "startTime": start_time_ms,
                "endTime": end_time_ms,
                "limit": limit,
            },
        )


class Growth2HScanner:
    """
    Scans last N days and finds ALL 2h windows with >= threshold growth.
    Tracks frequency and recency of spikes per symbol.
    """

    def __init__(
        self,
        client: MexcPublicClient,
        *,
        lookback_days: int = 10,
        interval: str = "5m",
        window_minutes: int = 120,
        threshold_pct: float = 20.0,
        max_growth_pct: float = 300.0,  # Sanity cap
        max_quote_volume_24h: float = 200_000.0,
        min_quote_volume_24h: float = 3_000.0,
        recency_days: int = 3,  # Only count recent spikes
        min_spikes: int = 2,  # Require >= N spikes for watchlist
        concurrency: int = 8,
        request_delay_ms: int = 80,
    ):
        self.client = client
        self.lookback_days = lookback_days
        self.interval = interval
        self.window_minutes = window_minutes
        self.threshold_pct = threshold_pct
        self.max_growth_pct = max_growth_pct
        self.max_quote_volume_24h = max_quote_volume_24h
        self.min_quote_volume_24h = min_quote_volume_24h
        self.recency_days = recency_days
        self.min_spikes = min_spikes
        self.concurrency = max(1, concurrency)
        self.request_delay_ms = max(0, request_delay_ms)

        if interval != "5m":
            raise ValueError("This implementation expects interval='5m'")

        self._bars_per_window = window_minutes // 5  # 120min / 5m = 24 bars
        if self._bars_per_window < 2:
            raise ValueError("window too small")

        self._sem = asyncio.Semaphore(self.concurrency)

    @staticmethod
    def _now_ms() -> int:
        return int(time.time() * 1000)

    @staticmethod
    def _ms_days(days: int) -> int:
        return days * 24 * 60 * 60 * 1000

    async def _sleep_throttle(self):
        if self.request_delay_ms > 0:
            await asyncio.sleep(self.request_delay_ms / 1000)

    async def list_usdt_symbols(self, session: aiohttp.ClientSession) -> list[str]:
        info = await self.client.exchange_info(session)
        symbols = []
        all_symbols = info.get("symbols", [])
        print(f"[info] exchangeInfo: {len(all_symbols)} total symbols")

        for s in all_symbols:
            sym = s.get("symbol")
            status = s.get("status") or s.get("isSpotTradingAllowed")
            quote = s.get("quoteAsset")

            if not sym:
                continue
            if status not in ("ENABLED", "1", True, 1):
                continue
            if quote != "USDT":
                continue
            # Filter non-ASCII symbols (can cause issues)
            if not sym.replace("USDT", "").isalnum():
                continue
            symbols.append(sym)

        print(f"[info] Found {len(symbols)} valid USDT symbols")
        return symbols

    async def build_volume_map(self, session: aiohttp.ClientSession) -> dict[str, float]:
        tickers = await self.client.ticker_24hr(session)
        out: dict[str, float] = {}
        for t in tickers:
            sym = t.get("symbol")
            qv = t.get("quoteVolume")
            if not sym or qv is None:
                continue
            try:
                out[sym] = float(qv)
            except Exception:
                continue
        print(f"[info] Volume data for {len(out)} symbols")
        return out

    async def fetch_klines_last_days(
        self,
        session: aiohttp.ClientSession,
        symbol: str
    ) -> list[list[Any]]:
        """Fetch 5m candles for last lookback_days with pagination."""
        end_ms = self._now_ms()
        start_ms = end_ms - self._ms_days(self.lookback_days)

        all_rows: list[list[Any]] = []
        cursor = start_ms
        step_ms = 1000 * 5 * 60 * 1000  # ~3.47 days per request

        while cursor < end_ms:
            batch_end = min(end_ms, cursor + step_ms)
            rows = await self.client.klines(
                session=session,
                symbol=symbol,
                interval=self.interval,
                start_time_ms=cursor,
                end_time_ms=batch_end,
                limit=1000,
            )
            if not isinstance(rows, list):
                raise RuntimeError("klines response not list")

            if rows:
                all_rows.extend(rows)

            cursor = batch_end + 1
            await self._sleep_throttle()

        # Sort and de-dup by openTime
        all_rows.sort(key=lambda r: int(r[0]))
        dedup: list[list[Any]] = []
        last_t = None
        for r in all_rows:
            t0 = int(r[0])
            if last_t is None or t0 != last_t:
                dedup.append(r)
                last_t = t0
        return dedup

    def detect_all_spikes(
        self,
        symbol: str,
        rows: list[list[Any]],
        quote_volume_24h: float
    ) -> Optional[SymbolAnalysis]:
        """
        Detect ALL 2h growth windows >= threshold.
        Returns full analysis including spike count and recency.
        """
        n = len(rows)
        if n < self._bars_per_window + 1:
            return None

        now_ms = self._now_ms()
        recency_cutoff_ms = now_ms - self._ms_days(self.recency_days)

        # Pre-extract arrays for speed
        opens = [float(r[1]) for r in rows]
        highs = [float(r[2]) for r in rows]
        times = [int(r[0]) for r in rows]

        w = self._bars_per_window
        spikes: List[SpikeEvent] = []
        best_growth = 0.0
        best_start = 0
        best_end = 0

        # Sliding window detection
        for i in range(0, n - w):
            start_price = opens[i]
            if start_price <= 0:
                continue

            window_high = max(highs[i : i + w])
            growth = (window_high - start_price) / start_price * 100

            if growth >= self.threshold_pct:
                # Record this spike
                spike = SpikeEvent(
                    symbol=symbol,
                    growth_pct=growth,
                    window_start_ms=times[i],
                    window_end_ms=times[i + w - 1]
                )
                spikes.append(spike)

                if growth > best_growth:
                    best_growth = growth
                    best_start = times[i]
                    best_end = times[i + w - 1]

        if not spikes:
            return None

        # De-duplicate overlapping spikes (keep best per 2h period)
        deduped_spikes = self._dedupe_spikes(spikes)

        # Count recent spikes
        recent_count = sum(
            1 for s in deduped_spikes
            if s.window_start_ms >= recency_cutoff_ms
        )

        # Find last spike time
        last_spike_ms = max(s.window_start_ms for s in deduped_spikes)

        return SymbolAnalysis(
            symbol=symbol,
            quote_volume_24h=quote_volume_24h,
            best_growth_pct=best_growth,
            best_window_start_ms=best_start,
            best_window_end_ms=best_end,
            spike_count=len(deduped_spikes),
            recent_spike_count=recent_count,
            last_spike_ms=last_spike_ms,
            all_spikes=deduped_spikes
        )

    def _dedupe_spikes(self, spikes: List[SpikeEvent]) -> List[SpikeEvent]:
        """Remove overlapping spikes, keeping the best one per 2h period."""
        if not spikes:
            return []

        # Sort by time
        spikes.sort(key=lambda s: s.window_start_ms)

        deduped = []
        last_end = 0

        for spike in spikes:
            # If this spike starts after the last one ended, it's a new spike
            if spike.window_start_ms > last_end:
                deduped.append(spike)
                last_end = spike.window_end_ms
            # If overlapping, keep the better one
            elif spike.growth_pct > deduped[-1].growth_pct:
                deduped[-1] = spike
                last_end = spike.window_end_ms

        return deduped

    def calculate_score(self, analysis: SymbolAnalysis) -> float:
        """
        Calculate watchlist score for ranking.
        Score = best_growth * log(1 + spike_count) * recency_bonus
        """
        # Base: best growth (capped at max_growth_pct for scoring)
        capped_growth = min(analysis.best_growth_pct, self.max_growth_pct)

        # Frequency bonus: more spikes = more reliable pattern
        freq_multiplier = math.log1p(analysis.spike_count)

        # Recency bonus: recent spikes are more valuable
        recency_multiplier = 1.0 + (analysis.recent_spike_count * 0.5)

        # Volume bonus: prefer mid-range volume (sweet spot)
        vol = analysis.quote_volume_24h
        if 50_000 <= vol <= 150_000:
            vol_multiplier = 1.2
        elif 20_000 <= vol <= 200_000:
            vol_multiplier = 1.0
        else:
            vol_multiplier = 0.8

        score = capped_growth * freq_multiplier * recency_multiplier * vol_multiplier
        return score

    async def scan_symbol(
        self,
        session: aiohttp.ClientSession,
        symbol: str,
        quote_volume_24h: float
    ) -> Optional[SymbolAnalysis]:
        async with self._sem:
            rows = await self.fetch_klines_last_days(session, symbol)
            analysis = self.detect_all_spikes(symbol, rows, quote_volume_24h)
            if analysis:
                analysis.score = self.calculate_score(analysis)
            return analysis

    async def scan_all(
        self,
        progress_callback: Optional[callable] = None
    ) -> list[SymbolAnalysis]:
        """
        Scan all qualifying symbols and return full analysis.
        """
        async with aiohttp.ClientSession(timeout=self.client.timeout) as session:
            symbols = await self.list_usdt_symbols(session)
            volume_map = await self.build_volume_map(session)

            # Filter by volume
            candidates: list[tuple[str, float]] = []
            stats = {"no_vol": 0, "too_low": 0, "too_high": 0}

            for sym in symbols:
                qv = volume_map.get(sym)
                if qv is None:
                    stats["no_vol"] += 1
                    continue
                if qv < self.min_quote_volume_24h:
                    stats["too_low"] += 1
                    continue
                if qv >= self.max_quote_volume_24h:
                    stats["too_high"] += 1
                    continue
                candidates.append((sym, qv))

            print(f"[info] Volume filter: {len(candidates)} passed | "
                  f"{stats['too_low']} too low | {stats['too_high']} too high")

            results: list[SymbolAnalysis] = []
            started = time.time()
            total = len(candidates)
            done = 0

            async def worker(sym: str, qv: float):
                nonlocal done
                try:
                    analysis = await self.scan_symbol(session, sym, qv)
                    if analysis:
                        results.append(analysis)
                except Exception:
                    pass
                finally:
                    done += 1
                    if done % 50 == 0 or done == total:
                        elapsed = time.time() - started
                        rate = done / elapsed if elapsed > 0 else 0
                        eta = (total - done) / rate if rate > 0 else math.inf
                        if progress_callback:
                            progress_callback(done, total, rate, eta / 60)
                        else:
                            print(f"[scan] {done}/{total} | {rate:.1f}/s | ETA {eta/60:.1f}m")

            await asyncio.gather(*(worker(sym, qv) for sym, qv in candidates))

            # Sort by score
            results.sort(key=lambda x: x.score, reverse=True)
            return results

    def filter_for_watchlist(
        self,
        results: list[SymbolAnalysis]
    ) -> list[SymbolAnalysis]:
        """
        Apply final filters for watchlist inclusion:
        - Sanity cap on growth
        - Minimum spike frequency
        - Recency requirement
        """
        now_ms = self._now_ms()
        recency_cutoff_ms = now_ms - self._ms_days(self.recency_days)

        qualified = []
        for r in results:
            # Sanity filter: skip extreme manipulation
            if r.best_growth_pct > self.max_growth_pct:
                continue

            # Frequency filter: require multiple spikes
            if r.spike_count < self.min_spikes:
                continue

            # Recency filter: must have recent spike
            if r.last_spike_ms < recency_cutoff_ms:
                continue

            qualified.append(r)

        return qualified
