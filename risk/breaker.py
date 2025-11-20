"""
Risk Breaker - Daily Loss Limit
Stops trading if daily loss exceeds threshold (e.g., -3%)
"""
import logging
from datetime import datetime
from scalperbot.db import TradeDB
from scalperbot.config import settings

logger = logging.getLogger(__name__)


class RiskBreaker:
    """
    Circuit breaker for daily losses
    Stops trading if loss exceeds configured limit
    """

    def __init__(self, db: TradeDB, daily_loss_limit_pct: float = None):
        self.db = db
        self.daily_loss_limit_pct = daily_loss_limit_pct or settings.daily_loss_limit_pct
        self.is_breaker_tripped = False
        self.starting_balance = None

    def set_starting_balance(self, balance: float):
        """Set starting balance for the day"""
        self.starting_balance = balance
        logger.info(f"Risk breaker initialized with starting balance: ${balance:.2f}")

    def check_daily_loss(self) -> bool:
        """
        Check if daily loss limit is exceeded
        Returns True if trading should stop
        """
        if self.is_breaker_tripped:
            logger.warning("⛔ Risk breaker already tripped - trading stopped")
            return True

        # Get today's PnL from database
        today = datetime.utcnow().strftime("%Y-%m-%d")
        daily_pnl = self.db.get_daily_pnl(today)

        # Calculate loss percentage
        if self.starting_balance and self.starting_balance > 0:
            loss_pct = (daily_pnl / self.starting_balance) * 100
        else:
            loss_pct = 0

        logger.info(f"Daily PnL: ${daily_pnl:.2f} ({loss_pct:.2f}%)")

        # Check if loss limit exceeded
        if loss_pct < -self.daily_loss_limit_pct:
            self.is_breaker_tripped = True
            logger.error(f"🚨 RISK BREAKER TRIPPED! Daily loss {loss_pct:.2f}% exceeds limit -{self.daily_loss_limit_pct}%")
            logger.error(f"⛔ Trading STOPPED for the day")
            return True

        return False

    def can_trade(self) -> bool:
        """
        Check if trading is allowed
        Returns False if breaker is tripped
        """
        if self.is_breaker_tripped:
            return False

        return not self.check_daily_loss()

    def reset(self):
        """Reset breaker (call at start of new trading day)"""
        self.is_breaker_tripped = False
        logger.info("Risk breaker reset for new trading day")

    def get_status(self) -> dict:
        """Get current risk breaker status"""
        today = datetime.utcnow().strftime("%Y-%m-%d")
        daily_pnl = self.db.get_daily_pnl(today)

        if self.starting_balance and self.starting_balance > 0:
            loss_pct = (daily_pnl / self.starting_balance) * 100
        else:
            loss_pct = 0

        return {
            'tripped': self.is_breaker_tripped,
            'daily_pnl': daily_pnl,
            'loss_pct': loss_pct,
            'limit_pct': -self.daily_loss_limit_pct,
            'can_trade': not self.is_breaker_tripped
        }
