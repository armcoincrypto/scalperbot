# 📊 ScalperBot Daily Report Template

Copy this template each morning and fill in the data using the commands below.

---

## Daily Report: [DATE]

### ⏱️ Uptime & Health
```bash
# Bot uptime
systemctl status scalperbot | grep "Active:"
```
**Result:**
- Uptime: _______________
- Restarts: _______________
- Status: ✅ Running / ⚠️ Issues

---

### 🎯 Signal Performance

```bash
# Count signals generated today
grep "$(date +%Y-%m-%d)" /home/user/scalperbot/bot.log | grep "🟢 SIGNAL GENERATED" | wc -l
```
**Signals today:** _____

```bash
# Show all signals with details
grep "$(date +%Y-%m-%d)" /home/user/scalperbot/bot.log | grep "🟢 SIGNAL GENERATED"
```
**Signal list:**
1. _____________________
2. _____________________
3. _____________________

---

### 📈 Filter Analysis (Last 24 Hours)

```bash
# Get latest GREEN-2 status for each pair
tail -500 /home/user/scalperbot/bot.log | grep "GREEN 2" | tail -8
```

**Current Mode:** RELAXED / STRICT / PERMISSIVE

| Pair | Squeeze Status | Expansion Rate | Threshold | Pass/Fail |
|------|---------------|----------------|-----------|-----------|
| BTC  | _____         | _____%         | 12%       | ⬜        |
| ETH  | _____         | _____%         | 12%       | ⬜        |
| SOL  | _____         | _____%         | 10%       | ⬜        |
| XRP  | _____         | _____%         | 8%        | ⬜        |

**Observations:**
- Closest to triggering: _____________________
- Common failure reason: _____________________
- Market condition: Trending UP / DOWN / SIDEWAYS

---

### 🔗 Correlation Events

```bash
# Check for correlated signals
grep "$(date +%Y-%m-%d)" /home/user/scalperbot/bot.log | grep "🔗 CORRELATION"
```
**Correlation count:** _____

**Details:**
- Event 1: _____________________
- Event 2: _____________________

---

### 💰 Trade Execution (if any)

```bash
# Check database for today's trades
sqlite3 /home/user/scalperbot/scalperbot/trades.db "SELECT symbol, side, price, quantity, notional, status, entry_time FROM trades WHERE date(entry_time) = date('now') ORDER BY entry_time DESC;"
```

| Time | Symbol | Side | Price | Quantity | Notional | Status |
|------|--------|------|-------|----------|----------|--------|
|      |        |      |       |          |          |        |

**Execution quality:**
- Average slippage: _____%
- Fill rate: _____/_____ orders
- Issues: _____________________

---

### 📊 Market Summary

```bash
# Get latest prices
tail -100 /home/user/scalperbot/bot.log | grep "Strategy Check:" -A 1 | grep "GREEN 1"
```

| Pair | Current Price | Trend (5m) | Notes |
|------|---------------|------------|-------|
| BTC  | $_____        | UP/DOWN    |       |
| ETH  | $_____        | UP/DOWN    |       |
| SOL  | $_____        | UP/DOWN    |       |
| XRP  | $_____        | UP/DOWN    |       |

---

### 🚨 Errors & Warnings

```bash
# Check for errors
grep "$(date +%Y-%m-%d)" /home/user/scalperbot/bot.log | grep -E "ERROR|WARNING" | tail -10
```

**Error count:** _____

**Critical issues:**
1. _____________________
2. _____________________

---

### 💻 System Resources

```bash
# Check resource usage
systemctl status scalperbot | grep -E "Memory|CPU|Tasks"
```

**Resource usage:**
- Memory: _____ MB
- CPU time: _____ seconds
- Tasks: _____

**Health:** ✅ Normal / ⚠️ High usage / 🔴 Critical

---

### 🧪 Relaxed Session Status

```bash
# Check how much time left in experiment
grep "Relaxed session" /home/user/scalperbot/bot.log | tail -1
```

**Experiment status:**
- Enabled: YES / NO
- Started: _____________________
- End time: 2025-11-30 00:10:48 CET
- Time remaining: _____ hours

**Progress:**
- Signals generated: _____
- Threshold proving: TOO LOOSE / JUST RIGHT / TOO STRICT
- Action needed: CONTINUE / ROLLBACK / ADJUST

---

## 📝 Decision & Actions

### Today's Assessment:
⬜ Everything normal - continue monitoring
⬜ Some concerns - investigate further
⬜ Issues detected - execute rollback

### Actions Taken:
- [ ] _____________________
- [ ] _____________________
- [ ] _____________________

### Notes for Tomorrow:
_____________________________________________________
_____________________________________________________
_____________________________________________________

---

## 🎯 Quick Health Check (Run This First)

```bash
# One-liner health check
echo "=== BOT HEALTH CHECK ===" && \
systemctl is-active scalperbot && \
echo "Signals today: $(grep "$(date +%Y-%m-%d)" /home/user/scalperbot/bot.log | grep "🟢 SIGNAL" | wc -l)" && \
echo "Errors today: $(grep "$(date +%Y-%m-%d)" /home/user/scalperbot/bot.log | grep "ERROR" | wc -l)" && \
echo "Last check: $(tail -1 /home/user/scalperbot/bot.log | awk '{print $1, $2}')"
```

**Quick status:** ✅ / ⚠️ / 🔴

---

## 📅 Historical Comparison

| Date | Signals | Mode | Errors | Status |
|------|---------|------|--------|--------|
| [Today] | ____ | RELAXED | ____ | ✅/⚠️/🔴 |
| [Yesterday] | ____ | _____ | ____ | ✅/⚠️/🔴 |
| [2 days ago] | ____ | _____ | ____ | ✅/⚠️/🔴 |

**Trend:** IMPROVING / STABLE / DEGRADING

---

**Report completed by:** _____________________
**Next review:** _____________________
