#!/bin/bash
# Auto-update script for ScalperBot
# Checks for updates and restarts the bot if changes are detected

REPO_DIR="/home/user/scalperbot"
BRANCH="claude/analyze-trading-data-01JfApHjnoHq5Z5DGYheQCot"
LOG_FILE="/var/log/scalperbot-update.log"
SERVICE_NAME="scalperbot"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a "$LOG_FILE"
}

cd "$REPO_DIR" || exit 1

# Fetch latest changes
git fetch origin "$BRANCH" 2>/dev/null

# Get current and remote commit hashes
LOCAL=$(git rev-parse HEAD)
REMOTE=$(git rev-parse "origin/$BRANCH")

if [ "$LOCAL" != "$REMOTE" ]; then
    log "Update detected! Local: ${LOCAL:0:7} -> Remote: ${REMOTE:0:7}"

    # Pull changes
    git pull origin "$BRANCH" >> "$LOG_FILE" 2>&1

    if [ $? -eq 0 ]; then
        log "Pull successful. Restarting $SERVICE_NAME..."
        systemctl restart "$SERVICE_NAME"
        log "Bot restarted successfully"
    else
        log "ERROR: Git pull failed!"
    fi
else
    log "No updates available"
fi
