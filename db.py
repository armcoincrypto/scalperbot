"""
Database module for trade logging
SQLite-based trade history storage
"""
import sqlite3
from datetime import datetime
from typing import Optional, List, Dict, Any
from pathlib import Path


class TradeDB:
    """SQLite database for trade logging"""

    def __init__(self, db_path: str):
        self.db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = None
        self.init_db()

    def init_db(self):
        """Initialize database schema"""
        self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row

        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                symbol TEXT NOT NULL,
                side TEXT NOT NULL,
                price REAL NOT NULL,
                quantity REAL NOT NULL,
                notional REAL NOT NULL,
                fee REAL DEFAULT 0,
                order_id TEXT,
                status TEXT DEFAULT 'NEW',
                pnl REAL DEFAULT 0,
                signal_reason TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)

        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS daily_pnl (
                date TEXT PRIMARY KEY,
                pnl REAL DEFAULT 0,
                num_trades INTEGER DEFAULT 0,
                win_count INTEGER DEFAULT 0,
                loss_count INTEGER DEFAULT 0,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)

        self.conn.commit()

    def log_trade(
        self,
        symbol: str,
        side: str,
        price: float,
        quantity: float,
        notional: float,
        signal_reason: str = "",
        order_id: Optional[str] = None,
        status: str = "NEW"
    ) -> int:
        """Log a new trade"""
        cursor = self.conn.execute("""
            INSERT INTO trades (timestamp, symbol, side, price, quantity, notional, signal_reason, order_id, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            datetime.utcnow().isoformat(),
            symbol,
            side,
            price,
            quantity,
            notional,
            signal_reason,
            order_id,
            status
        ))
        self.conn.commit()
        return cursor.lastrowid

    def update_trade_status(self, trade_id: int, status: str, order_id: Optional[str] = None):
        """Update trade status"""
        if order_id:
            self.conn.execute(
                "UPDATE trades SET status = ?, order_id = ? WHERE id = ?",
                (status, order_id, trade_id)
            )
        else:
            self.conn.execute(
                "UPDATE trades SET status = ? WHERE id = ?",
                (status, trade_id)
            )
        self.conn.commit()

    def update_trade_pnl(self, trade_id: int, pnl: float):
        """Update trade PnL"""
        self.conn.execute(
            "UPDATE trades SET pnl = ? WHERE id = ?",
            (pnl, trade_id)
        )
        self.conn.commit()

    def get_daily_pnl(self, date: Optional[str] = None) -> float:
        """Get PnL for a specific date (default: today)"""
        if date is None:
            date = datetime.utcnow().strftime("%Y-%m-%d")

        cursor = self.conn.execute(
            "SELECT pnl FROM daily_pnl WHERE date = ?",
            (date,)
        )
        row = cursor.fetchone()
        return row['pnl'] if row else 0.0

    def update_daily_pnl(self, pnl_delta: float, date: Optional[str] = None):
        """Update daily PnL"""
        if date is None:
            date = datetime.utcnow().strftime("%Y-%m-%d")

        self.conn.execute("""
            INSERT INTO daily_pnl (date, pnl, num_trades)
            VALUES (?, ?, 1)
            ON CONFLICT(date) DO UPDATE SET
                pnl = pnl + ?,
                num_trades = num_trades + 1,
                updated_at = CURRENT_TIMESTAMP
        """, (date, pnl_delta, pnl_delta))
        self.conn.commit()

    def get_recent_trades(self, limit: int = 20) -> List[Dict[str, Any]]:
        """Get recent trades"""
        cursor = self.conn.execute("""
            SELECT * FROM trades
            ORDER BY created_at DESC
            LIMIT ?
        """, (limit,))
        return [dict(row) for row in cursor.fetchall()]

    def get_open_positions(self) -> List[Dict[str, Any]]:
        """Get currently open positions"""
        cursor = self.conn.execute("""
            SELECT * FROM trades
            WHERE status IN ('FILLED', 'OPEN')
            ORDER BY created_at DESC
        """)
        return [dict(row) for row in cursor.fetchall()]

    def close(self):
        """Close database connection"""
        if self.conn:
            self.conn.close()
