#!/bin/bash
# Start ScalperBot locally (for testing)

cd "$(dirname "$0")/.."

# Check if .env exists
if [ ! -f ".env" ]; then
    echo "⚠️  .env file not found. Creating from .env.example..."
    cp scalperbot/.env.example .env
    echo "Please edit .env with your API keys before running!"
    exit 1
fi

# Activate virtual environment
if [ ! -d ".venv" ]; then
    echo "Creating virtual environment..."
    python3 -m venv .venv
    source .venv/bin/activate
    pip install --upgrade pip
    pip install -r scalperbot/requirements.txt
else
    source .venv/bin/activate
fi

echo "🚀 Starting ScalperBot..."
python -m scalperbot.main
