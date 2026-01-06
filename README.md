# ScalperBot

Professional MEXC Spot trading bot with Telegram control.

## Features

- **Telegram Control**: Start/stop, watchlist management, signals, position monitoring
- **Signal Scoring**: Momentum, volume, RSI, external signals combined into score
- **Safety Filters**: Spread guard, volume guard, depth check for low-liquidity safety
- **Dynamic Risk**: Stop loss = max(base, 2x spread, 0.8x ATR)
- **Candidate Selection**: Picks best opportunity when multiple signals
- **Limit Orders**: Safe execution with timeout/cancel for illiquid markets
- **DRY_RUN Mode**: Paper trading by default

## Quick Start

### 1. Clone and Setup

```bash
git clone https://github.com/armcoincrypto/scalperbot.git
cd scalperbot

# Create virtual environment
python3 -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

### 2. Configure

```bash
# Copy example config
cp .env.example .env

# Edit with your credentials
nano .env
```

Required settings:
- `MEXC_API_KEY` and `MEXC_API_SECRET` - Get from MEXC
- `TELEGRAM_BOT_TOKEN` - Create bot via @BotFather
- `TELEGRAM_CHAT_ID` - Get via @userinfobot
- `TELEGRAM_ADMIN_IDS` - Your Telegram user ID

### 3. Syntax Check

```bash
python -m py_compile scalperbot/*.py scalperbot/**/*.py
```

### 4. Run

```bash
# Dry run mode (default, safe)
python -m scalperbot.main

# Or with options
python -m scalperbot.main --dry-run
python -m scalperbot.main --no-telegram
python -m scalperbot.main --auto-start
```

## Telegram Commands

| Command | Description |
|---------|-------------|
| `/start` | Show help |
| `/run` | Start trading engine |
| `/stop` | Stop trading engine |
| `/status` | Show current status |
| `/watchlist` | Show current watchlist |
| `/setcoins BTCUSDT,ETHUSDT` | Update watchlist |
| `/signal BTCUSDT +2` | Add external signal |
| `/positions` | Show open positions |
| `/pnl` | Daily PnL summary |
| `/debug` | Debug info (no secrets) |

## Project Structure

```
scalperbot/
  __init__.py
  main.py           # Entry point
  config.py         # Pydantic settings
  log.py            # Logging setup
  mexc/
    rest.py         # MEXC API client
    signing.py      # Request signing
    models.py       # Data models
  core/
    engine.py       # Main trading loop
    indicators.py   # Technical indicators
    scoring.py      # Signal scoring
    filters.py      # Safety filters
    risk.py         # Risk management
    selector.py     # Candidate selection
  storage/
    db.py           # SQLite connection
    schema.py       # Database schema
    repo.py         # Data repositories
  telegram/
    bot.py          # Telegram bot
    handlers.py     # Command handlers
```

## Deploy with systemd

Create service file:

```bash
sudo nano /etc/systemd/system/scalperbot.service
```

```ini
[Unit]
Description=ScalperBot Trading Bot
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/home/user/scalperbot
ExecStart=/home/user/scalperbot/venv/bin/python -m scalperbot.main
Restart=always
RestartSec=10
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
```

Enable and start:

```bash
sudo systemctl daemon-reload
sudo systemctl enable scalperbot
sudo systemctl start scalperbot

# Check status
sudo systemctl status scalperbot
journalctl -u scalperbot -f
```

## Configuration Reference

| Variable | Default | Description |
|----------|---------|-------------|
| `DRY_RUN` | true | Paper trading mode |
| `WATCHLIST` | BTCUSDT,ETHUSDT | Symbols to trade |
| `POSITION_SIZE_USDT` | 50 | Position size in USDT |
| `MAX_OPEN_POSITIONS` | 3 | Max concurrent positions |
| `BUY_PCT_TRIGGER` | 0.5 | Min momentum % for entry |
| `BUY_SCORE_MIN` | 2.0 | Min score for entry |
| `BASE_SL_PCT` | 2.0 | Base stop loss % |
| `TAKE_PROFIT_PCT` | 3.0 | Take profit % |
| `MAX_SPREAD_PCT` | 0.5 | Max allowed spread |
| `COOLDOWN_SEC` | 300 | Cooldown after trade |

## Safety Features

1. **DRY_RUN by default** - No real trades until you enable live mode
2. **Spread filter** - Rejects trades with spread > MAX_SPREAD_PCT
3. **Volume filter** - Requires minimum 24h volume
4. **Dynamic SL** - Adjusts for spread and volatility
5. **Cooldown** - Prevents overtrading same symbol
6. **Limit orders** - Uses limit orders with timeout for safety

## License

MIT
