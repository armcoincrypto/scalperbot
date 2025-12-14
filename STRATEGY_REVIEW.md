# ScalperBot Strategy Review Document
## For Expert Analysis

**Generated:** December 14, 2025
**Bot Version:** 2.1
**Exchange:** MEXC (Spot Trading)
**Mode:** LIVE (Real Money)

---

## 1. EXECUTIVE SUMMARY

### Current Status
- **Running Time:** ~48 hours
- **Total Trades:** 1
- **Win/Loss:** 0 wins / 1 loss
- **Total PnL:** -$2 to -$3
- **Current Balance:** $369.18 USDT

### Problem Statement
The bot is generating very few trades. In 48+ hours of running, only 1 trade was executed (which was a loss). We need expert review to determine if:
1. The strategy is too restrictive
2. The parameters need adjustment
3. A different strategy would be more suitable

---

## 2. STRATEGY OVERVIEW

### Strategy Name: Smart Breakout (RSI Bounce)

### Core Concept
Buy when price bounces from oversold conditions with momentum confirmation.

### Entry Logic (ALL 5 filters must pass simultaneously)

```
┌─────────────────────────────────────────────────────────────┐
│  FILTER 1: HIGHER TIMEFRAME TREND (1H Chart)                │
│  ✓ Price >= 99% of EMA20 (allow 1% below)                   │
│  ✓ RSI < 80 (not extremely overbought)                      │
│  Timeframe: 1 Hour                                          │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│  FILTER 2: RSI MOMENTUM (5m Chart)                          │
│  ✓ RSI between 35-75 (not at extremes)                      │
│  ✓ RSI is RISING vs 3 bars ago                              │
│  Timeframe: 5 Minutes                                       │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│  FILTER 3: VOLUME CONFIRMATION (5m Chart)                   │
│  ✓ Volume > 0.8x 20-period average (recently lowered from   │
│    1.2x due to lack of trades)                              │
│  ✓ Current candle must be GREEN (close > open)              │
│  Timeframe: 5 Minutes                                       │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│  FILTER 4: BREAKOUT CONFIRMATION (5m Chart)                 │
│  ✓ Previous candle closed above 20-bar resistance           │
│  ✓ Current candle low > resistance * 0.998 (holds above)    │
│  ✓ Current close > resistance                               │
│  Timeframe: 5 Minutes                                       │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│  FILTER 5: NOT EXTENDED (5m Chart)                          │
│  ✓ Price < EMA20 + 2*ATR (not overextended)                 │
│  Timeframe: 5 Minutes                                       │
└─────────────────────────────────────────────────────────────┘
```

### Exit Logic

| Exit Type | Condition |
|-----------|-----------|
| Take Profit | Price >= Entry + 2.0 * ATR |
| Stop Loss | Price <= Entry - 0.8 * ATR |
| Trailing Stop | Activates at +0.9% profit, trails 0.15% behind peak |
| Time Exit | Position held > 6 hours |

### Risk:Reward Ratio
- TP = 2.0 ATR
- SL = 0.8 ATR
- **R:R = 2.5:1**

---

## 3. TRADING PARAMETERS

### Current Configuration

```
Trading Pairs:        BNB/USDT, XRP/USDT, XLM/USDT
Position Size:        $50 USD per trade
Max Positions:        3 simultaneous
Strategy Interval:    60 seconds (check every minute)
Data Poll Interval:   10 seconds

RSI Period:           14
RSI Oversold:         35
RSI Overbought:       75
EMA Period:           20
ATR Period:           14
Volume Multiplier:    0.8x average (was 1.2x)
Breakout Lookback:    20 bars
HTF RSI Limit:        80

Daily Loss Limit:     3% of equity
Trailing Enabled:     Yes
Trail Start:          0.9% profit
Trail Offset:         0.15%
Max Hold Time:        6 hours
```

---

## 4. RECENT PERFORMANCE DATA

### Filter Pass Rates (Last 48 hours)

| Filter | Times Checked | Times Passed | Pass Rate |
|--------|---------------|--------------|-----------|
| 1. HTF Trend | ~4000+ | ~2000 | ~50% |
| 2. RSI Momentum | ~2000 | ~1000 | ~50% |
| 3. Volume | ~1000 | ~10-20 | ~1-2% |
| 4. Breakout | ~10-20 | 2-3 | ~15% |
| 5. Not Extended | ~2-3 | 2 | ~66% |
| **All 5 Filters** | ~4000+ | **2** | **0.05%** |

### Rejection Breakdown (from logs)
```
Volume does not confirm direction:  ~70% of rejections
Higher timeframe trend not bullish: ~20% of rejections
RSI conditions not met:             ~10% of rejections
```

### The ONE Trade Executed

| Field | Value |
|-------|-------|
| Date | December 14, 2025, 07:36 UTC |
| Pair | XRP/USDT |
| Entry | $2.0177 - $2.0582 |
| Quantity | 24.78 XRP |
| Size | $50 |
| Stop Loss | $2.0592 |
| Exit | ~$1.98 (SL hit) |
| PnL | -$2 to -$3 (loss) |
| Hold Time | ~15 hours |
| Exit Reason | Stop Loss |

---

## 5. ISSUES IDENTIFIED

### Issue 1: Too Few Trades
- Strategy requires ALL 5 conditions simultaneously
- In 48 hours, only 2 signals passed all filters
- Volume filter was the main bottleneck (originally 1.2x, lowered to 0.8x)

### Issue 2: Volume Filter Too Restrictive
- Required: Volume > 1.2x average AND green candle
- Reality: Volume on BNB/XRP/XLM often 0.01x - 0.1x during quiet hours
- Even during active hours, rarely exceeds 1.2x

### Issue 3: The One Trade Lost
- Entry was made during what appeared to be a bounce
- But price continued falling, hitting stop loss
- Possible false signal or bad market conditions

### Issue 4: Timing Mismatch
- Backtest used 1-minute candles for entry signals
- Live bot uses 5-minute candles for entry signals
- This could explain different results

---

## 6. BACKTEST vs REALITY

### Backtest Results (30 days historical data)

| Pair | Profit Factor | Monthly PnL | Win Rate |
|------|---------------|-------------|----------|
| XLM/USDT | 1.12 | +$21 | ~58% |
| BNB/USDT | 1.14 | +$19 | ~60% |
| XRP/USDT | 1.12 | +$17 | ~60% |
| **Total** | 1.12 | **+$57** | ~59% |

### Reality (2 days live)

| Metric | Expected | Actual |
|--------|----------|--------|
| Trades | ~6-12 | 1 |
| Win Rate | ~60% | 0% |
| PnL | +$4-8 | -$2-3 |

---

## 7. QUESTIONS FOR EXPERT

1. **Is the 5-filter approach too restrictive?**
   - Should we reduce to 3-4 filters for more trades?

2. **Is the volume filter appropriate?**
   - 0.8x average still might be too high
   - Should we remove the "green candle" requirement?

3. **Is RSI bounce the right strategy for these pairs?**
   - BNB, XRP, XLM are relatively stable altcoins
   - Maybe momentum/trend following would work better?

4. **Timeframe mismatch:**
   - Backtest used 1m candles
   - Live uses 5m candles
   - Should we switch to 1m for live trading?

5. **Should we increase position size or number of pairs?**
   - Currently $50 per trade
   - Only 3 pairs being monitored

6. **Is the R:R ratio appropriate?**
   - TP = 2.0 ATR, SL = 0.8 ATR
   - The one trade that executed hit SL
   - Maybe SL is too tight?

---

## 8. CODE LOCATIONS

For detailed review:

| Component | File Path |
|-----------|-----------|
| Main Bot | `/home/user/scalperbot/main.py` |
| Smart Breakout Strategy | `/home/user/scalperbot/strategies/smart_breakout.py` |
| Configuration | `/home/user/scalperbot/config.py` |
| Environment Variables | `/home/user/scalperbot/.env` |
| Position Manager | `/home/user/scalperbot/ops/position_manager.py` |
| Position Sizing | `/home/user/scalperbot/ops/pos_size.py` |
| Backtest Tool | `/home/user/scalperbot/tools/backtest.py` |

---

## 9. SUGGESTED ALTERNATIVES

### Option A: Relax Current Strategy
- Lower volume to 0.5x
- Remove green candle requirement
- Widen RSI range to 30-80

### Option B: Simpler Trend Strategy
- Remove breakout confirmation
- Just trade RSI bounce + trend direction
- More trades, possibly lower win rate

### Option C: Different Strategy Entirely
- Pure momentum (buy breakouts)
- Mean reversion (buy dips)
- Grid trading

---

## 10. HOW TO RUN BACKTEST

The expert can test different parameters:

```bash
cd /home/user/scalperbot

# Basic backtest
python tools/backtest.py --symbol BNB/USDT --days 30

# With optimization (tests multiple parameters)
python tools/backtest.py --symbol BNB/USDT --days 30 --optimize

# Extended optimization (tests SL multipliers)
python tools/backtest.py --symbol BNB/USDT --days 30 --extended
```

---

## 11. CONTACT & ACCESS

- **VPS IP:** 89.213.0.106
- **Bot Path:** /home/user/scalperbot/
- **Logs:** /home/user/scalperbot/bot.log
- **Database:** /home/user/scalperbot/scalperbot/trades.db

---

*Document prepared for expert strategy review. Please provide feedback on recommended changes.*
