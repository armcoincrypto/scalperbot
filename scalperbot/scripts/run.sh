#!/bin/bash
# Run ScalperBot

cd "$(dirname "$0")/../.."

# Activate virtual environment if exists
if [ -d "venv" ]; then
    source venv/bin/activate
fi

# Run the bot
python -m scalperbot.main "$@"
