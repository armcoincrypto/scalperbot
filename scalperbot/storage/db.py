"""
Async SQLite database connection manager.
"""

import aiosqlite
from typing import Optional, List, Dict, Any
from contextlib import asynccontextmanager

from scalperbot.config import settings
from scalperbot.log import get_logger
from scalperbot.storage.schema import SCHEMA_SQL

logger = get_logger(__name__)


class Database:
    """
    Async SQLite database manager.
    Uses aiosqlite for non-blocking operations.
    """

    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path or settings.database_path
        self._connection: Optional[aiosqlite.Connection] = None

    async def connect(self):
        """Open database connection and initialize schema."""
        if self._connection is None:
            self._connection = await aiosqlite.connect(self.db_path)
            self._connection.row_factory = aiosqlite.Row
            await self._init_schema()
            logger.info(f"Database connected: {self.db_path}")

    async def close(self):
        """Close database connection."""
        if self._connection:
            await self._connection.close()
            self._connection = None
            logger.info("Database connection closed")

    async def _init_schema(self):
        """Initialize database schema."""
        await self._connection.executescript(SCHEMA_SQL)
        await self._connection.commit()

    @asynccontextmanager
    async def transaction(self):
        """Context manager for database transactions."""
        if not self._connection:
            await self.connect()
        try:
            yield self._connection
            await self._connection.commit()
        except Exception:
            await self._connection.rollback()
            raise

    async def execute(
        self,
        sql: str,
        params: tuple = ()
    ) -> aiosqlite.Cursor:
        """Execute SQL statement."""
        if not self._connection:
            await self.connect()
        cursor = await self._connection.execute(sql, params)
        await self._connection.commit()
        return cursor

    async def execute_many(
        self,
        sql: str,
        params_list: List[tuple]
    ):
        """Execute SQL with multiple parameter sets."""
        if not self._connection:
            await self.connect()
        await self._connection.executemany(sql, params_list)
        await self._connection.commit()

    async def fetch_one(
        self,
        sql: str,
        params: tuple = ()
    ) -> Optional[Dict[str, Any]]:
        """Fetch single row as dict."""
        if not self._connection:
            await self.connect()
        async with self._connection.execute(sql, params) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None

    async def fetch_all(
        self,
        sql: str,
        params: tuple = ()
    ) -> List[Dict[str, Any]]:
        """Fetch all rows as list of dicts."""
        if not self._connection:
            await self.connect()
        async with self._connection.execute(sql, params) as cursor:
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]

    async def insert(
        self,
        table: str,
        data: Dict[str, Any]
    ) -> int:
        """Insert row and return ID."""
        columns = ", ".join(data.keys())
        placeholders = ", ".join("?" * len(data))
        sql = f"INSERT INTO {table} ({columns}) VALUES ({placeholders})"

        cursor = await self.execute(sql, tuple(data.values()))
        return cursor.lastrowid

    async def update(
        self,
        table: str,
        data: Dict[str, Any],
        where: str,
        where_params: tuple
    ) -> int:
        """Update rows and return affected count."""
        set_clause = ", ".join(f"{k} = ?" for k in data.keys())
        sql = f"UPDATE {table} SET {set_clause}, updated_at = datetime('now') WHERE {where}"

        cursor = await self.execute(sql, tuple(data.values()) + where_params)
        return cursor.rowcount


# Global database instance
_db: Optional[Database] = None


async def get_database() -> Database:
    """Get or create global database instance."""
    global _db
    if _db is None:
        _db = Database()
        await _db.connect()
    return _db
