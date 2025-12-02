"""
Telegram Notifications for ScalperBot
Handles all Telegram messaging including:
- Bot startup/shutdown
- Signal notifications
- Order execution
- Hourly status reports
- Early warning alerts (price near breakout)
"""
import asyncio
import logging
from datetime import datetime, timezone
from typing import Dict, Any, Optional, List
import httpx

logger = logging.getLogger(__name__)


class TelegramNotifier:
    """
    Telegram notification handler with rate limiting and cooldowns
    """

    def __init__(
        self,
        bot_token: str,
        chat_id: str,
        status_interval_min: int = 60,
        early_warn_pct: float = 0.1,
        early_warn_cooldown_min: int = 60,
        status_enabled: bool = True
    ):
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.status_interval_min = status_interval_min
        self.early_warn_pct = early_warn_pct
        self.early_warn_cooldown_min = early_warn_cooldown_min
        self.status_enabled = status_enabled

        self.enabled = bool(bot_token and chat_id)
        self.base_url = f"https://api.telegram.org/bot{bot_token}"

        # State tracking
        self.start_time: Optional[datetime] = None
        self.last_status_time: Optional[datetime] = None
        self.early_warn_sent: Dict[str, datetime] = {}  # symbol -> last sent time
        self.status_count_today = 0
        self.max_status_per_day = 25

        if self.enabled:
            logger.info("Telegram notifications enabled")
        else:
            logger.warning("Telegram notifications disabled (missing token or chat_id)")

    async def send_message(self, text: str, parse_mode: str = "HTML") -> bool:
        """Send a message to Telegram"""
        if not self.enabled:
            return False

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(
                    f"{self.base_url}/sendMessage",
                    data={
                        "chat_id": self.chat_id,
                        "text": text,
                        "parse_mode": parse_mode
                    }
                )

                if response.status_code == 200:
                    logger.debug(f"Telegram message sent successfully")
                    return True
                else:
                    logger.error(f"Telegram send failed: {response.status_code} - {response.text}")
                    return False

        except Exception as e:
            logger.error(f"Telegram send error: {e}")
            return False

    async def notify_bot_started(self, mode: str, pairs: List[str], balance: float):
        """Send bot startup notification"""
        self.start_time = datetime.now(timezone.utc)

        pairs_str = ", ".join([p.replace("/USDT", "") for p in pairs])

        message = (
            f"<b>ScalperBot Started</b>\n\n"
            f"Mode: <code>{mode}</code>\n"
            f"Pairs: <code>{pairs_str}</code>\n"
            f"Balance: <code>${balance:.2f} USDT</code>\n"
            f"Time: <code>{self.start_time.strftime('%Y-%m-%d %H:%M UTC')}</code>\n\n"
            f"Status reports every {self.status_interval_min} min"
        )

        await self.send_message(message)

    async def notify_bot_stopped(self, reason: str = "Manual shutdown"):
        """Send bot shutdown notification"""
        uptime = self._get_uptime_str()

        message = (
            f"<b>ScalperBot Stopped</b>\n\n"
            f"Reason: <code>{reason}</code>\n"
            f"Uptime: <code>{uptime}</code>\n"
            f"Time: <code>{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}</code>"
        )

        await self.send_message(message)

    async def notify_signal(self, signal: Dict[str, Any]):
        """Send signal notification"""
        symbol = signal['symbol'].replace("/USDT", "")
        action = signal['action']
        price = signal['price']
        reason = signal.get('reason', 'N/A')

        emoji = "" if action == "BUY" else ""

        message = (
            f"<b>{emoji} {action} Signal: {symbol}</b>\n\n"
            f"Price: <code>${price:,.4f}</code>\n"
            f"Reason: <code>{reason}</code>\n"
            f"Time: <code>{datetime.now(timezone.utc).strftime('%H:%M:%S UTC')}</code>"
        )

        await self.send_message(message)

    async def notify_order_executed(
        self,
        symbol: str,
        side: str,
        price: float,
        quantity: float,
        notional: float,
        order_id: Optional[str] = None,
        dry_run: bool = False,
        pnl_pct: Optional[float] = None,
        exit_reason: Optional[str] = None
    ):
        """Send order execution notification"""
        symbol_short = symbol.replace("/USDT", "")
        mode_tag = "[DRY_RUN] " if dry_run else ""

        # Use different emoji for BUY vs SELL
        if side.upper() == "BUY":
            emoji = ""
        else:
            # Profit or loss emoji for SELL
            if pnl_pct is not None and pnl_pct >= 0:
                emoji = ""
            else:
                emoji = ""

        message = (
            f"<b>{emoji} {mode_tag}Order Executed</b>\n\n"
            f"Symbol: <code>{symbol_short}</code>\n"
            f"Side: <code>{side.upper()}</code>\n"
            f"Price: <code>${price:,.4f}</code>\n"
            f"Quantity: <code>{quantity:.6f}</code>\n"
            f"Notional: <code>${notional:.2f}</code>\n"
        )

        # Add PnL info for SELL orders
        if side.upper() == "SELL" and pnl_pct is not None:
            pnl_emoji = "" if pnl_pct >= 0 else ""
            message += f"PnL: <code>{pnl_pct:+.2f}%</code> {pnl_emoji}\n"

        # Add exit reason for SELL orders
        if exit_reason:
            message += f"Reason: <code>{exit_reason}</code>\n"

        if order_id:
            message += f"Order ID: <code>{order_id}</code>\n"

        message += f"Time: <code>{datetime.now(timezone.utc).strftime('%H:%M:%S UTC')}</code>"

        await self.send_message(message)

    async def notify_risk_breaker(self, pnl: float, pnl_pct: float, limit_pct: float):
        """Send risk breaker notification"""
        message = (
            f"<b>Risk Breaker Triggered</b>\n\n"
            f"Daily PnL: <code>${pnl:.2f} ({pnl_pct:.2f}%)</code>\n"
            f"Limit: <code>{limit_pct:.1f}%</code>\n\n"
            f"Trading paused until tomorrow."
        )

        await self.send_message(message)

    async def send_hourly_status(
        self,
        mode: str,
        pairs_data: List[Dict[str, Any]],
        open_positions: int = 0,
        exposure_usd: float = 0,
        max_exposure_usd: float = 0,
        pnl_pct: float = 0
    ):
        """
        Send hourly status report

        pairs_data: List of dicts with keys:
            - symbol: str
            - price: float
            - breakout_level: float
            - gap_pct: float
            - green1_pass: bool
            - green2_pass: bool
        """
        if not self.status_enabled:
            return

        # Check daily limit
        if self.status_count_today >= self.max_status_per_day:
            logger.debug("Daily status message limit reached")
            return

        now = datetime.now(timezone.utc)
        uptime = self._get_uptime_str()

        # Build pairs status
        pairs_lines = []
        for p in pairs_data:
            symbol = p['symbol'].replace("/USDT", "")
            price = p['price']
            breakout = p['breakout_level']
            gap_pct = p['gap_pct']

            # Status indicators
            if gap_pct <= self.early_warn_pct:
                status = ""  # Very close
            elif gap_pct <= 0.5:
                status = ""  # Close
            else:
                status = ""  # Normal

            pairs_lines.append(
                f"{status} <code>{symbol}: {price:,.2f} -> {breakout:,.2f} ({gap_pct:.2f}%)</code>"
            )

        pairs_str = "\n".join(pairs_lines)

        # Exposure info
        exposure_pct = (exposure_usd / max_exposure_usd * 100) if max_exposure_usd > 0 else 0

        message = (
            f"<b>ScalperBot Status</b>\n"
            f"<code>{now.strftime('%Y-%m-%d %H:%M UTC')}</code>\n\n"
            f"Mode: <code>{mode}</code> | Uptime: <code>{uptime}</code>\n\n"
            f"<b>Pairs (price -> breakout):</b>\n"
            f"{pairs_str}\n\n"
            f"Positions: <code>{open_positions}</code>\n"
            f"Exposure: <code>${exposure_usd:.0f}/${max_exposure_usd:.0f} ({exposure_pct:.1f}%)</code>\n"
            f"Daily PnL: <code>{pnl_pct:+.2f}%</code>\n\n"
            f"<i>Next report in {self.status_interval_min} min</i>"
        )

        if await self.send_message(message):
            self.last_status_time = now
            self.status_count_today += 1

    async def send_early_warning(
        self,
        symbol: str,
        price: float,
        breakout_level: float,
        gap_pct: float,
        green1_pass: bool,
        green2_pass: bool
    ):
        """
        Send early warning when price is within threshold of breakout
        Respects cooldown to avoid spam
        """
        if not self.status_enabled:
            return

        now = datetime.now(timezone.utc)

        # Check cooldown
        if symbol in self.early_warn_sent:
            last_sent = self.early_warn_sent[symbol]
            cooldown_seconds = self.early_warn_cooldown_min * 60
            if (now - last_sent).total_seconds() < cooldown_seconds:
                logger.debug(f"Early warning cooldown active for {symbol}")
                return

        symbol_short = symbol.replace("/USDT", "")

        # Filter status
        filters_status = []
        if green1_pass:
            filters_status.append("GREEN1")
        if green2_pass:
            filters_status.append("GREEN2")

        filters_str = ", ".join(filters_status) if filters_status else "None yet"

        message = (
            f"<b>Early Warning: {symbol_short}</b>\n\n"
            f"Price: <code>${price:,.4f}</code>\n"
            f"Breakout: <code>${breakout_level:,.4f}</code>\n"
            f"Gap: <code>{gap_pct:.3f}%</code>\n\n"
            f"Passing filters: <code>{filters_str}</code>\n\n"
            f"<i>Signal may trigger if price breaks {breakout_level:,.2f}</i>"
        )

        if await self.send_message(message):
            self.early_warn_sent[symbol] = now
            logger.info(f"Early warning sent for {symbol}")

    def should_send_status(self) -> bool:
        """Check if it's time to send hourly status"""
        if not self.status_enabled:
            return False

        if self.last_status_time is None:
            return True

        now = datetime.now(timezone.utc)
        elapsed = (now - self.last_status_time).total_seconds()
        return elapsed >= (self.status_interval_min * 60)

    def check_early_warning(self, symbol: str, price: float, breakout_level: float) -> tuple[bool, float]:
        """
        Check if early warning should be sent for symbol
        Returns (should_warn, gap_pct)
        """
        if breakout_level <= 0:
            return False, 0.0

        gap_pct = abs(breakout_level - price) / breakout_level * 100

        should_warn = gap_pct <= self.early_warn_pct

        return should_warn, gap_pct

    def reset_daily_counters(self):
        """Reset daily counters (call at midnight)"""
        self.status_count_today = 0
        self.early_warn_sent.clear()
        logger.info("Daily notification counters reset")

    def _get_uptime_str(self) -> str:
        """Get formatted uptime string"""
        if not self.start_time:
            return "N/A"

        now = datetime.now(timezone.utc)
        delta = now - self.start_time

        hours, remainder = divmod(int(delta.total_seconds()), 3600)
        minutes, _ = divmod(remainder, 60)

        if hours > 0:
            return f"{hours}h{minutes}m"
        return f"{minutes}m"
