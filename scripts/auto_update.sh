#!/bin/bash
#
# ScalperBot Auto-Update Script
# Run this on VPS to pull latest changes and restart bot
#

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BOT_DIR="$(dirname "$SCRIPT_DIR")"
BRANCH="claude/analyze-trading-data-01JfApHjnoHq5Z5DGYheQCot"
LOG_FILE="/var/log/scalperbot_update.log"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a "$LOG_FILE"
}

log "=========================================="
log "ScalperBot Auto-Update Starting..."
log "=========================================="

cd "$BOT_DIR"

# Check current branch
CURRENT_BRANCH=$(git branch --show-current)
log "Current branch: $CURRENT_BRANCH"

# Stash any local changes
if [[ -n $(git status --porcelain) ]]; then
    log "Stashing local changes..."
    git stash
fi

# Fetch and pull latest
log "Fetching latest from origin..."
git fetch origin "$BRANCH"

log "Pulling changes..."
git pull origin "$BRANCH"

# Show what changed
log "Recent commits:"
git log --oneline -5

# Initialize/update research database
log "Initializing research database..."
python3 -c "from analysis.research_db import ResearchDB; db = ResearchDB(); print('DB initialized')"

# Check if systemd service exists
if systemctl list-units --type=service | grep -q scalperbot; then
    log "Restarting scalperbot service..."
    sudo systemctl restart scalperbot
    sleep 3

    # Check status
    if systemctl is-active --quiet scalperbot; then
        log "✅ ScalperBot is running"
        systemctl status scalperbot --no-pager | head -15
    else
        log "❌ ScalperBot failed to start!"
        journalctl -u scalperbot -n 20 --no-pager
        exit 1
    fi
else
    log "⚠️ No systemd service found. Start bot manually with:"
    log "   cd $BOT_DIR && python main.py"
fi

log "=========================================="
log "Update complete!"
log "=========================================="

# Show current config
log ""
log "Current Configuration:"
python3 -c "
from config import settings
print(f'  Research Mode: {settings.research_mode}')
print(f'  Trading Pairs: {settings.trading_pairs}')
print(f'  Strategy: {settings.strategy_type}')
print(f'  TP: {settings.take_profit_pct}% | SL: {settings.stop_loss_pct}%')
print(f'  Trailing: {settings.trailing_enabled} (start: {settings.trail_start_pct}%)')
print(f'  Trading Hours: {settings.trading_hours_start}:00-{settings.trading_hours_end}:00 UTC')
"

log ""
log "Research Database Status:"
python3 -c "
from analysis.research_db import get_research_db
db = get_research_db()
counts = db.get_table_counts()
for table, count in counts.items():
    print(f'  {table}: {count} rows')
"
