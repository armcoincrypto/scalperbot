"""
Repository classes for database operations.
Clean separation of data access logic.
"""

from typing import List, Optional, Dict, Any
from datetime import datetime, timedelta, timezone

from scalperbot.storage.db import Database, get_database
from scalperbot.log import get_logger

logger = get_logger(__name__)


class PositionRepo:
    """Repository for position management."""

    def __init__(self, db: Database):
        self.db = db

    async def create(
        self,
        symbol: str,
        quantity: float,
        entry_price: float,
        stop_loss: float,
        take_profit: float,
        entry_order_id: str = ""
    ) -> int:
        """Create new position."""
        return await self.db.insert("positions", {
            "symbol": symbol,
            "side": "BUY",
            "quantity": quantity,
            "entry_price": entry_price,
            "current_price": entry_price,
            "stop_loss": stop_loss,
            "take_profit": take_profit,
            "peak_price": entry_price,
            "status": "OPEN",
            "entry_order_id": entry_order_id,
            "entry_time": datetime.now(timezone.utc).isoformat()
        })

    async def get_open(self, symbol: Optional[str] = None) -> List[Dict]:
        """Get all open positions, optionally filtered by symbol."""
        if symbol:
            return await self.db.fetch_all(
                "SELECT * FROM positions WHERE status = 'OPEN' AND symbol = ?",
                (symbol,)
            )
        return await self.db.fetch_all(
            "SELECT * FROM positions WHERE status = 'OPEN'"
        )

    async def get_by_id(self, position_id: int) -> Optional[Dict]:
        """Get position by ID."""
        return await self.db.fetch_one(
            "SELECT * FROM positions WHERE id = ?",
            (position_id,)
        )

    async def update_price(
        self,
        position_id: int,
        current_price: float,
        trailing_stop: Optional[float] = None
    ):
        """Update current price and optionally trailing stop."""
        data = {"current_price": current_price}

        # Update peak price if higher
        position = await self.get_by_id(position_id)
        if position and current_price > (position.get("peak_price") or 0):
            data["peak_price"] = current_price

        if trailing_stop is not None:
            data["trailing_stop"] = trailing_stop

        await self.db.update("positions", data, "id = ?", (position_id,))

    async def close(
        self,
        position_id: int,
        exit_price: float,
        exit_order_id: str,
        exit_reason: str
    ) -> float:
        """
        Close position and calculate PnL.

        Returns:
            Realized PnL in quote currency
        """
        position = await self.get_by_id(position_id)
        if not position:
            return 0.0

        entry_price = position["entry_price"]
        quantity = position["quantity"]
        pnl = (exit_price - entry_price) * quantity

        await self.db.update("positions", {
            "status": "CLOSED",
            "exit_price": exit_price,
            "exit_order_id": exit_order_id,
            "exit_time": datetime.now(timezone.utc).isoformat(),
            "realized_pnl": pnl,
            "exit_reason": exit_reason
        }, "id = ?", (position_id,))

        logger.info(f"Position {position_id} closed: {exit_reason}, PnL: ${pnl:.2f}")
        return pnl

    async def count_open(self) -> int:
        """Count open positions."""
        result = await self.db.fetch_one(
            "SELECT COUNT(*) as count FROM positions WHERE status = 'OPEN'"
        )
        return result["count"] if result else 0

    async def has_open_position(self, symbol: str) -> bool:
        """Check if symbol has open position."""
        result = await self.db.fetch_one(
            "SELECT 1 FROM positions WHERE symbol = ? AND status = 'OPEN'",
            (symbol,)
        )
        return result is not None


class TradeRepo:
    """Repository for trade history."""

    def __init__(self, db: Database):
        self.db = db

    async def record(
        self,
        symbol: str,
        side: str,
        order_type: str,
        quantity: float,
        price: float,
        order_id: str,
        status: str,
        position_id: Optional[int] = None,
        is_entry: bool = False
    ) -> int:
        """Record a trade."""
        return await self.db.insert("trades", {
            "position_id": position_id,
            "symbol": symbol,
            "side": side,
            "order_type": order_type,
            "quantity": quantity,
            "price": price,
            "quote_amount": quantity * price,
            "order_id": order_id,
            "status": status,
            "is_entry": 1 if is_entry else 0
        })

    async def get_recent(self, limit: int = 20) -> List[Dict]:
        """Get recent trades."""
        return await self.db.fetch_all(
            "SELECT * FROM trades ORDER BY created_at DESC LIMIT ?",
            (limit,)
        )

    async def get_daily_summary(self, date: Optional[str] = None) -> Dict:
        """Get daily trading summary."""
        if date is None:
            date = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        trades = await self.db.fetch_all(
            """
            SELECT * FROM positions
            WHERE date(exit_time) = ? AND status = 'CLOSED'
            """,
            (date,)
        )

        total_trades = len(trades)
        winning = sum(1 for t in trades if (t.get("realized_pnl") or 0) > 0)
        losing = sum(1 for t in trades if (t.get("realized_pnl") or 0) < 0)
        total_pnl = sum(t.get("realized_pnl") or 0 for t in trades)

        return {
            "date": date,
            "total_trades": total_trades,
            "winning_trades": winning,
            "losing_trades": losing,
            "win_rate": (winning / total_trades * 100) if total_trades > 0 else 0,
            "total_pnl": total_pnl
        }


class SignalRepo:
    """Repository for external signals."""

    def __init__(self, db: Database):
        self.db = db

    async def add_signal(
        self,
        symbol: str,
        weight: float,
        source: str = "telegram",
        ttl_minutes: int = 30
    ) -> int:
        """
        Add external signal with TTL.

        Args:
            symbol: Trading symbol
            weight: Signal weight (+/- value)
            source: Signal source
            ttl_minutes: Time to live in minutes

        Returns:
            Signal ID
        """
        expires_at = (datetime.now(timezone.utc) + timedelta(minutes=ttl_minutes)).isoformat()

        # Update existing signal or insert new
        existing = await self.db.fetch_one(
            "SELECT id FROM signals WHERE symbol = ? AND source = ?",
            (symbol, source)
        )

        if existing:
            await self.db.update(
                "signals",
                {"weight": weight, "expires_at": expires_at},
                "id = ?",
                (existing["id"],)
            )
            return existing["id"]

        return await self.db.insert("signals", {
            "symbol": symbol,
            "weight": weight,
            "source": source,
            "expires_at": expires_at
        })

    async def get_signal(self, symbol: str) -> float:
        """Get current signal weight for symbol (0 if expired or none)."""
        result = await self.db.fetch_one(
            """
            SELECT weight FROM signals
            WHERE symbol = ? AND expires_at > datetime('now')
            ORDER BY created_at DESC LIMIT 1
            """,
            (symbol,)
        )
        return result["weight"] if result else 0.0

    async def clear_expired(self):
        """Remove expired signals."""
        await self.db.execute(
            "DELETE FROM signals WHERE expires_at <= datetime('now')"
        )


class CooldownRepo:
    """Repository for symbol cooldowns."""

    def __init__(self, db: Database):
        self.db = db

    async def set_cooldown(self, symbol: str, seconds: int):
        """Set cooldown for symbol."""
        expires_at = (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat()

        await self.db.execute(
            """
            INSERT OR REPLACE INTO cooldowns (symbol, expires_at)
            VALUES (?, ?)
            """,
            (symbol, expires_at)
        )

    async def is_in_cooldown(self, symbol: str) -> bool:
        """Check if symbol is in cooldown."""
        result = await self.db.fetch_one(
            "SELECT 1 FROM cooldowns WHERE symbol = ? AND expires_at > datetime('now')",
            (symbol,)
        )
        return result is not None

    async def clear_expired(self):
        """Remove expired cooldowns."""
        await self.db.execute(
            "DELETE FROM cooldowns WHERE expires_at <= datetime('now')"
        )


# ============================================================
# RESEARCH/ANALYTICS REPOSITORIES
# ============================================================

class TickRepo:
    """Repository for tick data (every poll snapshot for analysis)."""

    def __init__(self, db: Database):
        self.db = db

    async def record(
        self,
        symbol: str,
        timestamp: str,
        price: float,
        bid: float,
        ask: float,
        spread_pct: float,
        indicators: Dict[str, Any],
        scores: Dict[str, Any],
        filters: Dict[str, Any],
        decision: str,
        decision_reason: str,
        is_signal: bool = False,
        strategy_version_id: Optional[int] = None
    ) -> int:
        """Record a tick snapshot."""
        return await self.db.insert("ticks", {
            "symbol": symbol,
            "timestamp": timestamp,
            "price": price,
            "bid": bid,
            "ask": ask,
            "spread_pct": spread_pct,
            # Indicators
            "momentum_2m": indicators.get("momentum_2m"),
            "momentum_5m": indicators.get("momentum_5m"),
            "volume_ratio": indicators.get("volume_ratio"),
            "rsi": indicators.get("rsi"),
            "atr": indicators.get("atr"),
            "atr_pct": indicators.get("atr_pct"),
            # Scores
            "score_momentum": scores.get("momentum"),
            "score_volume": scores.get("volume"),
            "score_rsi": scores.get("rsi"),
            "score_external": scores.get("external"),
            "score_total": scores.get("total"),
            # Filters
            "filter_spread_ok": 1 if filters.get("spread_ok") else 0,
            "filter_volume_ok": 1 if filters.get("volume_ok") else 0,
            "filter_depth_ok": 1 if filters.get("depth_ok") else 0,
            "filters_passed": 1 if filters.get("all_passed") else 0,
            # Decision
            "is_signal": 1 if is_signal else 0,
            "decision": decision,
            "decision_reason": decision_reason,
            "strategy_version_id": strategy_version_id
        })

    async def get_unlabeled(self, min_age_minutes: int = 15, limit: int = 100) -> List[Dict]:
        """Get ticks that need outcome labeling (at least N minutes old)."""
        cutoff = (datetime.now(timezone.utc) - timedelta(minutes=min_age_minutes)).isoformat()
        return await self.db.fetch_all(
            """
            SELECT t.* FROM ticks t
            LEFT JOIN outcomes o ON t.id = o.tick_id
            WHERE o.tick_id IS NULL AND t.timestamp < ?
            ORDER BY t.timestamp ASC
            LIMIT ?
            """,
            (cutoff, limit)
        )

    async def get_recent(self, symbol: Optional[str] = None, limit: int = 100) -> List[Dict]:
        """Get recent ticks, optionally filtered by symbol."""
        if symbol:
            return await self.db.fetch_all(
                "SELECT * FROM ticks WHERE symbol = ? ORDER BY timestamp DESC LIMIT ?",
                (symbol, limit)
            )
        return await self.db.fetch_all(
            "SELECT * FROM ticks ORDER BY timestamp DESC LIMIT ?",
            (limit,)
        )

    async def get_signals_only(self, limit: int = 100) -> List[Dict]:
        """Get only ticks that were signals."""
        return await self.db.fetch_all(
            "SELECT * FROM ticks WHERE is_signal = 1 ORDER BY timestamp DESC LIMIT ?",
            (limit,)
        )

    async def count_by_decision(self, hours: int = 24) -> Dict[str, int]:
        """Count ticks by decision in last N hours."""
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
        rows = await self.db.fetch_all(
            """
            SELECT decision, COUNT(*) as count
            FROM ticks WHERE timestamp > ?
            GROUP BY decision
            """,
            (cutoff,)
        )
        return {row["decision"]: row["count"] for row in rows}


class OutcomeRepo:
    """Repository for outcome labeling (forward returns)."""

    def __init__(self, db: Database):
        self.db = db

    async def label(
        self,
        tick_id: int,
        returns: Dict[str, float],
        mfe_mae: Dict[str, float],
        tp_sl_hits: Dict[str, bool],
        first_hit: str,
        first_hit_time_sec: Optional[int] = None
    ):
        """Label a tick with outcome data."""
        await self.db.execute(
            """
            INSERT OR REPLACE INTO outcomes (
                tick_id, return_1m, return_5m, return_15m, return_30m,
                mfe_5m, mae_5m, mfe_15m, mae_15m,
                tp_hit_5m, sl_hit_5m, tp_hit_15m, sl_hit_15m,
                first_hit, first_hit_time_sec, labeled_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
            """,
            (
                tick_id,
                returns.get("1m"), returns.get("5m"), returns.get("15m"), returns.get("30m"),
                mfe_mae.get("mfe_5m"), mfe_mae.get("mae_5m"),
                mfe_mae.get("mfe_15m"), mfe_mae.get("mae_15m"),
                1 if tp_sl_hits.get("tp_5m") else 0,
                1 if tp_sl_hits.get("sl_5m") else 0,
                1 if tp_sl_hits.get("tp_15m") else 0,
                1 if tp_sl_hits.get("sl_15m") else 0,
                first_hit,
                first_hit_time_sec
            )
        )

    async def get_stats(self, strategy_version_id: Optional[int] = None) -> Dict[str, Any]:
        """Get outcome statistics for strategy analysis."""
        version_filter = ""
        params = []
        if strategy_version_id:
            version_filter = "AND t.strategy_version_id = ?"
            params.append(strategy_version_id)

        # Win rate based on TP/SL hits
        row = await self.db.fetch_one(
            f"""
            SELECT
                COUNT(*) as total,
                SUM(CASE WHEN o.first_hit = 'TP' THEN 1 ELSE 0 END) as tp_wins,
                SUM(CASE WHEN o.first_hit = 'SL' THEN 1 ELSE 0 END) as sl_losses,
                AVG(o.return_5m) as avg_return_5m,
                AVG(o.return_15m) as avg_return_15m,
                AVG(o.mfe_5m) as avg_mfe_5m,
                AVG(o.mae_5m) as avg_mae_5m
            FROM outcomes o
            JOIN ticks t ON o.tick_id = t.id
            WHERE t.is_signal = 1 {version_filter}
            """,
            params
        )
        return dict(row) if row else {}


class StrategyVersionRepo:
    """Repository for strategy version management."""

    def __init__(self, db: Database):
        self.db = db

    async def create(
        self,
        name: str,
        parameters: Dict[str, Any],
        description: str = ""
    ) -> int:
        """Create a new strategy version."""
        import json

        # Deactivate current active version
        await self.db.execute(
            "UPDATE strategy_versions SET is_active = 0 WHERE is_active = 1"
        )

        return await self.db.insert("strategy_versions", {
            "name": name,
            "description": description,
            "parameters": json.dumps(parameters),
            "buy_pct_trigger": parameters.get("buy_pct_trigger"),
            "buy_score_min": parameters.get("buy_score_min"),
            "base_sl_pct": parameters.get("base_sl_pct"),
            "take_profit_pct": parameters.get("take_profit_pct"),
            "max_spread_pct": parameters.get("max_spread_pct"),
            "is_active": 1
        })

    async def get_active(self) -> Optional[Dict]:
        """Get the currently active strategy version."""
        return await self.db.fetch_one(
            "SELECT * FROM strategy_versions WHERE is_active = 1"
        )

    async def get_by_id(self, version_id: int) -> Optional[Dict]:
        """Get strategy version by ID."""
        return await self.db.fetch_one(
            "SELECT * FROM strategy_versions WHERE id = ?",
            (version_id,)
        )

    async def update_stats(
        self,
        version_id: int,
        total_ticks: int,
        total_signals: int,
        total_entries: int,
        simulated_pnl: float,
        win_rate: Optional[float] = None,
        avg_return: Optional[float] = None
    ):
        """Update performance stats for a strategy version."""
        await self.db.update("strategy_versions", {
            "total_ticks": total_ticks,
            "total_signals": total_signals,
            "total_entries": total_entries,
            "simulated_pnl": simulated_pnl,
            "win_rate": win_rate,
            "avg_return": avg_return,
            "updated_at": datetime.now(timezone.utc).isoformat()
        }, "id = ?", (version_id,))

    async def list_all(self) -> List[Dict]:
        """List all strategy versions."""
        return await self.db.fetch_all(
            "SELECT * FROM strategy_versions ORDER BY created_at DESC"
        )

    async def compare(self, version_ids: List[int]) -> List[Dict]:
        """Compare multiple strategy versions."""
        placeholders = ",".join("?" * len(version_ids))
        return await self.db.fetch_all(
            f"""
            SELECT * FROM strategy_versions
            WHERE id IN ({placeholders})
            ORDER BY win_rate DESC NULLS LAST
            """,
            version_ids
        )


class SimulatedTradeRepo:
    """Repository for simulated trades with realistic execution modeling."""

    def __init__(self, db: Database):
        self.db = db

    async def record(
        self,
        tick_id: int,
        symbol: str,
        side: str,
        intended_price: float,
        simulated_price: float,
        slippage_pct: float,
        fee_rate: float,
        fee_amount: float,
        intended_qty: float,
        filled_qty: float,
        fill_ratio: float,
        net_cost: float,
        strategy_version_id: Optional[int] = None
    ) -> int:
        """Record a simulated trade."""
        return await self.db.insert("simulated_trades", {
            "tick_id": tick_id,
            "symbol": symbol,
            "side": side,
            "intended_price": intended_price,
            "simulated_price": simulated_price,
            "slippage_pct": slippage_pct,
            "fee_rate": fee_rate,
            "fee_amount": fee_amount,
            "intended_qty": intended_qty,
            "filled_qty": filled_qty,
            "fill_ratio": fill_ratio,
            "net_cost": net_cost,
            "strategy_version_id": strategy_version_id
        })

    async def get_summary(self, strategy_version_id: Optional[int] = None) -> Dict[str, Any]:
        """Get summary statistics for simulated trades."""
        version_filter = ""
        params = []
        if strategy_version_id:
            version_filter = "WHERE strategy_version_id = ?"
            params.append(strategy_version_id)

        row = await self.db.fetch_one(
            f"""
            SELECT
                COUNT(*) as total_trades,
                SUM(fee_amount) as total_fees,
                AVG(slippage_pct) as avg_slippage_pct,
                AVG(fill_ratio) as avg_fill_ratio,
                SUM(net_cost) as total_cost
            FROM simulated_trades {version_filter}
            """,
            params
        )
        return dict(row) if row else {}
