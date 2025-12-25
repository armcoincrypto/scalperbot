#!/bin/bash
# Install ScalperBot systemd services

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "Installing ScalperBot services..."

# Make auto-update script executable
chmod +x "$SCRIPT_DIR/auto_update.sh"

# Copy service files to systemd
cp "$SCRIPT_DIR/scalperbot.service" /etc/systemd/system/
cp "$SCRIPT_DIR/scalperbot-update.service" /etc/systemd/system/
cp "$SCRIPT_DIR/scalperbot-update.timer" /etc/systemd/system/

# Create log files
touch /var/log/scalperbot.log
touch /var/log/scalperbot-update.log

# Reload systemd
systemctl daemon-reload

# Enable and start services
systemctl enable scalperbot
systemctl enable scalperbot-update.timer

# Start the bot
systemctl start scalperbot

# Start the update timer
systemctl start scalperbot-update.timer

echo ""
echo "Installation complete!"
echo ""
echo "Commands:"
echo "  systemctl status scalperbot        - Check bot status"
echo "  systemctl restart scalperbot       - Restart the bot"
echo "  systemctl stop scalperbot          - Stop the bot"
echo "  journalctl -u scalperbot -f        - View live logs"
echo "  tail -f /var/log/scalperbot.log    - View bot logs"
echo "  tail -f /var/log/scalperbot-update.log - View update logs"
echo "  systemctl list-timers              - Check update timer"
