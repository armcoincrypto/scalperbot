# Systemd Setup for ScalperBot

## Growth 2H Scanner Timer

Runs the growth scanner daily at 00:10 UTC to find coins with repeated 20%+ growth patterns.

### Installation

```bash
# Copy service files to systemd directory
sudo cp /home/user/scalperbot/systemd/scalperbot-growth2h.service /etc/systemd/system/
sudo cp /home/user/scalperbot/systemd/scalperbot-growth2h.timer /etc/systemd/system/

# Reload systemd to recognize new files
sudo systemctl daemon-reload

# Enable and start the timer
sudo systemctl enable scalperbot-growth2h.timer
sudo systemctl start scalperbot-growth2h.timer

# Check timer status
systemctl list-timers --all | grep growth2h
```

### Manual Run

```bash
# Run scanner manually
sudo systemctl start scalperbot-growth2h.service

# Check logs
journalctl -u scalperbot-growth2h.service -f
```

### View Results

```bash
# Check qualified symbols in watchlist
sqlite3 /home/user/scalperbot/scalperbot.db "SELECT symbol, best_growth_pct, spike_count_10d, recent_spike_count, score FROM scanner_watchlist ORDER BY score DESC"

# Check all detected growth events
sqlite3 /home/user/scalperbot/scalperbot.db "SELECT symbol, best_growth_pct, spike_count, recent_spike_count, score FROM growth2h_events ORDER BY score DESC LIMIT 20"
```
