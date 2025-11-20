# 🤖 ScalperBot

Cryptocurrency momentum breakout trading bot with 4-filter GREEN strategy.

## 📊 Features

- **4-Filter GREEN Strategy**:
  - GREEN 1: Trend check (5m price > price 2 bars ago)
  - GREEN 2: Bollinger Band expansion (volatility increasing)
  - GREEN 3: Volume surge (volume Z-score > threshold) - *Optional*
  - GREEN 4: Price breakout (price > 10-period high + buffer)

- **Data Feed**:
  - REST API polling every 10s
  - 1m candle accumulation with automatic resampling to 5m
  - Historical candle initialization for fast warmup

- **Risk Management**:
  - Daily loss limit (-3% circuit breaker)
  - Position size limits
  - Order validation and quantization

- **Order Execution**:
  - Market and limit orders
  - Maker order support (inside spread)
  - MIN_NOTIONAL validation

- **Monitoring**:
  - SQLite trade logging
  - Daily PnL tracking
  - Detailed strategy filter logging

## 🚀 Quick Start

### Local Testing

```bash
# 1. Create .env file
cp scalperbot/.env.example .env
nano .env  # Add your MEXC API keys

# 2. Install dependencies
python3 -m venv .venv
source .venv/bin/activate
pip install -r scalperbot/requirements.txt

# 3. Run bot (DRY_RUN mode)
python -m scalperbot.main

# Or use the start script
./scalperbot/start.sh
```

### VPS Deployment

```bash
# Deploy to VPS (root@207.180.212.142)
./scalperbot/deploy.sh

# On the server:
ssh root@207.180.212.142
cd /root/scalperbot

# Edit .env with your API keys
nano .env

# Start as systemd service
sudo systemctl start scalperbot
sudo systemctl enable scalperbot  # Auto-start on boot

# Check logs
tail -f bot.log
journalctl -u scalperbot -f
```

## ⚙️ Configuration

Edit `.env` file:

```env
# REQUIRED
MEXC_API_KEY=your_api_key
MEXC_API_SECRET=your_secret

# Trading mode
DRY_RUN=true  # Set to false for live trading

# Strategy parameters
GREEN3_ENABLED=false  # Enable/disable volume filter
POSITION_SIZE_USD=100.0
DAILY_LOSS_LIMIT_PCT=3.0
```

## 📁 Architecture

```
scalperbot/
├── main.py                 # Entry point, trading loop
├── config.py              # Pydantic settings
├── db.py                  # SQLite trade logging
├── strategies/
│   └── momentum_breakout.py   # GREEN 1-4 strategy
├── datafeed/
│   ├── rest_poller.py     # Market data poller
│   ├── candle_store.py    # OHLCV storage
│   └── orderbook.py       # Bid/ask tracking
├── exchanges/
│   └── adapter.py         # MEXC CCXT wrapper
├── exec/
│   └── router.py          # Order execution
├── ops/
│   └── pos_size.py        # Position sizing
└── risk/
    └── breaker.py         # Daily loss limit
```

## 📊 Database

SQLite database at `scalperbot/trades.db`:

```sql
-- View recent trades
SELECT * FROM trades ORDER BY created_at DESC LIMIT 10;

-- Check daily PnL
SELECT * FROM daily_pnl ORDER BY date DESC;
```

## 🔍 Monitoring

```bash
# Follow logs
tail -f bot.log | grep -E "GREEN|SIGNAL|Order"

# Check strategy output
tail -100 bot.log | grep "GREEN"

# Service status
sudo systemctl status scalperbot

# Restart service
sudo systemctl restart scalperbot
```

## 🎯 Next Steps (Prioritized)

### Priority 1: Strategy Tuning
- Analyze filter pass rates in logs
- Adjust GREEN 2 threshold (BB expansion too strict)
- Test with relaxed parameters
- Add detailed metric logging

### Priority 2: Backtesting
- Create `backtest/engine.py`
- Fetch 30 days of historical data
- Calculate win rate, profit factor, drawdown
- Optimize parameters

### Priority 3: Order Execution
- Switch to maker orders (currently uses market)
- Add stop-loss tracking
- Implement position closing logic

### Priority 4: Monitoring Dashboard
- Telegram alerts for trades
- Daily summary reports
- Web dashboard (optional)

## 📝 Logs Example

```
2025-11-19 12:00:00 - INFO - 🔄 Strategy Cycle #1
2025-11-19 12:00:00 - INFO - ============================================================
2025-11-19 12:00:00 - INFO - 📊 BTC/USDT Strategy Check:
2025-11-19 12:00:00 - INFO -   GREEN 1: Price=43500.00, 2bars_ago=43450.00, trending=UP ✅
2025-11-19 12:00:00 - INFO -   GREEN 2: BB_width=0.002345, prev=0.002123, expanding=True ✅
2025-11-19 12:00:00 - INFO -   GREEN 3: DISABLED (bypassed for testing)
2025-11-19 12:00:00 - INFO -   GREEN 4: Price=43500.00, breakout_level=43480.00, breakout=YES ✅
2025-11-19 12:00:00 - INFO - 🟢 SIGNAL GENERATED: BTC/USDT BUY @ 43500.0000
```

## ⚠️ Important Notes

- **Always test in DRY_RUN mode first**
- Start with small position sizes in LIVE mode
- Monitor logs closely for the first 24 hours
- GREEN 3 (volume filter) is disabled by default due to low market volume
- Strategy is conservative - may generate 0-3 signals per day

## 📞 Support

- GitHub: https://github.com/Armcoincrypto/scalperbot
- Server: root@207.180.212.142:/root/scalperbot

---

**Status**: ✅ Ready for Testing
**Mode**: DRY_RUN (default)
**Last Updated**: November 2024
