"""
ScalperBot - Main Entry Point

Professional MEXC Spot trading bot with Telegram control.

Usage:
    python -m scalperbot.main
    python -m scalperbot.main --dry-run
    python -m scalperbot.main --no-telegram
"""

import asyncio
import argparse
import signal
import sys
from datetime import datetime, timedelta

from scalperbot.config import settings
from scalperbot.log import setup_logging, get_logger
from scalperbot.core.engine import TradingEngine
from scalperbot.telegram.bot import TelegramBot

logger = get_logger(__name__)


class ScalperBot:
    """
    Main application class.

    Orchestrates:
    - Trading engine
    - Telegram bot
    - Daily summary scheduler
    """

    def __init__(self, no_telegram: bool = False):
        self.engine = TradingEngine()
        self.telegram = TelegramBot(self.engine) if not no_telegram else None
        self.running = False
        self._daily_task = None

    async def start(self):
        """Start all components."""
        self.running = True

        logger.info("=" * 60)
        logger.info("SCALPERBOT STARTING")
        logger.info(f"Mode: {'DRY_RUN' if settings.dry_run else 'LIVE'}")
        logger.info(f"Watchlist: {settings.watchlist_symbols}")
        logger.info("=" * 60)

        # Initialize engine
        await self.engine.initialize()

        # Initialize and start Telegram bot
        if self.telegram:
            initialized = await self.telegram.initialize()
            if initialized:
                # Start telegram in background
                asyncio.create_task(self.telegram.start())
                await self.telegram.send_message(
                    f"ScalperBot started\n"
                    f"Mode: {'DRY_RUN' if settings.dry_run else 'LIVE'}\n"
                    f"Watchlist: {len(settings.watchlist_symbols)} symbols\n"
                    f"Use /run to start trading"
                )

        # Start daily summary scheduler
        self._daily_task = asyncio.create_task(self._daily_summary_loop())

        # Keep running
        while self.running:
            await asyncio.sleep(1)

    async def stop(self):
        """Stop all components."""
        logger.info("Shutting down ScalperBot...")
        self.running = False

        if self.engine.running:
            await self.engine.stop()

        if self.telegram:
            await self.telegram.send_message("ScalperBot shutting down")
            await self.telegram.stop()

        if self._daily_task:
            self._daily_task.cancel()

        logger.info("ScalperBot stopped")

    async def _daily_summary_loop(self):
        """Send daily summary at midnight UTC."""
        while self.running:
            now = datetime.utcnow()
            # Calculate time until midnight
            tomorrow = (now + timedelta(days=1)).replace(
                hour=0, minute=0, second=0, microsecond=0
            )
            wait_seconds = (tomorrow - now).total_seconds()

            await asyncio.sleep(wait_seconds)

            if self.telegram and self.running:
                await self.telegram.send_daily_summary()


def parse_args():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(description="ScalperBot - MEXC Spot Trading Bot")

    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Force dry run mode (no real trades)"
    )

    parser.add_argument(
        "--live",
        action="store_true",
        help="Force live mode (real trades - DANGEROUS)"
    )

    parser.add_argument(
        "--no-telegram",
        action="store_true",
        help="Disable Telegram bot"
    )

    parser.add_argument(
        "--auto-start",
        action="store_true",
        help="Auto-start trading engine on launch"
    )

    return parser.parse_args()


async def main():
    """Main entry point."""
    args = parse_args()

    # Override mode if specified
    if args.dry_run:
        settings.dry_run = True
    elif args.live:
        settings.dry_run = False
        logger.warning("LIVE MODE ENABLED - REAL TRADES WILL BE PLACED")

    # Setup logging
    setup_logging()

    # Create bot instance
    bot = ScalperBot(no_telegram=args.no_telegram)

    # Handle shutdown signals
    def signal_handler(sig, frame):
        logger.info(f"Received signal {sig}")
        asyncio.create_task(bot.stop())

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    try:
        await bot.start()

        # Auto-start engine if requested
        if args.auto_start:
            await bot.engine.start()

    except KeyboardInterrupt:
        logger.info("Keyboard interrupt received")
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
    finally:
        await bot.stop()


if __name__ == "__main__":
    asyncio.run(main())
