# 🚨 Emergency Rollback Plan

## When to Execute Rollback

Execute immediately if:
- ✅ **Signal quality degrades** - Too many false signals (>5 per hour)
- ✅ **Slippage exceeds threshold** - Fills worse than 0.5% from signal price
- ✅ **Loss breaker triggers** - Losses exceed 2% in dry-run monitoring
- ✅ **Correlated failures** - Multiple pairs fail simultaneously (suggests filter too loose)
- ✅ **Manual override** - You decide thresholds are inappropriate

---

## 🔴 ROLLBACK PROCEDURE (Execute in Order)

### Step 1: Disable Relaxed Session (30 seconds)
```bash
# SSH to VPS
ssh root@207.180.212.142

# Stop the bot
cd /home/user/scalperbot
sudo systemctl stop scalperbot

# Edit .env file
nano .env
# Change this line:
# SQUEEZE_ENABLE_RELAXED_SESSION=true
# To:
# SQUEEZE_ENABLE_RELAXED_SESSION=false

# Save and exit (Ctrl+X, Y, Enter)

# Restart bot
sudo systemctl start scalperbot
```

### Step 2: Verify Rollback in Logs (1 minute)
```bash
# Watch logs for STRICT mode
tail -f /home/user/scalperbot/bot.log | grep "GREEN 2"

# You should see:
# GREEN 2 [STRICT]: Squeeze: ... (30%ile), Expanding: ... (need=20%)
#
# NOT:
# GREEN 2 [RELAXED_XXX]: ...

# Press Ctrl+C when verified
```

### Step 3: Confirm Bot Health (30 seconds)
```bash
# Check service status
sudo systemctl status scalperbot

# Should show: Active: active (running)
# Should NOT show: Failed/Restart messages
```

### Step 4: Document Rollback Reason
```bash
# Add entry to rollback log
echo "$(date): ROLLBACK - Reason: [YOUR REASON HERE]" >> /home/user/scalperbot/rollback.log
```

---

## 📊 Post-Rollback Analysis

After rollback, gather this data for analysis:

```bash
# 1. How many signals were generated during relaxed session?
grep "🟢 SIGNAL GENERATED" /home/user/scalperbot/bot.log | wc -l

# 2. What were the signal timestamps?
grep "🟢 SIGNAL GENERATED" /home/user/scalperbot/bot.log

# 3. Were there correlation warnings?
grep "🔗 CORRELATION" /home/user/scalperbot/bot.log

# 4. What were the expansion rates at signal time?
grep -B 2 "🟢 SIGNAL GENERATED" /home/user/scalperbot/bot.log | grep "GREEN 2"

# 5. Check database for fills (if any executed)
sqlite3 /home/user/scalperbot/scalperbot/trades.db "SELECT * FROM trades ORDER BY entry_time DESC LIMIT 10;"
```

---

## 🔄 Alternative: Pause Trading, Keep Data Collection

If you want to analyze more before deciding:

```bash
# Edit .env
nano /home/user/scalperbot/.env

# Change:
# DRY_RUN=false
# To:
# DRY_RUN=true

# Restart
sudo systemctl restart scalperbot
```

This keeps the bot running and collecting data, but prevents any real trades.

---

## 📞 Quick Reference Commands

| Action | Command |
|--------|---------|
| **Stop bot** | `sudo systemctl stop scalperbot` |
| **Start bot** | `sudo systemctl start scalperbot` |
| **Restart bot** | `sudo systemctl restart scalperbot` |
| **Check status** | `sudo systemctl status scalperbot` |
| **View live logs** | `tail -f /home/user/scalperbot/bot.log` |
| **Check last 50 GREEN-2 filters** | `tail -500 /home/user/scalperbot/bot.log \| grep "GREEN 2" \| tail -50` |
| **Count signals today** | `grep "$(date +%Y-%m-%d)" /home/user/scalperbot/bot.log \| grep "🟢 SIGNAL" \| wc -l` |

---

## 🎯 Rollback Success Criteria

After rollback, verify:
- ✅ Logs show `[STRICT]` mode (30%ile squeeze, 20% expansion)
- ✅ Bot has run for 5+ minutes without crashes
- ✅ No new signals generated (stricter thresholds working)
- ✅ systemctl status shows `active (running)`

---

## 📝 Rollback Decision Matrix

| Scenario | Action | Reasoning |
|----------|--------|-----------|
| **0-2 signals in 48h, all quality** | ✅ Keep relaxed | Working as intended |
| **3-5 signals, mixed quality** | 🟡 Analyze first, decide | Need more data |
| **5+ signals/hour** | 🔴 Rollback immediately | Too loose, false signals |
| **Any real losses >1%** | 🔴 Rollback immediately | Risk threshold exceeded |
| **Correlation on every signal** | 🟡 Reduce position size | Filters catching market-wide moves |
| **Slippage >0.5% average** | 🟡 Switch to limit orders | Execution quality issue |

---

## 💾 Backup Before Rollback

If you modified code during the experiment:
```bash
# Create snapshot before rollback
cd /home/user
tar -czf scalperbot-pre-rollback-$(date +%Y%m%d-%H%M).tar.gz scalperbot/
```

---

## ⏱️ Expected Rollback Time

- **Total time:** 2-3 minutes
- **Downtime:** ~30 seconds (during restart)
- **Verification:** 1-2 minutes

**The bot will continue monitoring markets during rollback, just with stricter filters.**
