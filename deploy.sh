#!/bin/bash
# ScalperBot Deployment Script
# Deploys ScalperBot to VPS at root@207.180.212.142

set -e

SERVER="root@207.180.212.142"
REMOTE_DIR="/root/scalperbot"

echo "🚀 Deploying ScalperBot to $SERVER..."

# Copy files to server
echo "📦 Copying files..."
rsync -avz --exclude '.git' --exclude '__pycache__' --exclude '*.pyc' \
    --exclude '.env' --exclude 'bot.log' --exclude 'trades.db' \
    . $SERVER:$REMOTE_DIR/

# Run setup on server
echo "⚙️ Setting up on server..."
ssh $SERVER << 'EOF'
cd /root/scalperbot

# Create virtual environment if it doesn't exist
if [ ! -d ".venv" ]; then
    echo "Creating virtual environment..."
    python3 -m venv .venv
fi

# Activate venv and install dependencies
source .venv/bin/activate
pip install --upgrade pip
pip install -r scalperbot/requirements.txt

# Copy .env.example to .env if .env doesn't exist
if [ ! -f ".env" ]; then
    echo "Creating .env from .env.example..."
    cp scalperbot/.env.example .env
    echo "⚠️  Please edit .env with your API keys!"
fi

# Install systemd service
echo "Installing systemd service..."
sudo cp scalperbot/scalperbot.service /etc/systemd/system/
sudo systemctl daemon-reload

echo "✅ Deployment complete!"
echo ""
echo "Next steps:"
echo "1. Edit /root/scalperbot/.env with your API keys"
echo "2. sudo systemctl start scalperbot"
echo "3. sudo systemctl enable scalperbot"
echo "4. tail -f /root/scalperbot/bot.log"
EOF

echo "🎉 Deployment finished!"
