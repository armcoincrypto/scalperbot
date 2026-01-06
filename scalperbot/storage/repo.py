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
