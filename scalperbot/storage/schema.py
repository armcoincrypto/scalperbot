"""
Database schema definitions.
"""

SCHEMA_SQL = """
-- Positions table: tracks open positions
CREATE TABLE IF NOT EXISTS positions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,  -- BUY only for spot
    quantity REAL NOT NULL,
    entry_price REAL NOT NULL,
    current_price REAL,
    stop_loss REAL,
    take_profit REAL,
    trailing_stop REAL,
    peak_price REAL,  -- For trailing stop
    status TEXT NOT NULL DEFAULT 'OPEN',  -- OPEN, CLOSED
    entry_order_id TEXT,
    exit_order_id TEXT,
    entry_time TEXT NOT NULL,
    exit_time TEXT,
    exit_price REAL,
    realized_pnl REAL,
    exit_reason TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Trades table: history of all executed trades
CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    position_id INTEGER,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,  -- BUY or SELL
    order_type TEXT NOT NULL,  -- MARKET, LIMIT
    quantity REAL NOT NULL,
    price REAL NOT NULL,
    quote_amount REAL NOT NULL,
    order_id TEXT,
    status TEXT NOT NULL,  -- FILLED, CANCELED, etc.
    is_entry INTEGER NOT NULL DEFAULT 0,  -- 1 if entry trade
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (position_id) REFERENCES positions(id)
);

-- External signals table: signals from Telegram or other sources
CREATE TABLE IF NOT EXISTS signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    weight REAL NOT NULL,  -- Positive = bullish, negative = bearish
    source TEXT NOT NULL DEFAULT 'telegram',
    expires_at TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Cooldowns table: per-symbol cooldown tracking
CREATE TABLE IF NOT EXISTS cooldowns (
    symbol TEXT PRIMARY KEY,
    expires_at TEXT NOT NULL
);

-- Daily stats table
CREATE TABLE IF NOT EXISTS daily_stats (
    date TEXT PRIMARY KEY,
    total_trades INTEGER NOT NULL DEFAULT 0,
    winning_trades INTEGER NOT NULL DEFAULT 0,
    losing_trades INTEGER NOT NULL DEFAULT 0,
    total_pnl REAL NOT NULL DEFAULT 0,
    volume_traded REAL NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Create indexes
CREATE INDEX IF NOT EXISTS idx_positions_symbol ON positions(symbol);
CREATE INDEX IF NOT EXISTS idx_positions_status ON positions(status);
CREATE INDEX IF NOT EXISTS idx_trades_symbol ON trades(symbol);
CREATE INDEX IF NOT EXISTS idx_signals_symbol ON signals(symbol);
CREATE INDEX IF NOT EXISTS idx_signals_expires ON signals(expires_at);
"""
