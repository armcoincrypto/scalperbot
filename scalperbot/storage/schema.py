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

-- ============================================================
-- RESEARCH/ANALYTICS TABLES (for strategy improvement)
-- ============================================================

-- Ticks table: every poll snapshot for later analysis
CREATE TABLE IF NOT EXISTS ticks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    timestamp TEXT NOT NULL,

    -- Price data
    price REAL NOT NULL,
    bid REAL,
    ask REAL,
    spread_pct REAL,

    -- Indicators
    momentum_2m REAL,
    momentum_5m REAL,
    volume_ratio REAL,
    rsi REAL,
    atr REAL,
    atr_pct REAL,

    -- Scoring
    score_momentum REAL,
    score_volume REAL,
    score_rsi REAL,
    score_external REAL,
    score_total REAL,

    -- Filters result
    filter_spread_ok INTEGER,
    filter_volume_ok INTEGER,
    filter_depth_ok INTEGER,
    filters_passed INTEGER,

    -- Decision
    is_signal INTEGER NOT NULL DEFAULT 0,
    decision TEXT,  -- SKIP, COOLDOWN, MAX_POS, FILTERED, CANDIDATE, ENTRY
    decision_reason TEXT,

    -- Strategy version for comparison
    strategy_version_id INTEGER,

    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Outcomes table: forward returns for each tick (filled async)
CREATE TABLE IF NOT EXISTS outcomes (
    tick_id INTEGER PRIMARY KEY,

    -- Forward returns (percentage)
    return_1m REAL,   -- 1 minute forward
    return_5m REAL,   -- 5 minute forward
    return_15m REAL,  -- 15 minute forward
    return_30m REAL,  -- 30 minute forward

    -- MFE/MAE (Maximum Favorable/Adverse Excursion)
    mfe_5m REAL,      -- Max gain in 5 min window
    mae_5m REAL,      -- Max drawdown in 5 min window
    mfe_15m REAL,
    mae_15m REAL,

    -- Would-hit analysis
    tp_hit_5m INTEGER,   -- Would TP (3%) be hit in 5m?
    sl_hit_5m INTEGER,   -- Would SL (2%) be hit in 5m?
    tp_hit_15m INTEGER,
    sl_hit_15m INTEGER,

    -- Which hit first?
    first_hit TEXT,      -- TP, SL, or NEITHER
    first_hit_time_sec INTEGER,

    labeled_at TEXT,
    FOREIGN KEY (tick_id) REFERENCES ticks(id)
);

-- Strategy versions: parameter snapshots for A/B comparison
CREATE TABLE IF NOT EXISTS strategy_versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    description TEXT,

    -- Core parameters (JSON for flexibility)
    parameters TEXT NOT NULL,  -- JSON blob of all settings

    -- Key params extracted for easy querying
    buy_pct_trigger REAL,
    buy_score_min REAL,
    base_sl_pct REAL,
    take_profit_pct REAL,
    max_spread_pct REAL,

    -- Performance stats (updated periodically)
    total_ticks INTEGER DEFAULT 0,
    total_signals INTEGER DEFAULT 0,
    total_entries INTEGER DEFAULT 0,
    simulated_pnl REAL DEFAULT 0,
    win_rate REAL,
    avg_return REAL,
    sharpe_ratio REAL,

    is_active INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Simulation results: track DRY_RUN performance with realistic modeling
CREATE TABLE IF NOT EXISTS simulated_trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tick_id INTEGER,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,

    -- Intended vs simulated execution
    intended_price REAL NOT NULL,
    simulated_price REAL NOT NULL,  -- After slippage
    slippage_pct REAL,

    -- Fees
    fee_rate REAL NOT NULL,
    fee_amount REAL NOT NULL,

    -- Fill simulation
    intended_qty REAL NOT NULL,
    filled_qty REAL NOT NULL,
    fill_ratio REAL NOT NULL,  -- 1.0 = full fill

    -- Net result
    net_cost REAL NOT NULL,  -- price * qty + fees + slippage

    strategy_version_id INTEGER,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (tick_id) REFERENCES ticks(id)
);

-- Create indexes
CREATE INDEX IF NOT EXISTS idx_positions_symbol ON positions(symbol);
CREATE INDEX IF NOT EXISTS idx_positions_status ON positions(status);
CREATE INDEX IF NOT EXISTS idx_trades_symbol ON trades(symbol);
CREATE INDEX IF NOT EXISTS idx_signals_symbol ON signals(symbol);
CREATE INDEX IF NOT EXISTS idx_signals_expires ON signals(expires_at);

-- Research indexes
CREATE INDEX IF NOT EXISTS idx_ticks_symbol ON ticks(symbol);
CREATE INDEX IF NOT EXISTS idx_ticks_timestamp ON ticks(timestamp);
CREATE INDEX IF NOT EXISTS idx_ticks_decision ON ticks(decision);
CREATE INDEX IF NOT EXISTS idx_ticks_is_signal ON ticks(is_signal);
CREATE INDEX IF NOT EXISTS idx_ticks_strategy ON ticks(strategy_version_id);
CREATE INDEX IF NOT EXISTS idx_outcomes_labeled ON outcomes(labeled_at);
CREATE INDEX IF NOT EXISTS idx_simtrades_symbol ON simulated_trades(symbol);
"""
