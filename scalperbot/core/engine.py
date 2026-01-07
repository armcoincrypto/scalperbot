"""
Main trading engine - orchestrates all components.
"""

import asyncio
from datetime import datetime, timezone
from typing import Dict, List, Optional

from scalperbot.config import settings
from scalperbot.log import get_logger
from scalperbot.mexc import MEXCClient
from scalperbot.mexc.models import BookTicker, Ticker24h
from scalperbot.storage import Database, PositionRepo, TradeRepo, SignalRepo
from scalperbot.storage.repo import (
    CooldownRepo, TickRepo, OutcomeRepo, StrategyVersionRepo, SimulatedTradeRepo
)
from scalperbot.core.indicators import Indicators, IndicatorResult
from scalperbot.core.scoring import SignalScorer, SignalScore
from scalperbot.core.filters import SafetyFilters, FilterResult
from scalperbot.core.risk import RiskManager, RiskParameters
from scalperbot.core.selector import CandidateSelector, TradeCandidate
from scalperbot.core.simulator import TradeSimulator, get_simulator

logger = get_logger(__name__)


class TradingEngine:
    """
    Main trading engine.

    Responsibilities:
    - Poll market data
    - Calculate signals
    - Select best candidates
    - Execute trades
    - Manage positions
    """

    def __init__(self):
        self.client = MEXCClient(dry_run=settings.dry_run)
        self.db: Optional[Database] = None
        self.positions: Optional[PositionRepo] = None
        self.trades: Optional[TradeRepo] = None
        self.signals: Optional[SignalRepo] = None
        self.cooldowns: Optional[CooldownRepo] = None

        # Research/analytics repos
        self.ticks: Optional[TickRepo] = None
        self.outcomes: Optional[OutcomeRepo] = None
        self.strategy_versions: Optional[StrategyVersionRepo] = None
        self.simulated_trades: Optional[SimulatedTradeRepo] = None
        self.current_strategy_version_id: Optional[int] = None

        # Trade simulator for realistic DRY_RUN
        self.simulator = get_simulator()

        self.indicators = Indicators()
        self.scorer = SignalScorer()
        self.filters = SafetyFilters()
        self.risk = RiskManager()
        self.selector = CandidateSelector()

        self.running = False
        self.watchlist: List[str] = []
        self.start_time: Optional[datetime] = None

        # Callbacks for Telegram notifications
        self.on_trade_open = None
        self.on_trade_close = None
        self.on_signal = None

    async def initialize(self):
        """Initialize database and connections."""
        from scalperbot.storage.db import get_database

        self.db = await get_database()
        self.positions = PositionRepo(self.db)
        self.trades = TradeRepo(self.db)
        self.signals = SignalRepo(self.db)
        self.cooldowns = CooldownRepo(self.db)

        # Research/analytics repos
        self.ticks = TickRepo(self.db)
        self.outcomes = OutcomeRepo(self.db)
        self.strategy_versions = StrategyVersionRepo(self.db)
        self.simulated_trades = SimulatedTradeRepo(self.db)

        # Initialize or get strategy version
        await self._init_strategy_version()

        self.watchlist = settings.watchlist_symbols

        # Test API connectivity
        if await self.client.ping():
            logger.info("MEXC API connected")
        else:
            logger.error("Failed to connect to MEXC API")

        logger.info(f"Engine initialized with {len(self.watchlist)} symbols")
        logger.info(f"Strategy version: {self.current_strategy_version_id}")

    async def _init_strategy_version(self):
        """Initialize or get current strategy version for tracking."""
        # Check if there's an active version with same parameters
        active = await self.strategy_versions.get_active()

        current_params = {
            "buy_pct_trigger": settings.buy_pct_trigger,
            "buy_score_min": settings.buy_score_min,
            "base_sl_pct": settings.base_sl_pct,
            "take_profit_pct": settings.take_profit_pct,
            "max_spread_pct": settings.max_spread_pct,
            "position_size_usdt": settings.position_size_usdt,
            "poll_interval_sec": settings.poll_interval_sec,
            "use_limit_orders": settings.use_limit_orders,
        }

        # Create new version if none exists or params changed
        if not active:
            self.current_strategy_version_id = await self.strategy_versions.create(
                name=f"v1.0-{datetime.now(timezone.utc).strftime('%Y%m%d')}",
                parameters=current_params,
                description="Initial strategy version"
            )
            logger.info(f"Created new strategy version: {self.current_strategy_version_id}")
        else:
            # Check if key params match
            params_match = (
                active.get("buy_pct_trigger") == current_params["buy_pct_trigger"] and
                active.get("buy_score_min") == current_params["buy_score_min"] and
                active.get("base_sl_pct") == current_params["base_sl_pct"]
            )
            if params_match:
                self.current_strategy_version_id = active["id"]
            else:
                # Create new version with changed params
                self.current_strategy_version_id = await self.strategy_versions.create(
                    name=f"v1.1-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M')}",
                    parameters=current_params,
                    description="Parameters changed"
                )
                logger.info(f"Created new strategy version (params changed): {self.current_strategy_version_id}")

    async def start(self):
        """Start the trading loop."""
        if self.running:
            logger.warning("Engine already running")
            return

        await self.initialize()
        self.running = True
        self.start_time = datetime.now(timezone.utc)

        logger.info("=" * 60)
        logger.info("TRADING ENGINE STARTED")
        logger.info(f"Mode: {'DRY_RUN' if settings.dry_run else 'LIVE'}")
        logger.info(f"Watchlist: {self.watchlist}")
        logger.info(f"Position size: ${settings.position_size_usdt}")
        logger.info(f"Poll interval: {settings.poll_interval_sec}s")
        logger.info("=" * 60)

        while self.running:
            try:
                await self._run_cycle()
            except Exception as e:
                logger.error(f"Cycle error: {e}", exc_info=True)

            await asyncio.sleep(settings.poll_interval_sec)

    async def stop(self):
        """Stop the trading loop."""
        self.running = False
        await self.client.close()
        logger.info("Trading engine stopped")

    def set_watchlist(self, symbols: List[str]):
        """Update watchlist."""
        self.watchlist = [s.upper() for s in symbols]
        logger.info(f"Watchlist updated: {self.watchlist}")

    async def _run_cycle(self):
        """Run one trading cycle."""
        logger.debug(f"Running cycle for {len(self.watchlist)} symbols")

        # Clean up expired cooldowns
        await self.cooldowns.clear_expired()
        await self.signals.clear_expired()

        # Get open positions count
        open_positions = await self.positions.get_open()
        open_count = len(open_positions)

        # Check existing positions for exit
        for pos in open_positions:
            await self._check_position_exit(pos)

        # Skip new entries if max positions reached
        if open_count >= settings.max_open_positions:
            logger.debug(f"Max positions ({settings.max_open_positions}) reached")
            return

        # Analyze each symbol and collect candidates
        candidates = []
        for symbol in self.watchlist:
            candidate = await self._analyze_symbol(symbol, open_count)
            if candidate:
                candidates.append(candidate)

        # Select best candidate
        slots_available = settings.max_open_positions - open_count
        selected = self.selector.select_best(candidates, slots_available)

        # Execute selected trades
        for candidate in selected:
            await self._execute_entry(candidate)

    async def _analyze_symbol(
        self,
        symbol: str,
        open_count: int
    ) -> Optional[TradeCandidate]:
        """Analyze single symbol for trading opportunity."""
        # Check if should skip
        has_position = await self.positions.has_open_position(symbol)
        in_cooldown = await self.cooldowns.is_in_cooldown(symbol)

        skip, skip_reason = self.selector.should_skip(
            symbol, has_position, in_cooldown,
            open_count, settings.max_open_positions
        )

        # Variables for tick logging
        decision = "SKIP"
        decision_reason = skip_reason if skip else ""
        tick_id = None

        if skip:
            logger.debug(f"{symbol}: Skipped ({skip_reason})")
            # Still log the skip for analysis (but don't fetch market data)
            return None

        try:
            # Fetch market data
            klines = await self.client.get_klines(
                symbol, "1m", settings.kline_limit
            )
            book_ticker = await self.client.get_book_ticker(symbol)
            ticker_24h = await self.client.get_ticker_24h(symbol)

            # Calculate indicators
            ind = self.indicators.calculate(symbol, klines)
            if not ind.is_valid:
                decision = "SKIP"
                decision_reason = "Invalid indicators"
                return None

            # Run safety filters
            filter_result = self.filters.check(symbol, book_ticker, ticker_24h)

            # Get external signal
            ext_signal = await self.signals.get_signal(symbol)

            # Calculate score
            score = self.scorer.calculate_score(
                ind, ext_signal, filter_result.spread_pct
            )

            # Determine decision
            is_signal = score.total_score >= 1.0 or score.total_score <= -1.0
            if not filter_result.passed:
                decision = "FILTERED"
                decision_reason = filter_result.rejection_reason
            elif not score.is_buy_signal:
                decision = "NO_SIGNAL"
                decision_reason = f"Score {score.total_score:.2f} below threshold"
            else:
                decision = "CANDIDATE"
                decision_reason = f"Score {score.total_score:.2f}"

            # Log tick for research/analytics
            tick_id = await self._log_tick(
                symbol=symbol,
                book_ticker=book_ticker,
                ind=ind,
                score=score,
                filter_result=filter_result,
                decision=decision,
                decision_reason=decision_reason,
                is_signal=is_signal
            )

            # Log interesting signals
            if is_signal:
                logger.info(
                    f"{symbol}: Score={score.total_score:.2f} "
                    f"({', '.join(score.reasons)})"
                )
                if self.on_signal:
                    await self.on_signal(symbol, score)

            if not filter_result.passed:
                logger.debug(f"{symbol}: Filter failed - {filter_result.rejection_reason}")
                return None

            if not score.is_buy_signal:
                return None

            return TradeCandidate(
                symbol=symbol,
                score=score,
                filter_result=filter_result,
                priority=0,  # Will be calculated by selector
                entry_price=book_ticker.ask_price,
                tick_id=tick_id  # Store for simulated trade tracking
            )

        except Exception as e:
            logger.error(f"{symbol}: Analysis error - {e}")
            return None

    async def _log_tick(
        self,
        symbol: str,
        book_ticker: BookTicker,
        ind: IndicatorResult,
        score: SignalScore,
        filter_result: FilterResult,
        decision: str,
        decision_reason: str,
        is_signal: bool
    ) -> int:
        """Log tick data for research/analytics."""
        try:
            tick_id = await self.ticks.record(
                symbol=symbol,
                timestamp=datetime.now(timezone.utc).isoformat(),
                price=book_ticker.ask_price,
                bid=book_ticker.bid_price,
                ask=book_ticker.ask_price,
                spread_pct=filter_result.spread_pct,
                indicators={
                    "momentum_2m": ind.momentum_2m,
                    "momentum_5m": ind.momentum_5m,
                    "volume_ratio": ind.volume_ratio,
                    "rsi": ind.rsi,
                    "atr": ind.atr,
                    "atr_pct": ind.atr_pct
                },
                scores={
                    "momentum": score.momentum_score,
                    "volume": score.volume_score,
                    "rsi": score.rsi_score,
                    "external": score.external_signal,
                    "total": score.total_score
                },
                filters={
                    "spread_ok": filter_result.spread_ok,
                    "volume_ok": filter_result.volume_ok,
                    "depth_ok": filter_result.depth_ok,
                    "all_passed": filter_result.passed
                },
                decision=decision,
                decision_reason=decision_reason,
                is_signal=is_signal,
                strategy_version_id=self.current_strategy_version_id
            )
            return tick_id
        except Exception as e:
            logger.error(f"Failed to log tick: {e}")
            return 0

    async def _execute_entry(self, candidate: TradeCandidate):
        """Execute entry trade."""
        symbol = candidate.symbol
        entry_price = candidate.entry_price

        try:
            # Get fresh market data for execution
            book_ticker = await self.client.get_book_ticker(symbol)
            ticker_24h = await self.client.get_ticker_24h(symbol)
            klines = await self.client.get_klines(symbol, "1m", 30)
            ind = self.indicators.calculate(symbol, klines)

            # Calculate risk parameters
            risk_params = self.risk.calculate_risk_params(
                entry_price, ind, candidate.filter_result.spread_pct
            )

            logger.info(f"Opening position: {symbol}")
            logger.info(f"  Entry: {entry_price:.6f}")
            logger.info(f"  SL: {risk_params.stop_loss_price:.6f} ({risk_params.stop_loss_pct:.2f}%)")
            logger.info(f"  TP: {risk_params.take_profit_price:.6f}")
            logger.info(f"  Qty: {risk_params.quantity:.6f}")

            # Simulate realistic execution in DRY_RUN mode
            simulated_exec = None
            if settings.dry_run:
                order_type = "LIMIT" if settings.use_limit_orders else "MARKET"
                simulated_exec = self.simulator.simulate_buy(
                    ask_price=book_ticker.ask_price,
                    bid_price=book_ticker.bid_price,
                    quantity=risk_params.quantity,
                    order_type=order_type,
                    volume_24h=ticker_24h.volume if ticker_24h else None
                )
                logger.info(f"  [SIM] Slippage: {simulated_exec.slippage_pct:.4f}%")
                logger.info(f"  [SIM] Fees: ${simulated_exec.fee_amount:.4f} ({simulated_exec.fee_rate*100:.2f}%)")
                logger.info(f"  [SIM] Fill ratio: {simulated_exec.fill_ratio*100:.1f}%")
                if simulated_exec.is_partial:
                    logger.warning(f"  [SIM] PARTIAL FILL: {simulated_exec.filled_qty:.6f} of {risk_params.quantity:.6f}")

            # Execute order
            if settings.use_limit_orders:
                order = await self.client.buy_limit(
                    symbol, risk_params.quantity, entry_price, "GTC"
                )
                # Wait for fill with timeout
                await self._wait_for_fill(order, symbol)
            else:
                order = await self.client.buy_market(symbol, risk_params.quantity)

            if not order.is_filled and not settings.dry_run:
                logger.warning(f"{symbol}: Order not filled, canceling")
                await self.client.cancel_order(symbol, order.order_id)
                return

            # Use simulated execution price in DRY_RUN
            actual_entry_price = order.avg_price or entry_price
            actual_quantity = risk_params.quantity
            if settings.dry_run and simulated_exec:
                actual_entry_price = simulated_exec.executed_price
                actual_quantity = simulated_exec.filled_qty

                # Record simulated trade for analysis
                if candidate.tick_id:
                    await self.simulated_trades.record(
                        tick_id=candidate.tick_id,
                        symbol=symbol,
                        side="BUY",
                        intended_price=entry_price,
                        simulated_price=simulated_exec.executed_price,
                        slippage_pct=simulated_exec.slippage_pct,
                        fee_rate=simulated_exec.fee_rate,
                        fee_amount=simulated_exec.fee_amount,
                        intended_qty=risk_params.quantity,
                        filled_qty=simulated_exec.filled_qty,
                        fill_ratio=simulated_exec.fill_ratio,
                        net_cost=simulated_exec.net_cost,
                        strategy_version_id=self.current_strategy_version_id
                    )

            # Create position in database
            position_id = await self.positions.create(
                symbol=symbol,
                quantity=actual_quantity,
                entry_price=actual_entry_price,
                stop_loss=risk_params.stop_loss_price,
                take_profit=risk_params.take_profit_price,
                entry_order_id=order.order_id
            )

            # Record trade
            await self.trades.record(
                symbol=symbol,
                side="BUY",
                order_type=order.type,
                quantity=actual_quantity,
                price=actual_entry_price,
                order_id=order.order_id,
                status=order.status,
                position_id=position_id,
                is_entry=True
            )

            logger.info(f"Position opened: {symbol} (ID: {position_id})")

            # Notify via callback
            if self.on_trade_open:
                await self.on_trade_open(symbol, risk_params, order)

        except Exception as e:
            logger.error(f"Entry execution error: {e}", exc_info=True)

    async def _check_position_exit(self, position: Dict):
        """Check if position should be exited."""
        symbol = position["symbol"]
        position_id = position["id"]
        entry_price = position["entry_price"]
        quantity = position["quantity"]
        stop_loss = position["stop_loss"]
        take_profit = position["take_profit"]
        trailing_stop = position.get("trailing_stop")
        peak_price = position.get("peak_price", entry_price)

        try:
            # Get current price
            book_ticker = await self.client.get_book_ticker(symbol)
            current_price = book_ticker.bid_price

            # Update position with current price
            await self.positions.update_price(position_id, current_price)

            # Calculate trailing stop if enabled
            new_trailing = self.risk.calculate_trailing_stop(
                entry_price, current_price, peak_price, trailing_stop
            )
            if new_trailing and new_trailing != trailing_stop:
                await self.positions.update_price(
                    position_id, current_price, new_trailing
                )
                trailing_stop = new_trailing

            # Get indicators for exit decision
            klines = await self.client.get_klines(symbol, "1m", 30)
            ind = self.indicators.calculate(symbol, klines)

            # Check exit conditions
            should_exit, reason = self.scorer.should_exit(
                entry_price, current_price, stop_loss, take_profit,
                trailing_stop, ind
            )

            if should_exit:
                await self._execute_exit(position, current_price, reason)

        except Exception as e:
            logger.error(f"Position check error {symbol}: {e}")

    async def _execute_exit(self, position: Dict, exit_price: float, reason: str):
        """Execute exit trade."""
        symbol = position["symbol"]
        position_id = position["id"]
        quantity = position["quantity"]
        entry_price = position["entry_price"]

        logger.info(f"Closing position: {symbol} ({reason})")

        try:
            # Get fresh market data for exit simulation
            book_ticker = await self.client.get_book_ticker(symbol)
            ticker_24h = await self.client.get_ticker_24h(symbol)

            # Simulate realistic execution in DRY_RUN mode
            simulated_exec = None
            if settings.dry_run:
                order_type = "LIMIT" if settings.use_limit_orders else "MARKET"
                simulated_exec = self.simulator.simulate_sell(
                    bid_price=book_ticker.bid_price,
                    ask_price=book_ticker.ask_price,
                    quantity=quantity,
                    order_type=order_type,
                    volume_24h=ticker_24h.volume if ticker_24h else None
                )
                logger.info(f"  [SIM] Exit slippage: {simulated_exec.slippage_pct:.4f}%")
                logger.info(f"  [SIM] Exit fees: ${simulated_exec.fee_amount:.4f}")

            # Execute sell order
            if settings.use_limit_orders:
                order = await self.client.sell_limit(
                    symbol, quantity, exit_price, "GTC"
                )
                await self._wait_for_fill(order, symbol)
            else:
                order = await self.client.sell_market(symbol, quantity)

            if not order.is_filled and not settings.dry_run:
                # If limit didn't fill, try market
                logger.warning(f"{symbol}: Limit sell not filled, using market")
                await self.client.cancel_order(symbol, order.order_id)
                order = await self.client.sell_market(symbol, quantity)

            # Use simulated exit price in DRY_RUN
            actual_exit_price = order.avg_price or exit_price
            if settings.dry_run and simulated_exec:
                actual_exit_price = simulated_exec.executed_price

            # Close position in database (with realistic price)
            pnl = await self.positions.close(
                position_id,
                exit_price=actual_exit_price,
                exit_order_id=order.order_id,
                exit_reason=reason
            )

            # Adjust PnL for fees in DRY_RUN
            if settings.dry_run and simulated_exec:
                # Subtract both entry and exit fees from PnL
                # (entry fees already accounted in entry price, exit fees need subtraction)
                pnl -= simulated_exec.fee_amount
                logger.info(f"  [SIM] Net PnL after fees: ${pnl:.2f}")

            # Record trade
            await self.trades.record(
                symbol=symbol,
                side="SELL",
                order_type=order.type,
                quantity=quantity,
                price=actual_exit_price,
                order_id=order.order_id,
                status=order.status,
                position_id=position_id,
                is_entry=False
            )

            # Set cooldown
            await self.cooldowns.set_cooldown(symbol, settings.cooldown_sec)

            logger.info(f"Position closed: {symbol}, PnL: ${pnl:.2f}")

            # Notify via callback
            if self.on_trade_close:
                await self.on_trade_close(symbol, pnl, reason)

        except Exception as e:
            logger.error(f"Exit execution error: {e}", exc_info=True)

    async def _wait_for_fill(self, order, symbol: str, timeout: int = None):
        """Wait for limit order to fill with timeout."""
        timeout = timeout or settings.limit_order_timeout_sec
        start = asyncio.get_event_loop().time()

        while asyncio.get_event_loop().time() - start < timeout:
            if settings.dry_run:
                return  # Dry run orders are always "filled"

            order = await self.client.get_order(symbol, order.order_id)
            if order.is_filled:
                return
            await asyncio.sleep(1)

        logger.warning(f"{symbol}: Order {order.order_id} not filled after {timeout}s")

    async def get_status(self) -> Dict:
        """Get engine status for reporting."""
        open_positions = await self.positions.get_open() if self.positions else []
        daily_summary = await self.trades.get_daily_summary() if self.trades else {}

        uptime = None
        if self.start_time:
            uptime = str(datetime.now(timezone.utc) - self.start_time).split('.')[0]

        return {
            "running": self.running,
            "mode": "DRY_RUN" if settings.dry_run else "LIVE",
            "uptime": uptime,
            "watchlist": self.watchlist,
            "open_positions": len(open_positions),
            "daily_pnl": daily_summary.get("total_pnl", 0),
            "daily_trades": daily_summary.get("total_trades", 0),
            "win_rate": daily_summary.get("win_rate", 0)
        }
