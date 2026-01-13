"""
Growth 2H Scanner - Detects >=20% growth within any 2-hour window.

Scans last 10 days of 5-minute klines for each symbol and finds
the best 2-hour growth window. Filters by 24h volume first.

Usage:
    python -m scalperbot.tools.scan_growth2h --db scalperbot.db --top 20
"""

from __future__ import annotations

import asyncio
import math
import time
from dataclasses import dataclass
from typing import Any, Optional

import aiohttp


@dataclass(frozen=True)
class GrowthEvent:
    """Represents a detected growth event."""
    symbol: str
    quote_volume_24h: float
    best_growth_pct: float
    window_start_ms: int
    window_end_ms: int


class MexcPublicClient:
    """
    MEXC Spot public REST client.
    Uses Binance-style endpoints on MEXC:
      /api/v3/exchangeInfo
      /api/v3/ticker/24hr
      /api/v3/klines
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
    Scans last N days and finds ANY 2h window with >= threshold growth.
    Uses 5m candles by default:
      10 days of 5m candles = 2880 bars (fits in 3 requests if limit=1000).
    """

    def __init__(
        self,
        client: MexcPublicClient,
        *,
        lookback_days: int = 10,
        interval: str = "5m",
        window_minutes: int = 120,
        threshold_pct: float = 20.0,
        max_quote_volume_24h: float = 200_000.0,
        min_quote_volume_24h: float = 3_000.0,
        concurrency: int = 8,
        request_delay_ms: int = 80,
    ):
        self.client = client
        self.lookback_days = lookback_days
        self.interval = interval
        self.window_minutes = window_minutes
        self.threshold_pct = threshold_pct
        self.max_quote_volume_24h = max_quote_volume_24h
        self.min_quote_volume_24h = min_quote_volume_24h
        self.concurrency = max(1, concurrency)
        self.request_delay_ms = max(0, request_delay_ms)

        if interval != "5m":
            raise ValueError("This implementation expects interval='5m' (adjust math if you change).")

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
        for s in info.get("symbols", []):
            sym = s.get("symbol")
            status = s.get("status")
            quote = s.get("quoteAsset")
            if not sym or status != "ENABLED":
                continue
            if quote != "USDT":
                continue
            symbols.append(sym)
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
        return out

    async def fetch_klines_last_days(
        self,
        session: aiohttp.ClientSession,
        symbol: str
    ) -> list[list[Any]]:
        """
        Fetch 5m candles for last lookback_days.
        Uses pagination by startTime/endTime.
        """
        end_ms = self._now_ms()
        start_ms = end_ms - self._ms_days(self.lookback_days)

        all_rows: list[list[Any]] = []
        cursor = start_ms
        # MEXC limit=1000, each 5m bar = 300,000 ms
        # 1000 bars = 5000 minutes = ~3.47 days
        step_ms = 1000 * 5 * 60 * 1000

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

    def detect_best_2h_growth(
        self,
        symbol: str,
        rows: list[list[Any]],
        quote_volume_24h: float
    ) -> Optional[GrowthEvent]:
        """
        Define "2h growth" as:
          start_price = open of first bar in window
          best_price = max(high) inside window
          growth = (best_price - start_price) / start_price
        """
        n = len(rows)
        if n < self._bars_per_window + 1:
            return None

        best_growth = 0.0
        best_start = 0
        best_end = 0

        # Pre-extract arrays for speed
        opens = [float(r[1]) for r in rows]
        highs = [float(r[2]) for r in rows]
        times = [int(r[0]) for r in rows]

        w = self._bars_per_window
        # Naive sliding: O(n*w) is fine for 2880*24 ~ 69k ops per symbol
        for i in range(0, n - w):
            start_price = opens[i]
            if start_price <= 0:
                continue
            window_high = max(highs[i : i + w])
            growth = (window_high - start_price) / start_price
            if growth > best_growth:
                best_growth = growth
                best_start = times[i]
                best_end = times[i + w - 1]

        if best_growth * 100.0 >= self.threshold_pct:
            return GrowthEvent(
                symbol=symbol,
                quote_volume_24h=quote_volume_24h,
                best_growth_pct=best_growth * 100.0,
                window_start_ms=best_start,
                window_end_ms=best_end,
            )
        return None

    async def scan_symbol(
        self,
        session: aiohttp.ClientSession,
        symbol: str,
        quote_volume_24h: float
    ) -> Optional[GrowthEvent]:
        async with self._sem:
            rows = await self.fetch_klines_last_days(session, symbol)
            return self.detect_best_2h_growth(symbol, rows, quote_volume_24h)

    async def scan_all(
        self,
        progress_callback: Optional[callable] = None
    ) -> list[GrowthEvent]:
        """
        Scan all qualifying symbols and return growth events.

        Args:
            progress_callback: Optional callback(done, total, rate, eta_min)
        """
        async with aiohttp.ClientSession(timeout=self.client.timeout) as session:
            symbols = await self.list_usdt_symbols(session)
            volume_map = await self.build_volume_map(session)

            # Filter by volume first (key requirement)
            candidates: list[tuple[str, float]] = []
            for sym in symbols:
                qv = volume_map.get(sym)
                if qv is None:
                    continue
                if qv < self.min_quote_volume_24h:
                    continue
                if qv >= self.max_quote_volume_24h:
                    continue
                candidates.append((sym, qv))

            results: list[GrowthEvent] = []
            started = time.time()

            total = len(candidates)
            done = 0

            async def worker(sym: str, qv: float):
                nonlocal done
                try:
                    ev = await self.scan_symbol(session, sym, qv)
                    if ev:
                        results.append(ev)
                except Exception:
                    # Swallow per-symbol errors
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
                            print(f"[scan] {done}/{total} done | {rate:.2f} sym/s | ETA ~ {eta/60:.1f} min")

            await asyncio.gather(*(worker(sym, qv) for sym, qv in candidates))

            # Rank by best growth
            results.sort(key=lambda x: x.best_growth_pct, reverse=True)
            return results
