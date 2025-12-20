"""
Database module for trade logging
SQLite-based trade history storage with position tracking
"""
import sqlite3
from datetime import datetime
from typing import Optional, List, Dict, Any
from pathlib import Path


class TradeDB:
    """SQLite database for trade logging and position tracking"""

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

        # New positions table for tracking open positions with TP/SL/trailing
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS positions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trade_id INTEGER NOT NULL,
                symbol TEXT NOT NULL,
                side TEXT NOT NULL,
                entry_price REAL NOT NULL,
                quantity REAL NOT NULL,
                notional REAL NOT NULL,
                take_profit_price REAL,
                stop_loss_price REAL,
                trailing_stop_price REAL,
                highest_price REAL,
                status TEXT DEFAULT 'OPEN',
                exit_price REAL,
                exit_reason TEXT,
                pnl REAL DEFAULT 0,
                fee REAL DEFAULT 0,
                opened_at TEXT NOT NULL,
                closed_at TEXT,
                FOREIGN KEY (trade_id) REFERENCES trades(id)
            )
        """)

        # Create indexes for performance
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_positions_symbol ON positions(symbol)")
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_positions_status ON positions(status)")

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
        """Get currently open positions from positions table"""
        cursor = self.conn.execute("""
            SELECT * FROM positions
            WHERE status = 'OPEN'
            ORDER BY opened_at DESC
        """)
        return [dict(row) for row in cursor.fetchall()]

    def open_position(
        self,
        trade_id: int,
        symbol: str,
        side: str,
        entry_price: float,
        quantity: float,
        notional: float,
        take_profit_price: float,
        stop_loss_price: float
    ) -> int:
        """Open a new position with TP/SL levels"""
        cursor = self.conn.execute("""
            INSERT INTO positions (
                trade_id, symbol, side, entry_price, quantity, notional,
                take_profit_price, stop_loss_price, highest_price,
                status, opened_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'OPEN', ?)
        """, (
            trade_id,
            symbol,
            side,
            entry_price,
            quantity,
            notional,
            take_profit_price,
            stop_loss_price,
            entry_price,  # highest_price starts at entry
            datetime.utcnow().isoformat()
        ))
        self.conn.commit()
        return cursor.lastrowid

    def close_position(
        self,
        position_id: int,
        exit_price: float,
        exit_reason: str,
        pnl: float,
        fee: float = 0
    ):
        """Close a position and record PnL"""
        self.conn.execute("""
            UPDATE positions SET
                status = 'CLOSED',
                exit_price = ?,
                exit_reason = ?,
                pnl = ?,
                fee = ?,
                closed_at = ?
            WHERE id = ?
        """, (
            exit_price,
            exit_reason,
            pnl,
            fee,
            datetime.utcnow().isoformat(),
            position_id
        ))
        self.conn.commit()

    def update_position_trailing(
        self,
        position_id: int,
        highest_price: float,
        trailing_stop_price: float
    ):
        """Update trailing stop price for a position"""
        self.conn.execute("""
            UPDATE positions SET
                highest_price = ?,
                trailing_stop_price = ?
            WHERE id = ?
        """, (highest_price, trailing_stop_price, position_id))
        self.conn.commit()

    def update_position_status(self, position_id: int, status: str):
        """Update position status (OPEN, CLOSING, CLOSED)"""
        self.conn.execute("""
            UPDATE positions SET status = ? WHERE id = ?
        """, (status, position_id))
        self.conn.commit()

    def get_position_by_symbol(self, symbol: str) -> Optional[Dict[str, Any]]:
        """Get open position for a specific symbol"""
        cursor = self.conn.execute("""
            SELECT * FROM positions
            WHERE symbol = ? AND status = 'OPEN'
            ORDER BY opened_at DESC
            LIMIT 1
        """, (symbol,))
        row = cursor.fetchone()
        return dict(row) if row else None

    def get_position_count(self) -> int:
        """Get count of open positions"""
        cursor = self.conn.execute("""
            SELECT COUNT(*) as count FROM positions WHERE status = 'OPEN'
        """)
        row = cursor.fetchone()
        return row['count'] if row else 0

    def update_daily_pnl_with_result(self, pnl: float, is_win: bool, date: Optional[str] = None):
        """Update daily PnL with win/loss tracking"""
        if date is None:
            date = datetime.utcnow().strftime("%Y-%m-%d")

        self.conn.execute("""
            INSERT INTO daily_pnl (date, pnl, num_trades, win_count, loss_count)
            VALUES (?, ?, 1, ?, ?)
            ON CONFLICT(date) DO UPDATE SET
                pnl = pnl + ?,
                num_trades = num_trades + 1,
                win_count = win_count + ?,
                loss_count = loss_count + ?,
                updated_at = CURRENT_TIMESTAMP
        """, (
            date,
            pnl,
            1 if is_win else 0,
            0 if is_win else 1,
            pnl,
            1 if is_win else 0,
            0 if is_win else 1
        ))
        self.conn.commit()

    def get_daily_stats(self, date: Optional[str] = None) -> Dict[str, Any]:
        """Get daily trading statistics"""
        if date is None:
            date = datetime.utcnow().strftime("%Y-%m-%d")

        cursor = self.conn.execute("""
            SELECT * FROM daily_pnl WHERE date = ?
        """, (date,))
        row = cursor.fetchone()

        if row:
            return {
                'date': row['date'],
                'pnl': row['pnl'],
                'num_trades': row['num_trades'],
                'win_count': row['win_count'],
                'loss_count': row['loss_count'],
                'win_rate': (row['win_count'] / row['num_trades'] * 100) if row['num_trades'] > 0 else 0
            }
        return {
            'date': date,
            'pnl': 0,
            'num_trades': 0,
            'win_count': 0,
            'loss_count': 0,
            'win_rate': 0
        }

    def close(self):
        """Close database connection"""
        if self.conn:
            self.conn.close()
