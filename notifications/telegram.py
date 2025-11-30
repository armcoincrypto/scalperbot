"""
Telegram Notification Module
Sends trading alerts and status updates via Telegram bot
"""
import asyncio
import logging
from typing import Optional, Dict, Any
from datetime import datetime

logger = logging.getLogger(__name__)

# Try to import telegram library
try:
    from telegram import Bot
    from telegram.error import TelegramError
    TELEGRAM_AVAILABLE = True
except ImportError:
    TELEGRAM_AVAILABLE = False
    logger.warning("python-telegram-bot not installed. Telegram notifications disabled.")


class TelegramNotifier:
    """
    Telegram notification sender for trading alerts
    """

    def __init__(self, bot_token: str, chat_id: str):
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.bot: Optional[Bot] = None
        self.enabled = False

        if not TELEGRAM_AVAILABLE:
            logger.warning("Telegram library not available")
            return

        if not bot_token or not chat_id:
            logger.warning("Telegram bot_token or chat_id not configured - notifications disabled")
            return

        try:
            self.bot = Bot(token=bot_token)
            self.enabled = True
            logger.info("Telegram notifications enabled")
        except Exception as e:
            logger.error(f"Failed to initialize Telegram bot: {e}")

    async def send_message(self, message: str, parse_mode: str = "HTML") -> bool:
        """
        Send a message to the configured Telegram chat

        Args:
            message: The message text to send
            parse_mode: Parsing mode (HTML or Markdown)

        Returns:
            True if sent successfully, False otherwise
        """
        if not self.enabled or not self.bot:
            logger.debug("Telegram not enabled, skipping notification")
            return False

        try:
            await self.bot.send_message(
                chat_id=self.chat_id,
                text=message,
                parse_mode=parse_mode
            )
            logger.debug(f"Telegram message sent: {message[:50]}...")
            return True
        except TelegramError as e:
            logger.error(f"Telegram send error: {e}")
            return False
        except Exception as e:
            logger.error(f"Unexpected Telegram error: {e}")
            return False

    def send_message_sync(self, message: str, parse_mode: str = "HTML") -> bool:
        """
        Synchronous wrapper for send_message
        """
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # Create a task if we're already in an async context
                asyncio.create_task(self.send_message(message, parse_mode))
                return True
            else:
                return loop.run_until_complete(self.send_message(message, parse_mode))
        except RuntimeError:
            # No event loop, create one
            return asyncio.run(self.send_message(message, parse_mode))

    async def notify_bot_started(self, mode: str, pairs: list, balance: float = 0):
        """Send bot startup notification"""
        mode_emoji = "🔶" if mode == "DRY_RUN" else "🟢"
        pairs_str = ", ".join(pairs)

        message = (
            f"🚀 <b>ScalperBot Started</b>\n\n"
            f"Mode: {mode_emoji} {mode}\n"
            f"Pairs: {pairs_str}\n"
            f"Balance: ${balance:.2f} USDT\n"
            f"Time: {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC"
        )
        await self.send_message(message)

    async def notify_signal(self, signal: Dict[str, Any]):
        """Send trading signal notification"""
        symbol = signal.get('symbol', 'N/A')
        action = signal.get('action', 'N/A')
        price = signal.get('price', 0)
        reason = signal.get('reason', '')

        action_emoji = "🟢" if action == "BUY" else "🔴"

        message = (
            f"{action_emoji} <b>Signal: {action} {symbol}</b>\n\n"
            f"Price: ${price:.4f}\n"
            f"Reason: {reason}\n"
            f"Time: {datetime.utcnow().strftime('%H:%M:%S')} UTC"
        )
        await self.send_message(message)

    async def notify_order_executed(
        self,
        symbol: str,
        side: str,
        quantity: float,
        price: float,
        notional: float,
        order_id: str = "",
        is_dry_run: bool = False
    ):
        """Send order execution notification"""
        side_emoji = "🟢" if side.lower() == "buy" else "🔴"
        mode_tag = " [DRY RUN]" if is_dry_run else ""

        message = (
            f"{side_emoji} <b>Order Executed{mode_tag}</b>\n\n"
            f"Symbol: {symbol}\n"
            f"Side: {side.upper()}\n"
            f"Quantity: {quantity:.6f}\n"
            f"Price: ${price:.4f}\n"
            f"Notional: ${notional:.2f}\n"
            f"Order ID: {order_id or 'N/A'}\n"
            f"Time: {datetime.utcnow().strftime('%H:%M:%S')} UTC"
        )
        await self.send_message(message)

    async def notify_order_failed(self, symbol: str, side: str, reason: str = ""):
        """Send order failure notification"""
        message = (
            f"❌ <b>Order Failed</b>\n\n"
            f"Symbol: {symbol}\n"
            f"Side: {side.upper()}\n"
            f"Reason: {reason or 'Unknown'}\n"
            f"Time: {datetime.utcnow().strftime('%H:%M:%S')} UTC"
        )
        await self.send_message(message)

    async def notify_risk_breaker(self, daily_pnl: float, limit: float):
        """Send risk breaker activation notification"""
        message = (
            f"⛔ <b>RISK BREAKER ACTIVATED</b>\n\n"
            f"Daily PnL: ${daily_pnl:.2f}\n"
            f"Loss Limit: {limit:.1f}%\n"
            f"Trading suspended for today!\n"
            f"Time: {datetime.utcnow().strftime('%H:%M:%S')} UTC"
        )
        await self.send_message(message)

    async def notify_bot_stopped(self, reason: str = ""):
        """Send bot shutdown notification"""
        message = (
            f"🛑 <b>ScalperBot Stopped</b>\n\n"
            f"Reason: {reason or 'Manual shutdown'}\n"
            f"Time: {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC"
        )
        await self.send_message(message)

    async def notify_error(self, error: str, context: str = ""):
        """Send error notification"""
        message = (
            f"⚠️ <b>Error Alert</b>\n\n"
            f"Context: {context or 'General'}\n"
            f"Error: {error[:200]}\n"
            f"Time: {datetime.utcnow().strftime('%H:%M:%S')} UTC"
        )
        await self.send_message(message)

    async def notify_green_status(self, symbol: str, filters: Dict[str, bool]):
        """Send GREEN filter status update"""
        def status(passed: bool) -> str:
            return "✅" if passed else "❌"

        message = (
            f"📊 <b>{symbol} Filter Status</b>\n\n"
            f"GREEN 1 (Trend): {status(filters.get('green1', False))}\n"
            f"GREEN 2 (BB Expansion): {status(filters.get('green2', False))}\n"
            f"GREEN 3 (Volume): {status(filters.get('green3', False))}\n"
            f"GREEN 4 (Breakout): {status(filters.get('green4', False))}\n"
            f"Time: {datetime.utcnow().strftime('%H:%M:%S')} UTC"
        )
        await self.send_message(message)

    async def notify_position_closed(
        self,
        symbol: str,
        entry_price: float,
        exit_price: float,
        quantity: float,
        pnl_pct: float,
        pnl_usd: float,
        reason: str,
        is_dry_run: bool = False
    ):
        """Send position closed notification"""
        # Determine emoji based on profit/loss
        if pnl_pct >= 0:
            result_emoji = "🎯" if reason == "TAKE_PROFIT" else "✅"
            result_text = "PROFIT"
        else:
            result_emoji = "🛑" if reason == "STOP_LOSS" else "❌"
            result_text = "LOSS"

        mode_tag = " [DRY RUN]" if is_dry_run else ""

        # Format reason nicely
        reason_display = reason.replace("_", " ").title()

        message = (
            f"{result_emoji} <b>Position Closed{mode_tag}</b>\n\n"
            f"Symbol: {symbol}\n"
            f"Reason: {reason_display}\n"
            f"Entry: ${entry_price:.4f}\n"
            f"Exit: ${exit_price:.4f}\n"
            f"Quantity: {quantity:.6f}\n"
            f"Result: <b>{result_text}</b>\n"
            f"PnL: {pnl_pct:+.2f}% (${pnl_usd:+.2f})\n"
            f"Time: {datetime.utcnow().strftime('%H:%M:%S')} UTC"
        )
        await self.send_message(message)
