"""
Telegram Notifier for ScalperBot
Sends trading signals and alerts to Telegram
"""
import asyncio
import logging
from typing import Optional, Dict, Any
from datetime import datetime
from telegram import Bot
from telegram.error import TelegramError
from config import settings

logger = logging.getLogger(__name__)


class TelegramNotifier:
    """
    Sends notifications to Telegram
    Handles errors gracefully if Telegram is not configured
    """

    def __init__(self):
        self.bot_token = settings.telegram_bot_token
        self.chat_id = settings.telegram_chat_id
        self.enabled = bool(self.bot_token and self.chat_id)
        self.bot: Optional[Bot] = None

        if self.enabled:
            self.bot = Bot(token=self.bot_token)
            logger.info("✅ Telegram notifications enabled")
        else:
            logger.info("ℹ️ Telegram notifications disabled (no credentials)")

    def _format_signal_message(self, signal: Dict[str, Any]) -> str:
        """Format a trading signal for Telegram"""
        symbol = signal['symbol']
        action = signal['action']
        price = signal['price']
        timestamp = signal.get('timestamp', datetime.now())

        # Format timestamp
        if isinstance(timestamp, str):
            time_str = timestamp
        else:
            time_str = timestamp.strftime('%H:%M:%S')

        # Build message
        msg = f"🟢 <b>SIGNAL GENERATED</b>\n\n"
        msg += f"📊 <b>{symbol}</b>\n"
        msg += f"💹 {action} @ {price:.4f}\n"
        msg += f"🕐 {time_str}\n\n"

        # Add filter results
        filters = signal.get('filters', {})
        if filters:
            msg += "<b>Filters:</b>\n"
            for key, value in filters.items():
                # Clean up filter message (remove emojis for compact view)
                clean_value = value.replace('✅', '✓').replace('❌', '✗')
                msg += f"• {clean_value}\n"
            msg += "\n"

        # Add correlation info
        correlation = signal.get('correlation', {})
        if correlation:
            is_correlated = correlation.get('is_correlated', False)
            pair_count = correlation.get('pair_count', 1)
            size_multiplier = correlation.get('size_multiplier', 1.0)

            if is_correlated:
                correlated_symbols = correlation.get('correlated_symbols', [])
                msg += f"🔗 <b>CORRELATION DETECTED</b>\n"
                msg += f"   {pair_count} pairs: {', '.join(correlated_symbols)}\n"
                msg += f"💰 Size multiplier: <b>{size_multiplier}x</b>\n"
                msg += f"   (Higher conviction - market-wide breakout)\n"
            else:
                msg += f"📊 Single signal (no correlation)\n"
                msg += f"💰 Size multiplier: {size_multiplier}x\n"

        return msg

    def _format_error_message(self, error: str, context: Optional[str] = None) -> str:
        """Format an error message for Telegram"""
        msg = f"⚠️ <b>ERROR ALERT</b>\n\n"
        msg += f"❌ {error}\n"
        if context:
            msg += f"\n📍 Context: {context}\n"
        msg += f"\n🕐 {datetime.now().strftime('%H:%M:%S')}"
        return msg

    def _format_info_message(self, title: str, details: str) -> str:
        """Format an info message for Telegram"""
        msg = f"ℹ️ <b>{title}</b>\n\n"
        msg += f"{details}\n"
        msg += f"\n🕐 {datetime.now().strftime('%H:%M:%S')}"
        return msg

    async def _send_async(self, message: str):
        """Internal async send method"""
        if not self.enabled or not self.bot:
            return

        try:
            await self.bot.send_message(
                chat_id=self.chat_id,
                text=message,
                parse_mode='HTML',
                disable_web_page_preview=True
            )
        except TelegramError as e:
            logger.error(f"Failed to send Telegram message: {e}")
        except Exception as e:
            logger.error(f"Unexpected error sending Telegram message: {e}")

    def send_signal(self, signal: Dict[str, Any]):
        """
        Send a trading signal notification
        Non-blocking - runs in background
        """
        if not self.enabled:
            return

        message = self._format_signal_message(signal)

        # Run async in background without blocking
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            # No event loop in current thread, create new one
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

        loop.create_task(self._send_async(message))

    def send_error(self, error: str, context: Optional[str] = None):
        """
        Send an error alert
        Non-blocking - runs in background
        """
        if not self.enabled:
            return

        message = self._format_error_message(error, context)

        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

        loop.create_task(self._send_async(message))

    def send_info(self, title: str, details: str):
        """
        Send an info message
        Non-blocking - runs in background
        """
        if not self.enabled:
            return

        message = self._format_info_message(title, details)

        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

        loop.create_task(self._send_async(message))

    def send_daily_summary(self, summary: Dict[str, Any]):
        """
        Send a daily trading summary
        Non-blocking - runs in background
        """
        if not self.enabled:
            return

        msg = f"📊 <b>DAILY SUMMARY</b>\n\n"
        msg += f"Signals: {summary.get('signal_count', 0)}\n"
        msg += f"Trades: {summary.get('trade_count', 0)}\n"
        msg += f"Win Rate: {summary.get('win_rate', 0):.1f}%\n"
        msg += f"PnL: ${summary.get('pnl', 0):.2f}\n"
        msg += f"\n🕐 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"

        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

        loop.create_task(self._send_async(msg))
