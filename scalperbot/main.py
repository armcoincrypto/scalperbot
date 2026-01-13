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
from datetime import datetime, timedelta, timezone

from scalperbot.config import settings
from scalperbot.log import setup_logging, get_logger
from scalperbot.core.engine import TradingEngine
from scalperbot.core.labeler import OutcomeLabeler
from scalperbot.core.reconciler import ExchangeReconciler
from scalperbot.core.scanner import get_scanner
from scalperbot.telegram.bot import TelegramBot

logger = get_logger(__name__)


class ScalperBot:
    """
    Main application class.

    Orchestrates:
    - Trading engine
    - Telegram bot
    - Outcome labeler (research)
    - Exchange reconciler (safety)
    - Coin scanner (momentum watchlist)
    - Daily summary scheduler
    """

    def __init__(self, no_telegram: bool = False):
        self.engine = TradingEngine()
        self.telegram = TelegramBot(self.engine) if not no_telegram else None
        self.labeler = OutcomeLabeler()
        self.reconciler = ExchangeReconciler()
        self.running = False
        self._stopped = False  # Prevent double-stop
        self._daily_task = None
        self._labeler_task = None
        self._reconciler_task = None
        self._scanner_task = None
        self._telegram_task = None

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

        # Run state recovery on startup (for LIVE mode)
        if not settings.dry_run:
            await self.reconciler.initialize()
            recovery = await self.reconciler.recover_state()
            if recovery.get("positions_found", 0) > 0:
                logger.warning(f"State recovery: {recovery}")

        # Initialize and start Telegram bot
        if self.telegram:
            initialized = await self.telegram.initialize()
            if initialized:
                # Start telegram in background (save task for cleanup)
                self._telegram_task = asyncio.create_task(self.telegram.start())
                await self.telegram.send_message(
                    f"ScalperBot started\n"
                    f"Mode: {'DRY_RUN' if settings.dry_run else 'LIVE'}\n"
                    f"Watchlist: {len(settings.watchlist_symbols)} symbols\n"
                    f"Use /run to start trading"
                )

        # Start daily summary scheduler
        self._daily_task = asyncio.create_task(self._daily_summary_loop())

        # Start outcome labeler (background task for research)
        self._labeler_task = asyncio.create_task(self._labeler_loop())

        # Start periodic reconciliation (for LIVE mode)
        if not settings.dry_run:
            self._reconciler_task = asyncio.create_task(self._reconciler_loop())

        # Start coin scanner (daily momentum watchlist updater)
        if settings.scanner_enabled:
            self._scanner_task = asyncio.create_task(self._scanner_loop())

        # Keep running
        while self.running:
            await asyncio.sleep(1)

    async def stop(self):
        """Stop all components gracefully."""
        if self._stopped:
            return  # Already stopped
        self._stopped = True

        logger.info("Shutting down ScalperBot...")
        self.running = False

        # Cancel background tasks first
        tasks_to_cancel = []
        if self._daily_task and not self._daily_task.done():
            self._daily_task.cancel()
            tasks_to_cancel.append(self._daily_task)
        if self._labeler_task and not self._labeler_task.done():
            self._labeler_task.cancel()
            tasks_to_cancel.append(self._labeler_task)
        if self._reconciler_task and not self._reconciler_task.done():
            self._reconciler_task.cancel()
            tasks_to_cancel.append(self._reconciler_task)
        if self._scanner_task and not self._scanner_task.done():
            self._scanner_task.cancel()
            tasks_to_cancel.append(self._scanner_task)

        # Wait for tasks to cancel with timeout
        if tasks_to_cancel:
            try:
                await asyncio.wait_for(
                    asyncio.gather(*tasks_to_cancel, return_exceptions=True),
                    timeout=5.0
                )
            except asyncio.TimeoutError:
                logger.warning("Some background tasks did not cancel in time")

        # Stop engine
        if self.engine and self.engine.running:
            try:
                await asyncio.wait_for(self.engine.stop(), timeout=10.0)
            except asyncio.TimeoutError:
                logger.warning("Engine stop timed out")

        # Stop telegram - cancel task first, then clean up
        if self.telegram:
            # Cancel the polling task first to interrupt any pending requests
            if self._telegram_task and not self._telegram_task.done():
                self._telegram_task.cancel()

            # Stop telegram (closes session and dispatcher)
            try:
                await asyncio.wait_for(self.telegram.stop(), timeout=5.0)
            except asyncio.TimeoutError:
                logger.warning("Telegram stop timed out")
            except Exception as e:
                logger.debug(f"Telegram stop error: {e}")

            # Wait for cancelled task to finish
            if self._telegram_task and not self._telegram_task.done():
                try:
                    await asyncio.wait_for(self._telegram_task, timeout=2.0)
                except (asyncio.TimeoutError, asyncio.CancelledError):
                    pass

        logger.info("ScalperBot stopped")

    async def _daily_summary_loop(self):
        """Send daily summary at midnight UTC."""
        while self.running:
            now = datetime.now(timezone.utc)
            # Calculate time until midnight
            tomorrow = (now + timedelta(days=1)).replace(
                hour=0, minute=0, second=0, microsecond=0
            )
            wait_seconds = (tomorrow - now).total_seconds()

            await asyncio.sleep(wait_seconds)

            if self.telegram and self.running:
                await self.telegram.send_daily_summary()

    async def _labeler_loop(self):
        """Run outcome labeler to analyze historical ticks."""
        # Wait for initial startup
        await asyncio.sleep(60)

        try:
            await self.labeler.initialize()
            logger.info("Outcome labeler started")

            while self.running:
                try:
                    await self.labeler._label_batch(batch_size=50)
                except Exception as e:
                    logger.error(f"Labeler error: {e}")

                # Run every 5 minutes
                await asyncio.sleep(300)

        except asyncio.CancelledError:
            logger.info("Labeler task cancelled")

    async def _reconciler_loop(self):
        """Run periodic position reconciliation."""
        # Wait for initial startup
        await asyncio.sleep(120)

        try:
            logger.info("Position reconciler started")

            while self.running:
                try:
                    discrepancies = await self.reconciler.reconcile_positions()
                    if discrepancies:
                        # Alert via Telegram
                        if self.telegram:
                            msg = "STATE DISCREPANCY DETECTED:\n"
                            for d in discrepancies:
                                msg += f"  {d.severity}: {d.discrepancy_type} {d.symbol}\n"
                            await self.telegram.send_message(msg)
                except Exception as e:
                    logger.error(f"Reconciler error: {e}")

                # Run every 5 minutes
                await asyncio.sleep(300)

        except asyncio.CancelledError:
            logger.info("Reconciler task cancelled")

    async def _scanner_loop(self):
        """Run daily coin scanner to update momentum watchlist."""
        try:
            scanner = await get_scanner()
            logger.info("Coin scanner started")

            # Run immediately on startup, then daily
            first_run = True

            while self.running:
                now = datetime.now(timezone.utc)

                if first_run:
                    # Run 30 seconds after startup
                    await asyncio.sleep(30)
                    first_run = False
                else:
                    # Calculate time until next scheduled run
                    target = now.replace(
                        hour=settings.scanner_run_hour,
                        minute=settings.scanner_run_minute,
                        second=0,
                        microsecond=0
                    )
                    if target <= now:
                        target += timedelta(days=1)

                    wait_seconds = (target - now).total_seconds()
                    logger.info(
                        f"Next scanner run in {wait_seconds/3600:.1f} hours "
                        f"at {target.strftime('%H:%M')} UTC"
                    )
                    await asyncio.sleep(wait_seconds)

                if not self.running:
                    break

                # Run the scan
                try:
                    result = await scanner.run_daily_scan()

                    # Notify via Telegram
                    if self.telegram and result['success']:
                        if result['watchlist']:
                            msg = (
                                f"SCANNER UPDATE\n"
                                f"Found {result['candidates']} momentum coins\n"
                                f"New watchlist ({len(result['watchlist'])}):\n"
                                + "\n".join(f"  {s}" for s in result['watchlist'][:10])
                            )
                            await self.telegram.send_message(msg)

                    # Update engine watchlist if using DB-backed watchlist
                    if settings.scanner_use_db_watchlist and result['watchlist']:
                        self.engine.set_watchlist(result['watchlist'])
                        logger.info(f"Engine watchlist updated: {result['watchlist']}")

                except Exception as e:
                    logger.error(f"Scanner error: {e}", exc_info=True)
                    if self.telegram:
                        await self.telegram.send_message(f"Scanner error: {e}")

        except asyncio.CancelledError:
            logger.info("Scanner task cancelled")


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

    # Handle shutdown signals using asyncio-safe approach
    loop = asyncio.get_running_loop()
    shutdown_event = asyncio.Event()

    def signal_handler(sig):
        logger.info(f"Received signal {sig.name}, initiating shutdown...")
        shutdown_event.set()

    # Register signal handlers
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, signal_handler, sig)
        except NotImplementedError:
            # Windows doesn't support add_signal_handler
            signal.signal(sig, lambda s, f: shutdown_event.set())

    try:
        # Start bot in background
        bot_task = asyncio.create_task(bot.start())

        # Auto-start engine if requested
        if args.auto_start:
            # Wait a moment for initialization
            await asyncio.sleep(2)
            if not bot.engine.running:
                asyncio.create_task(bot.engine.start())

        # Wait for EITHER: shutdown signal OR bot task completion
        # This handles both our signal handler and aiogram's signal handler
        shutdown_wait = asyncio.create_task(shutdown_event.wait())
        done, pending = await asyncio.wait(
            [bot_task, shutdown_wait],
            return_when=asyncio.FIRST_COMPLETED
        )

        # Cancel any pending tasks
        for task in pending:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        # Log what triggered the shutdown
        if bot_task in done:
            logger.info("Bot task completed, shutting down...")
        else:
            logger.info("Shutdown signal received...")

    except asyncio.CancelledError:
        logger.info("Main task cancelled")
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
    finally:
        # Graceful shutdown
        await bot.stop()
        logger.info("Shutdown complete")

        # Force exit after a brief delay - aiogram's retry loop can keep process alive
        await asyncio.sleep(0.5)
        import os
        os._exit(0)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass  # Already handled by signal handler
