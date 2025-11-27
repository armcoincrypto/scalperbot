# ScalperBot - Bugs & Improvements Found

## 🐛 **Bugs Found:**

### 1. **Deprecation Warning - datetime.utcnow()** (main.py:97)
**Severity:** Low (just a warning)
**Issue:** Using deprecated `datetime.utcnow()`
**Current Code:**
```python
logger.info(f"🔄 Strategy Cycle #{cycle} - {datetime.utcnow().isoformat()}")
```
**Fix:**
```python
logger.info(f"🔄 Strategy Cycle #{cycle} - {datetime.now(datetime.UTC).isoformat()}")
```

### 2. **Misleading Fallback Balance** (main.py:84)
**Severity:** Low
**Issue:** When balance fetch fails, defaults to $1000 which could confuse dry-run monitoring
**Current Code:**
```python
self.risk_breaker.set_starting_balance(1000.0)  # Default
```
**Suggestion:** Log more clearly that this is a fallback value

### 3. **Relaxed Session Mode Not Showing in Logs** (FIXED)
**Severity:** Medium  
**Issue:** GREEN-2 logs showed [STRICT] instead of [RELAXED_xxx]
**Status:** ✅ FIXED - Added `state.current_mode = relaxed_mode` to update logging

---

## ⚠️ **Potential Issues:**

### 4. **GREEN-2 Filter is Very Strict**
**Severity:** Medium (affects signal frequency)
**Issue:** Requires BOTH conditions simultaneously:
- Squeeze: BB width must be in bottom 70% of recent range
- Expansion: BB width must be expanding by X%

**Example from logs:**
```
ETH: Squeeze=False (width=36.8, 70%ile=24.9), Expanding=True (22.7%) → FAIL
SOL: Squeeze=False (width=1.96, 70%ile=1.27), Expanding=True (21.0%) → FAIL
```
Both have massive expansion (22%+) but fail because they're not squeezed enough!

**Why:** Market already expanded out of the squeeze zone before we detected it.

**Possible Improvement:** Consider "recently squeezed" (was in squeeze within last N candles)

### 5. **Missing GREEN-1 and GREEN-4 Status in Quick Logs**
**Severity:** Low (UX improvement)
**Issue:** When filtering logs with grep, we only see GREEN-2 passing but can't easily see why no signal
**Suggestion:** Add a summary line showing all filter states

---

## ✅ **What's Working Perfectly:**

1. **Systemd Service** - No crashes, NRestarts=0
2. **Path Configuration** - Correct paths (/home/user/scalperbot)
3. **Virtual Environment** - All dependencies installed
4. **Relaxed Session** - Active with pair-specific thresholds
5. **Telegram Integration** - Configured and ready
6. **Data Collection** - Getting live market data every 10s
7. **4-Layer Filtering** - All filters operational
8. **Correlation Detector** - Ready to identify multi-asset signals

---

## 📊 **Current Market Status (Last Check):**

```
BTC: GREEN-1 ✅ UP | GREEN-2 ❌ Squeeze but not expanding
ETH: Need to check GREEN-1 | GREEN-2 ❌ Expanding but not squeezed  
SOL: Need to check GREEN-1 | GREEN-2 ❌ Expanding but not squeezed
XRP: Need to check GREEN-1 | GREEN-2 ❌ Varied results
```

**Conclusion:** Bot is running perfectly. Waiting for all 4 filters to align.
Most likely to signal first: **BTC or XRP** (they've had GREEN-2 passes)

---

## 🔧 **Optional Quick Fixes:**

Run these commands to apply the datetime fix:
```bash
cd /home/user/scalperbot
# Fix deprecation warning
sed -i 's/datetime.utcnow()/datetime.now(datetime.UTC)/g' main.py
# Restart bot
sudo systemctl restart scalperbot
```
