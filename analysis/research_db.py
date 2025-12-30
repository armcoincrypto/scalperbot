"""
PHASE 4: RESEARCH DATABASE
============================
Append-only tables for market research.

NOT for trading - for discovering market truths.

Tables:
- displacements: Price displacement events
- retraces: What happens after displacements
- liquidity_events: Stop hunts and sweeps
- regimes: Market regime classifications
- simulated_trades: Hypothetical trade outcomes

All tables are APPEND-ONLY. No overwrites.
This ensures we never lose data or corrupt history.
"""
import sqlite3
import os
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any
import json
import logging

logger = logging.getLogger(__name__)

DB_PATH = "analysis/research.db"


class ResearchDB:
    """
    Research database for edge discovery.

    All inserts are append-only.
    No updates or deletes on core data.
    """

    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        os.makedirs(os.path.dirname(db_path) or '.', exist_ok=True)
        self._init_tables()

    def _get_conn(self) -> sqlite3.Connection:
        """Get database connection"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_tables(self):
        """Initialize all research tables"""
        conn = self._get_conn()
        cursor = conn.cursor()

        # DISPLACEMENTS TABLE
        # Records significant price moves (not noise)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS displacements (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                symbol TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                direction TEXT NOT NULL,  -- BULLISH or BEARISH
                displacement_pct REAL NOT NULL,
                body_ratio REAL NOT NULL,
                volume_ratio REAL NOT NULL,
                displacement_type TEXT NOT NULL,  -- JSON array: BODY, VOLUME, BREAK_HIGH, BREAK_LOW
                score INTEGER NOT NULL,
                open_price REAL NOT NULL,
                high_price REAL NOT NULL,
                low_price REAL NOT NULL,
                close_price REAL NOT NULL,
                volume REAL NOT NULL,
                UNIQUE(symbol, timestamp)
            )
        """)

        # RETRACES TABLE
        # What happens AFTER a displacement
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS retraces (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                displacement_id INTEGER NOT NULL,
                symbol TEXT NOT NULL,
                direction TEXT NOT NULL,
                entry_price REAL NOT NULL,
                -- 5-minute window
                tf5_max_favorable_pct REAL,
                tf5_max_adverse_pct REAL,
                tf5_final_change_pct REAL,
                tf5_continuation INTEGER,  -- 0 or 1
                -- 15-minute window
                tf15_max_favorable_pct REAL,
                tf15_max_adverse_pct REAL,
                tf15_final_change_pct REAL,
                tf15_continuation INTEGER,
                -- 30-minute window
                tf30_max_favorable_pct REAL,
                tf30_max_adverse_pct REAL,
                tf30_final_change_pct REAL,
                tf30_continuation INTEGER,
                -- 60-minute window
                tf60_max_favorable_pct REAL,
                tf60_max_adverse_pct REAL,
                tf60_final_change_pct REAL,
                tf60_continuation INTEGER,
                FOREIGN KEY (displacement_id) REFERENCES displacements(id),
                UNIQUE(displacement_id)
            )
        """)

        # LIQUIDITY EVENTS TABLE
        # Stop hunts and sweeps at equal highs/lows
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS liquidity_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                symbol TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                event_type TEXT NOT NULL,  -- HIGH_SWEEP or LOW_SWEEP
                direction TEXT NOT NULL,   -- Expected reversal direction
                level REAL NOT NULL,
                sweep_pct REAL NOT NULL,
                touches INTEGER NOT NULL,  -- How many times level was tested
                close_price REAL NOT NULL,
                reversal_confirmed INTEGER,  -- NULL until analyzed, then 0 or 1
                max_move_after REAL,
                UNIQUE(symbol, timestamp, event_type)
            )
        """)

        # REGIMES TABLE
        # Market regime classifications over time
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS regimes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                symbol TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                regime TEXT NOT NULL,  -- TRENDING_UP, TRENDING_DOWN, RANGING, CHOP
                confidence REAL NOT NULL,
                reasons TEXT NOT NULL,  -- JSON array
                -- Direction analysis
                up_pct REAL,
                down_pct REAL,
                r_squared REAL,
                -- Volatility analysis
                atr_ratio REAL,
                volatility_state TEXT,
                -- Range analysis
                range_size_pct REAL,
                midpoint_crosses INTEGER,
                UNIQUE(symbol, timestamp)
            )
        """)

        # SIMULATED TRADES TABLE
        # Hypothetical trades for research (NOT real orders)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS simulated_trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                symbol TEXT NOT NULL,
                entry_timestamp TEXT NOT NULL,
                exit_timestamp TEXT,
                direction TEXT NOT NULL,  -- LONG or SHORT
                entry_price REAL NOT NULL,
                exit_price REAL,
                take_profit_price REAL,
                stop_loss_price REAL,
                -- Conditions at entry
                regime TEXT,
                displacement_id INTEGER,
                liquidity_event_id INTEGER,
                entry_reason TEXT NOT NULL,
                -- Outcome (filled after simulation)
                outcome TEXT,  -- WIN, LOSS, TIMEOUT, OPEN
                pnl_pct REAL,
                max_favorable_pct REAL,
                max_adverse_pct REAL,
                hold_duration_minutes INTEGER,
                exit_reason TEXT,
                -- Strategy parameters used
                strategy_params TEXT,  -- JSON
                FOREIGN KEY (displacement_id) REFERENCES displacements(id),
                FOREIGN KEY (liquidity_event_id) REFERENCES liquidity_events(id)
            )
        """)

        # EDGE METRICS TABLE
        # Aggregated edge statistics (for reporting)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS edge_metrics (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                metric_type TEXT NOT NULL,  -- displacement_continuation, sweep_reversal, etc.
                symbol TEXT,  -- NULL for overall
                condition TEXT NOT NULL,  -- Description of what was measured
                sample_size INTEGER NOT NULL,
                win_rate REAL,
                expectancy REAL,
                avg_win REAL,
                avg_loss REAL,
                profit_factor REAL,
                r_multiple REAL,
                confidence_level TEXT,  -- HIGH, MEDIUM, LOW, INSUFFICIENT
                notes TEXT
            )
        """)

        # Create indexes for faster queries
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_disp_symbol ON displacements(symbol)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_disp_timestamp ON displacements(timestamp)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_liq_symbol ON liquidity_events(symbol)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_regime_symbol ON regimes(symbol)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_sim_symbol ON simulated_trades(symbol)")

        # DISPLACEMENT CONTEXT TABLE (Phase 7)
        # The MISSING LAYER: WHERE, HOW FAST, and WHAT conditions
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS displacement_context (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                displacement_id INTEGER NOT NULL,
                symbol TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                direction TEXT NOT NULL,
                displacement_pct REAL,
                -- LOCATION CONTEXT (where in the range)
                location TEXT,  -- EXTREME_HIGH, EXTREME_LOW, MID_RANGE
                position_in_range REAL,  -- 0-1
                distance_from_high_pct REAL,
                distance_from_low_pct REAL,
                near_session_extreme INTEGER,  -- 0 or 1
                session TEXT,  -- asian, london, newyork
                -- VELOCITY CONTEXT (how fast)
                velocity REAL,  -- % per minute
                speed_type TEXT,  -- FAST_STOPRUN, SLOW_ACCEPTANCE, NORMAL
                candles_to_form INTEGER,
                avg_body_ratio REAL,
                -- FOLLOW-THROUGH CONTEXT (did it continue)
                followthrough TEXT,  -- SUCCEEDED, FAILED, COMPRESSING, PENDING
                new_extreme_made INTEGER,  -- 0 or 1
                compression_ratio REAL,
                failure_candles INTEGER,
                -- LIQUIDITY CONTEXT (stop run?)
                has_liquidity_sweep INTEGER,  -- 0 or 1
                sweep_type TEXT,
                sweep_before_disp INTEGER,
                time_to_sweep_minutes REAL,
                -- COMPOSITE SIGNALS
                fade_signal INTEGER,  -- 0 or 1
                continuation_signal INTEGER,
                signal_strength INTEGER,
                signal_reasons TEXT,  -- JSON array
                -- OUTCOME (filled later when retrace analyzed)
                actual_outcome TEXT,  -- CONTINUED, REVERSED, CHOPPY
                outcome_pnl_pct REAL,
                FOREIGN KEY (displacement_id) REFERENCES displacements(id),
                UNIQUE(displacement_id)
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_ctx_symbol ON displacement_context(symbol)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_ctx_location ON displacement_context(location)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_ctx_speed ON displacement_context(speed_type)")

        conn.commit()
        conn.close()

        logger.info(f"Research database initialized at {self.db_path}")

    # ========== DISPLACEMENT METHODS ==========

    def insert_displacement(self, data: Dict) -> int:
        """Insert a displacement event (append-only)"""
        conn = self._get_conn()
        cursor = conn.cursor()

        try:
            cursor.execute("""
                INSERT OR IGNORE INTO displacements (
                    symbol, timestamp, direction, displacement_pct,
                    body_ratio, volume_ratio, displacement_type, score,
                    open_price, high_price, low_price, close_price, volume
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                data['symbol'],
                str(data['timestamp']),
                data['direction'],
                data['displacement_pct'],
                data['body_ratio'],
                data['volume_ratio'],
                json.dumps(data.get('displacement_type', [])),
                data['score'],
                data['open'],
                data['high'],
                data['low'],
                data['close'],
                data['volume']
            ))
            conn.commit()
            return cursor.lastrowid
        finally:
            conn.close()

    def get_displacements(self, symbol: str = None, limit: int = 1000) -> List[Dict]:
        """Get displacement events"""
        conn = self._get_conn()
        cursor = conn.cursor()

        if symbol:
            cursor.execute(
                "SELECT * FROM displacements WHERE symbol = ? ORDER BY timestamp DESC LIMIT ?",
                (symbol, limit)
            )
        else:
            cursor.execute(
                "SELECT * FROM displacements ORDER BY timestamp DESC LIMIT ?",
                (limit,)
            )

        rows = cursor.fetchall()
        conn.close()
        return [dict(row) for row in rows]

    # ========== RETRACE METHODS ==========

    def insert_retrace(self, data: Dict) -> int:
        """Insert retrace analysis (append-only)"""
        conn = self._get_conn()
        cursor = conn.cursor()

        try:
            cursor.execute("""
                INSERT OR IGNORE INTO retraces (
                    displacement_id, symbol, direction, entry_price,
                    tf5_max_favorable_pct, tf5_max_adverse_pct, tf5_final_change_pct, tf5_continuation,
                    tf15_max_favorable_pct, tf15_max_adverse_pct, tf15_final_change_pct, tf15_continuation,
                    tf30_max_favorable_pct, tf30_max_adverse_pct, tf30_final_change_pct, tf30_continuation,
                    tf60_max_favorable_pct, tf60_max_adverse_pct, tf60_final_change_pct, tf60_continuation
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                data['displacement_id'],
                data['symbol'],
                data['direction'],
                data['entry_price'],
                data.get('tf5_max_favorable_pct'),
                data.get('tf5_max_adverse_pct'),
                data.get('tf5_final_change_pct'),
                data.get('tf5_continuation'),
                data.get('tf15_max_favorable_pct'),
                data.get('tf15_max_adverse_pct'),
                data.get('tf15_final_change_pct'),
                data.get('tf15_continuation'),
                data.get('tf30_max_favorable_pct'),
                data.get('tf30_max_adverse_pct'),
                data.get('tf30_final_change_pct'),
                data.get('tf30_continuation'),
                data.get('tf60_max_favorable_pct'),
                data.get('tf60_max_adverse_pct'),
                data.get('tf60_final_change_pct'),
                data.get('tf60_continuation')
            ))
            conn.commit()
            return cursor.lastrowid
        finally:
            conn.close()

    def get_retraces(self, symbol: str = None) -> List[Dict]:
        """Get retrace analyses"""
        conn = self._get_conn()
        cursor = conn.cursor()

        if symbol:
            cursor.execute("SELECT * FROM retraces WHERE symbol = ?", (symbol,))
        else:
            cursor.execute("SELECT * FROM retraces")

        rows = cursor.fetchall()
        conn.close()
        return [dict(row) for row in rows]

    # ========== DISPLACEMENT CONTEXT METHODS ==========

    def insert_displacement_context(self, data: Dict) -> int:
        """Insert displacement context analysis (append-only)"""
        conn = self._get_conn()
        cursor = conn.cursor()

        try:
            location = data.get('location', {})
            velocity = data.get('velocity', {})
            followthrough = data.get('followthrough', {})
            liquidity = data.get('liquidity', {})
            signals = data.get('signals', {})

            cursor.execute("""
                INSERT OR IGNORE INTO displacement_context (
                    displacement_id, symbol, timestamp, direction, displacement_pct,
                    location, position_in_range, distance_from_high_pct, distance_from_low_pct,
                    near_session_extreme, session,
                    velocity, speed_type, candles_to_form, avg_body_ratio,
                    followthrough, new_extreme_made, compression_ratio, failure_candles,
                    has_liquidity_sweep, sweep_type, sweep_before_disp, time_to_sweep_minutes,
                    fade_signal, continuation_signal, signal_strength, signal_reasons
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                data.get('displacement_id'),
                data.get('symbol'),
                data.get('timestamp'),
                data.get('direction'),
                data.get('displacement_pct'),
                location.get('location'),
                location.get('position_in_range'),
                location.get('distance_from_high_pct'),
                location.get('distance_from_low_pct'),
                1 if location.get('near_session_extreme') else 0,
                location.get('session'),
                velocity.get('velocity'),
                velocity.get('speed_type'),
                velocity.get('candles_to_form'),
                velocity.get('avg_body_ratio'),
                followthrough.get('followthrough'),
                1 if followthrough.get('new_extreme_made') else 0,
                followthrough.get('compression_ratio'),
                followthrough.get('failure_candles'),
                1 if liquidity.get('has_liquidity_sweep') else 0,
                liquidity.get('sweep_type'),
                1 if liquidity.get('sweep_before_disp') else 0,
                liquidity.get('time_to_sweep_minutes'),
                1 if signals.get('fade_signal') else 0,
                1 if signals.get('continuation_signal') else 0,
                signals.get('signal_strength', 0),
                json.dumps(signals.get('reasons', []))
            ))
            conn.commit()
            return cursor.lastrowid
        finally:
            conn.close()

    def get_displacement_contexts(self, symbol: str = None,
                                   location: str = None,
                                   speed_type: str = None) -> List[Dict]:
        """Get displacement contexts with optional filters"""
        conn = self._get_conn()
        cursor = conn.cursor()

        query = "SELECT * FROM displacement_context WHERE 1=1"
        params = []

        if symbol:
            query += " AND symbol = ?"
            params.append(symbol)
        if location:
            query += " AND location = ?"
            params.append(location)
        if speed_type:
            query += " AND speed_type = ?"
            params.append(speed_type)

        query += " ORDER BY created_at DESC"
        cursor.execute(query, params)

        rows = cursor.fetchall()
        conn.close()
        return [dict(row) for row in rows]

    def update_context_outcome(self, displacement_id: int, outcome: str, pnl_pct: float):
        """Update displacement context with actual outcome"""
        conn = self._get_conn()
        cursor = conn.cursor()

        cursor.execute("""
            UPDATE displacement_context
            SET actual_outcome = ?, outcome_pnl_pct = ?
            WHERE displacement_id = ? AND actual_outcome IS NULL
        """, (outcome, pnl_pct, displacement_id))

        conn.commit()
        conn.close()

    # ========== LIQUIDITY EVENTS METHODS ==========

    def insert_liquidity_event(self, data: Dict) -> int:
        """Insert liquidity sweep event (append-only)"""
        conn = self._get_conn()
        cursor = conn.cursor()

        try:
            cursor.execute("""
                INSERT OR IGNORE INTO liquidity_events (
                    symbol, timestamp, event_type, direction,
                    level, sweep_pct, touches, close_price
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                data['symbol'],
                str(data['timestamp']),
                data['type'],
                data['direction'],
                data['level'],
                data['sweep_pct'],
                data['touches'],
                data['close']
            ))
            conn.commit()
            return cursor.lastrowid
        finally:
            conn.close()

    def update_liquidity_outcome(self, event_id: int, reversal_confirmed: bool, max_move: float):
        """Update liquidity event with outcome (only field we allow updating)"""
        conn = self._get_conn()
        cursor = conn.cursor()

        cursor.execute("""
            UPDATE liquidity_events
            SET reversal_confirmed = ?, max_move_after = ?
            WHERE id = ? AND reversal_confirmed IS NULL
        """, (1 if reversal_confirmed else 0, max_move, event_id))

        conn.commit()
        conn.close()

    def get_liquidity_events(self, symbol: str = None) -> List[Dict]:
        """Get liquidity events"""
        conn = self._get_conn()
        cursor = conn.cursor()

        if symbol:
            cursor.execute("SELECT * FROM liquidity_events WHERE symbol = ?", (symbol,))
        else:
            cursor.execute("SELECT * FROM liquidity_events")

        rows = cursor.fetchall()
        conn.close()
        return [dict(row) for row in rows]

    # ========== REGIME METHODS ==========

    def insert_regime(self, data: Dict) -> int:
        """Insert regime classification (append-only)"""
        conn = self._get_conn()
        cursor = conn.cursor()

        try:
            cursor.execute("""
                INSERT OR IGNORE INTO regimes (
                    symbol, timestamp, regime, confidence, reasons,
                    up_pct, down_pct, r_squared,
                    atr_ratio, volatility_state,
                    range_size_pct, midpoint_crosses
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                data['symbol'],
                str(data['timestamp']),
                data['regime'],
                data['confidence'],
                json.dumps(data.get('reasons', [])),
                data.get('direction_analysis', {}).get('up_pct'),
                data.get('direction_analysis', {}).get('down_pct'),
                data.get('direction_analysis', {}).get('r_squared'),
                data.get('volatility_analysis', {}).get('atr_ratio'),
                data.get('volatility_analysis', {}).get('volatility_state'),
                data.get('range_analysis', {}).get('range_size_pct'),
                data.get('range_analysis', {}).get('midpoint_crosses')
            ))
            conn.commit()
            return cursor.lastrowid
        finally:
            conn.close()

    def get_regimes(self, symbol: str = None, limit: int = 1000) -> List[Dict]:
        """Get regime history"""
        conn = self._get_conn()
        cursor = conn.cursor()

        if symbol:
            cursor.execute(
                "SELECT * FROM regimes WHERE symbol = ? ORDER BY timestamp DESC LIMIT ?",
                (symbol, limit)
            )
        else:
            cursor.execute(
                "SELECT * FROM regimes ORDER BY timestamp DESC LIMIT ?",
                (limit,)
            )

        rows = cursor.fetchall()
        conn.close()
        return [dict(row) for row in rows]

    # ========== SIMULATED TRADES METHODS ==========

    def insert_simulated_trade(self, data: Dict) -> int:
        """Insert simulated trade (append-only)"""
        conn = self._get_conn()
        cursor = conn.cursor()

        try:
            cursor.execute("""
                INSERT INTO simulated_trades (
                    symbol, entry_timestamp, direction, entry_price,
                    take_profit_price, stop_loss_price,
                    regime, displacement_id, liquidity_event_id,
                    entry_reason, strategy_params
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                data['symbol'],
                str(data['entry_timestamp']),
                data['direction'],
                data['entry_price'],
                data.get('take_profit_price'),
                data.get('stop_loss_price'),
                data.get('regime'),
                data.get('displacement_id'),
                data.get('liquidity_event_id'),
                data['entry_reason'],
                json.dumps(data.get('strategy_params', {}))
            ))
            conn.commit()
            return cursor.lastrowid
        finally:
            conn.close()

    def update_simulated_trade_outcome(self, trade_id: int, data: Dict):
        """Update simulated trade with outcome"""
        conn = self._get_conn()
        cursor = conn.cursor()

        cursor.execute("""
            UPDATE simulated_trades SET
                exit_timestamp = ?,
                exit_price = ?,
                outcome = ?,
                pnl_pct = ?,
                max_favorable_pct = ?,
                max_adverse_pct = ?,
                hold_duration_minutes = ?,
                exit_reason = ?
            WHERE id = ?
        """, (
            str(data.get('exit_timestamp', '')),
            data.get('exit_price'),
            data.get('outcome'),
            data.get('pnl_pct'),
            data.get('max_favorable_pct'),
            data.get('max_adverse_pct'),
            data.get('hold_duration_minutes'),
            data.get('exit_reason'),
            trade_id
        ))

        conn.commit()
        conn.close()

    def get_simulated_trades(self, symbol: str = None, outcome: str = None) -> List[Dict]:
        """Get simulated trades"""
        conn = self._get_conn()
        cursor = conn.cursor()

        query = "SELECT * FROM simulated_trades WHERE 1=1"
        params = []

        if symbol:
            query += " AND symbol = ?"
            params.append(symbol)
        if outcome:
            query += " AND outcome = ?"
            params.append(outcome)

        query += " ORDER BY entry_timestamp DESC"

        cursor.execute(query, params)
        rows = cursor.fetchall()
        conn.close()
        return [dict(row) for row in rows]

    # ========== EDGE METRICS METHODS ==========

    def insert_edge_metric(self, data: Dict) -> int:
        """Insert edge metric calculation (append-only)"""
        conn = self._get_conn()
        cursor = conn.cursor()

        try:
            cursor.execute("""
                INSERT INTO edge_metrics (
                    metric_type, symbol, condition, sample_size,
                    win_rate, expectancy, avg_win, avg_loss,
                    profit_factor, r_multiple, confidence_level, notes
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                data['metric_type'],
                data.get('symbol'),
                data['condition'],
                data['sample_size'],
                data.get('win_rate'),
                data.get('expectancy'),
                data.get('avg_win'),
                data.get('avg_loss'),
                data.get('profit_factor'),
                data.get('r_multiple'),
                data.get('confidence_level', 'INSUFFICIENT'),
                data.get('notes')
            ))
            conn.commit()
            return cursor.lastrowid
        finally:
            conn.close()

    def get_edge_metrics(self, metric_type: str = None) -> List[Dict]:
        """Get edge metrics"""
        conn = self._get_conn()
        cursor = conn.cursor()

        if metric_type:
            cursor.execute(
                "SELECT * FROM edge_metrics WHERE metric_type = ? ORDER BY created_at DESC",
                (metric_type,)
            )
        else:
            cursor.execute("SELECT * FROM edge_metrics ORDER BY created_at DESC")

        rows = cursor.fetchall()
        conn.close()
        return [dict(row) for row in rows]

    # ========== STATISTICS METHODS ==========

    def get_table_counts(self) -> Dict[str, int]:
        """Get row counts for all tables"""
        conn = self._get_conn()
        cursor = conn.cursor()

        tables = ['displacements', 'retraces', 'liquidity_events', 'regimes',
                  'simulated_trades', 'edge_metrics']

        counts = {}
        for table in tables:
            cursor.execute(f"SELECT COUNT(*) FROM {table}")
            counts[table] = cursor.fetchone()[0]

        conn.close()
        return counts

    def print_summary(self):
        """Print database summary"""
        counts = self.get_table_counts()

        print("\n" + "="*50)
        print("RESEARCH DATABASE SUMMARY")
        print("="*50)
        print(f"Database: {self.db_path}")
        print("\nTable row counts:")
        for table, count in counts.items():
            print(f"  {table}: {count}")


# Singleton instance
_db_instance = None

def get_research_db() -> ResearchDB:
    """Get singleton database instance"""
    global _db_instance
    if _db_instance is None:
        _db_instance = ResearchDB()
    return _db_instance


if __name__ == "__main__":
    # Test database creation
    db = ResearchDB()
    db.print_summary()
    print("\nDatabase initialized successfully!")
