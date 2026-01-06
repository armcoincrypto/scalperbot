"""
Telegram bot for controlling and monitoring the trading engine.
Uses aiogram 3.x for async Telegram API.
"""

import asyncio
from datetime import datetime, timedelta
from typing import Optional

from aiogram import Bot, Dispatcher
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties

from scalperbot.config import settings
from scalperbot.log import get_logger
from scalperbot.telegram import handlers

logger = get_logger(__name__)


class TelegramBot:
    """
    Telegram bot for trading control and notifications.

    Features:
    - Command handling via handlers module
    - Trade notifications (throttled)
    - Daily summary messages
    """

    def __init__(self, engine=None):
        self.engine = engine
        self.bot: Optional[Bot] = None
        self.dp: Optional[Dispatcher] = None
        self.running = False

        # Notification throttling
        self._last_notification: dict = {}
        self._notification_interval = 60  # Min seconds between notifications per symbol

    async def initialize(self):
        """Initialize bot and dispatcher."""
        if not settings.telegram_bot_token:
            logger.warning("Telegram bot token not configured")
            return False

        self.bot = Bot(
            token=settings.telegram_bot_token,
            default=DefaultBotProperties(parse_mode=ParseMode.HTML)
        )

        self.dp = Dispatcher()
        self.dp.include_router(handlers.router)

        # Set engine reference in handlers
        handlers.set_engine(self.engine)
        handlers.set_callbacks(self._start_engine, self._stop_engine)

        # Set up engine callbacks for notifications
        if self.engine:
            self.engine.on_trade_open = self._on_trade_open
            self.engine.on_trade_close = self._on_trade_close
            self.engine.on_signal = self._on_signal

        logger.info("Telegram bot initialized")
        return True

    async def start(self):
        """Start bot polling."""
        if not self.bot or not self.dp:
            logger.error("Bot not initialized")
            return

        self.running = True
        logger.info("Starting Telegram bot polling...")

        try:
            await self.dp.start_polling(self.bot)
        except Exception as e:
            logger.error(f"Bot polling error: {e}")
        finally:
            self.running = False

    async def stop(self):
        """Stop bot."""
        self.running = False
        if self.dp:
            await self.dp.stop_polling()
        if self.bot:
            await self.bot.session.close()
        logger.info("Telegram bot stopped")

    async def send_message(self, text: str, chat_id: str = None):
        """Send message to configured chat."""
        if not self.bot:
            return

        target = chat_id or settings.telegram_chat_id
        if not target:
            return

        try:
            await self.bot.send_message(target, text)
        except Exception as e:
            logger.error(f"Failed to send Telegram message: {e}")

    def _should_notify(self, symbol: str) -> bool:
        """Check if notification should be sent (throttling)."""
        now = datetime.utcnow()
        last = self._last_notification.get(symbol)

        if last and (now - last).total_seconds() < self._notification_interval:
            return False

        self._last_notification[symbol] = now
        return True

    async def _start_engine(self):
        """Callback to start engine from Telegram command."""
        if self.engine and not self.engine.running:
            asyncio.create_task(self.engine.start())
            await self.send_message("Trading engine started")

    async def _stop_engine(self):
        """Callback to stop engine from Telegram command."""
        if self.engine and self.engine.running:
            await self.engine.stop()
            await self.send_message("Trading engine stopped")

    async def _on_trade_open(self, symbol: str, risk_params, order):
        """Notification callback when trade is opened."""
        if not self._should_notify(symbol):
            return

        mode = "[DRY_RUN] " if settings.dry_run else ""
        text = (
            f"{mode}OPENED: {symbol}\n"
            f"Entry: {order.avg_price or risk_params.quantity:.6f}\n"
            f"Qty: {risk_params.quantity:.6f}\n"
            f"SL: {risk_params.stop_loss_price:.6f} ({risk_params.stop_loss_pct:.1f}%)\n"
            f"TP: {risk_params.take_profit_price:.6f}\n"
            f"R:R = {risk_params.risk_reward_ratio:.1f}"
        )
        await self.send_message(text)

    async def _on_trade_close(self, symbol: str, pnl: float, reason: str):
        """Notification callback when trade is closed."""
        mode = "[DRY_RUN] " if settings.dry_run else ""
        emoji = "" if pnl >= 0 else ""
        text = (
            f"{mode}{emoji} CLOSED: {symbol}\n"
            f"Reason: {reason}\n"
            f"PnL: ${pnl:+.2f}"
        )
        await self.send_message(text)

    async def _on_signal(self, symbol: str, score):
        """Notification callback for significant signals."""
        if abs(score.total_score) < 2.0:
            return  # Only notify for strong signals

        if not self._should_notify(f"signal_{symbol}"):
            return

        direction = "" if score.total_score > 0 else ""
        text = (
            f"{direction} SIGNAL: {symbol}\n"
            f"Score: {score.total_score:+.2f}\n"
            f"Reasons: {', '.join(score.reasons)}"
        )
        await self.send_message(text)

    async def send_daily_summary(self):
        """Send daily trading summary."""
        if not self.engine:
            return

        status = await self.engine.get_status()
        mode = "[DRY_RUN] " if settings.dry_run else ""

        text = (
            f"{mode}DAILY SUMMARY\n"
            f"Date: {datetime.utcnow().strftime('%Y-%m-%d')}\n"
            f"Trades: {status['daily_trades']}\n"
            f"Win rate: {status['win_rate']:.1f}%\n"
            f"Total PnL: ${status['daily_pnl']:.2f}\n"
            f"Open positions: {status['open_positions']}"
        )
        await self.send_message(text)
