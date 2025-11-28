# 🎯 ScalperBot Monitoring & Management Guide

Quick reference for managing your 48-hour relaxed session experiment.

---

## 📚 Documentation Files

| File | Purpose | When to Use |
|------|---------|-------------|
| **ROLLBACK_PLAN.md** | Emergency rollback procedure | When signals degrade or losses occur |
| **DAILY_REPORT_TEMPLATE.md** | Morning health check template | Every morning for 48 hours |
| **analyze_signals.sh** | Automated signal analysis | After first 3-5 signals appear |
| **BUGS_AND_IMPROVEMENTS.md** | Technical debt and issues | When planning improvements |
| **BACKUP_LOCATIONS.md** | Backup and recovery info | If restore needed |

---

## ⚡ Quick Commands (Copy-Paste Ready)

### Morning Health Check (30 seconds)
```bash
ssh root@207.180.212.142
cd /home/user/scalperbot

# One-liner status
echo "=== BOT HEALTH ===" && \
systemctl is-active scalperbot && \
echo "Signals today: $(grep "$(date +%Y-%m-%d)" bot.log | grep "🟢 SIGNAL" | wc -l)" && \
echo "Errors today: $(grep "$(date +%Y-%m-%d)" bot.log | grep "ERROR" | wc -l)" && \
echo "Memory: $(systemctl status scalperbot | grep Memory)" && \
echo "Last activity: $(tail -1 bot.log | awk '{print $1, $2}')"
```

### View Current Filter Status (10 seconds)
```bash
# See latest GREEN-2 checks for all pairs
tail -200 bot.log | grep "GREEN 2" | tail -4
```

### Check for Signals (5 seconds)
```bash
# Count signals today
grep "$(date +%Y-%m-%d)" bot.log | grep "🟢 SIGNAL GENERATED" | wc -l

# Show signal details
grep "🟢 SIGNAL GENERATED" bot.log | tail -10
```

### Run Full Signal Analysis (30 seconds)
```bash
./analyze_signals.sh
```

### Execute Rollback (2 minutes)
```bash
# 1. Stop bot
sudo systemctl stop scalperbot

# 2. Disable relaxed session
nano .env
# Change: SQUEEZE_ENABLE_RELAXED_SESSION=false

# 3. Restart
sudo systemctl start scalperbot

# 4. Verify STRICT mode in logs
tail -f bot.log | grep "GREEN 2"
```

---

## 🚦 Decision Matrix

### Based on Signal Count (First 24 Hours)

| Signals | Assessment | Action |
|---------|------------|--------|
| **0-2** | Normal (strict filters) | ✅ Continue monitoring |
| **3-5** | Healthy signal rate | ✅ Analyze quality with `./analyze_signals.sh` |
| **6-10** | Moderate activity | 🟡 Review correlation ratio |
| **11-20** | High activity | 🟡 Check if all pairs or concentrated |
| **20+** | Very high activity | 🔴 Consider rollback - filters may be too loose |

### Based on Correlation Ratio

| Correlation | Assessment | Action |
|-------------|------------|--------|
| **0-30%** | Independent signals | ✅ Good - pair-specific opportunities |
| **31-60%** | Some correlation | ✅ Normal market behavior |
| **61-80%** | High correlation | 🟡 Reduce position size multiplier |
| **81-100%** | Always correlated | 🔴 Filters catching market-wide moves only |

### Based on Filter Pass Rates (from logs)

| GREEN-1 Pass | GREEN-2 Pass | GREEN-4 Pass | Assessment |
|--------------|--------------|--------------|------------|
| High | High | Low | 🟡 Breakout buffer too strict |
| High | Low | High | ✅ BB filter working correctly |
| Low | High | High | ✅ Trend filter working correctly |
| High | High | High | 🔴 All filters too loose |

---

## 📅 48-Hour Experiment Timeline

**Start:** 2025-11-28 00:10:48 CET
**End:** 2025-11-30 00:10:48 CET (auto-revert)

### Daily Checklist

**Day 1 (Today - Nov 28):**
- [x] Bot started with relaxed thresholds
- [ ] Morning report (use DAILY_REPORT_TEMPLATE.md)
- [ ] Evening report
- [ ] Review first signals (if any)

**Day 2 (Nov 29):**
- [ ] Morning report
- [ ] Run `./analyze_signals.sh` if signals present
- [ ] Decide: Continue, Rollback, or Adjust
- [ ] Evening report

**Day 3 (Nov 30 - Auto-revert):**
- [ ] Verify auto-revert to STRICT mode occurred
- [ ] Final analysis report
- [ ] Document findings

---

## 🎯 Success Criteria (End of 48 Hours)

The experiment is **successful** if:
- ✅ 3-10 signals generated (not too many, not too few)
- ✅ Correlation ratio <70% (finding pair-specific opportunities)
- ✅ No crashes or errors
- ✅ Filters adapt appropriately per pair volatility class

The experiment **needs adjustment** if:
- 🟡 0-2 signals (still too strict - may need more relaxation)
- 🟡 10-20 signals (borderline - analyze quality)
- 🟡 High correlation but good signal quality

The experiment **failed** if:
- 🔴 20+ signals (way too loose - false signals)
- 🔴 Any real losses >1% from bad execution
- 🔴 Bot crashes or errors
- 🔴 100% correlation (not adding value)

---

## 📱 Telegram Notification Check

You should receive notifications on your phone when signals trigger.

**Telegram Bot:** `8514109797:AAEVXjbMyHY-vp8vNShJEDPIJG7Ho8xl0W4`
**Chat ID:** `667100147`

If you're NOT receiving notifications:
```bash
# Check Telegram settings in logs
grep "Telegram" bot.log | tail -10

# Verify .env has correct credentials
grep TELEGRAM .env
```

---

## 🔧 Troubleshooting

### Bot Not Running
```bash
sudo systemctl status scalperbot
sudo systemctl restart scalperbot
tail -50 bot.log  # Check for errors
```

### No Signals After 24 Hours
```bash
# This is EXPECTED if market is:
# - Trending DOWN (GREEN-1 fails)
# - Bands contracting (GREEN-2 expansion fails)
# - No breakouts (GREEN-4 fails)

# Check current market conditions:
tail -100 bot.log | grep "Strategy Check:" -A 4
```

### Too Many Signals
```bash
# Execute rollback immediately
# See: ROLLBACK_PLAN.md
```

### Logs Showing Errors
```bash
# Check error details
grep ERROR bot.log | tail -20

# Common errors and fixes:
# - API rate limit: Increase DATA_POLL_INTERVAL
# - Connection timeout: Check internet connection
# - Database locked: Restart bot
```

---

## 📊 What to Look For in Logs

### ✅ Good Signs
```
GREEN 2 [RELAXED_XXX]: Squeeze: True ... Expanding: True ... ✅ PASS
🟢 SIGNAL GENERATED: BTC/USDT BUY @ 91500.00
📊 Single signal (no correlation) - size multiplier: 1.0x
```

### 🟡 Warning Signs
```
🔗 CORRELATION: 4 pairs signaled together: BTC, ETH, SOL, XRP
💰 Suggested size multiplier: 2.0x (higher conviction)
```
→ High correlation may indicate market-wide move, not pair-specific breakout

### 🔴 Red Flags
```
🟢 SIGNAL GENERATED: BTC/USDT BUY @ 91500.00
🟢 SIGNAL GENERATED: ETH/USDT BUY @ 3200.00
🟢 SIGNAL GENERATED: SOL/USDT BUY @ 145.00
[5 more signals within 2 minutes]
```
→ Too many signals too fast = filters too loose = ROLLBACK

---

## 🎓 Learning from the Experiment

At the end of 48 hours, ask yourself:

1. **Did relaxed thresholds generate quality signals?**
   - If YES → Consider making relaxed thresholds permanent
   - If NO → Revert to STRICT and analyze why

2. **Were pair-specific thresholds appropriate?**
   - SOL (10%): Too strict / Just right / Too loose?
   - XRP (8%): Too strict / Just right / Too loose?
   - BTC/ETH (12%): Too strict / Just right / Too loose?

3. **What was the correlation pattern?**
   - Mostly independent signals → Good filter design
   - High correlation → May need additional filters

4. **Next steps:**
   - Keep current settings?
   - Adjust thresholds (which ones, by how much)?
   - Add new filters (volume, momentum, etc.)?
   - Enable GREEN-3 (volume filter)?

---

## 📞 Emergency Contact

If bot is misbehaving and you need immediate help:

1. **STOP THE BOT:**
   ```bash
   sudo systemctl stop scalperbot
   ```

2. **Check this guide:** ROLLBACK_PLAN.md

3. **Backup current state:**
   ```bash
   cp bot.log bot.log.backup.$(date +%Y%m%d-%H%M)
   ```

4. **Restart in safe mode (DRY_RUN):**
   ```bash
   nano .env  # Set DRY_RUN=true
   sudo systemctl start scalperbot
   ```

---

**This guide is your command center for the 48-hour experiment. Bookmark it!** 📌
