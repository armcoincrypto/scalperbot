"""
Configuration management using Pydantic Settings.
All settings loaded from environment variables / .env file.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import List, Optional
import json


class Settings(BaseSettings):
    """Application settings from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore"
    )

    # === Mode ===
    dry_run: bool = True  # CRITICAL: Default to paper trading
    debug: bool = False

    # === MEXC API ===
    mexc_api_key: str = ""
    mexc_api_secret: str = ""
    mexc_base_url: str = "https://api.mexc.com"

    # === Telegram ===
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""  # Your personal chat ID for notifications
    telegram_admin_ids: str = ""  # Comma-separated admin user IDs

    # === Trading Parameters ===
    watchlist: str = "BTCUSDT,ETHUSDT"  # Comma-separated symbols
    position_size_usdt: float = 50.0  # Default position size in USDT
    max_open_positions: int = 3

    # === Polling ===
    poll_interval_sec: int = 10  # How often to check market
    kline_limit: int = 30  # Number of 1m candles to fetch

    # === Entry Triggers ===
    buy_pct_trigger: float = 0.5  # Minimum % change to consider entry
    buy_score_min: float = 2.0  # Minimum score to trigger buy

    # === Exit Triggers ===
    sell_pct_trigger: float = -0.3  # Negative change to trigger exit
    sell_score_max: float = -1.0  # Score below this triggers exit

    # === Risk Management ===
    base_sl_pct: float = 2.0  # Base stop loss %
    take_profit_pct: float = 3.0  # Take profit %
    trailing_enabled: bool = True
    trailing_start_pct: float = 1.5  # Start trailing after this profit
    trailing_offset_pct: float = 0.5  # Trail offset from peak

    # === Safety Filters ===
    max_spread_pct: float = 0.5  # Max allowed spread %
    min_24h_volume_usdt: float = 100000.0  # Min 24h volume
    cooldown_sec: int = 300  # Cooldown per symbol after trade (5 min)

    # === Order Execution ===
    use_limit_orders: bool = True  # Use limit orders for safety
    limit_order_timeout_sec: int = 30  # Cancel limit order after timeout

    # === Liquidity Mode ===
    # "normal" = standard settings for majors (BTC, ETH, etc.)
    # "low" = stricter settings for low-liquidity coins
    liq_mode: str = "normal"

    # Low-liquidity overrides (applied when liq_mode="low")
    low_liq_max_spread_pct: float = 0.3  # Stricter spread for low-liq
    low_liq_require_depth: bool = True   # Always require depth check
    low_liq_position_size_pct: float = 50.0  # Use 50% of normal size
    low_liq_sl_multiplier: float = 1.5   # Wider SL for low-liq

    # === Circuit Breakers ===
    max_daily_loss_pct: float = 5.0  # Stop trading if daily loss exceeds this % of capital
    max_daily_trades: int = 50       # Max trades per day
    max_consecutive_losses: int = 5  # Pause after N consecutive losses

    # === Database ===
    database_path: str = "scalperbot.db"

    # === Coin Scanner ===
    scanner_enabled: bool = True           # Enable automatic daily scanning
    scanner_run_hour: int = 0              # Hour to run daily scan (UTC)
    scanner_run_minute: int = 5            # Minute to run daily scan
    scanner_min_volume: float = 15000.0    # Min 24h volume (filter dead coins)
    scanner_max_volume: float = 200000.0   # Max 24h volume (filter stable coins)
    scanner_min_momentum: float = 30.0     # Min 10-day momentum %
    scanner_max_spread: float = 0.6        # Max bid-ask spread %
    scanner_min_depth: float = 1000.0      # Min order book depth per side ($)
    scanner_top_n: int = 10                # Number of coins to add to watchlist
    scanner_use_db_watchlist: bool = True  # Use scanner watchlist instead of .env

    # === Unified Scoring (Growth2H + Live momentum) ===
    # unified_score = live_score + alpha * growth2h_score + beta * repeater_bonus - gamma * illiquidity_penalty
    unified_score_alpha: float = 0.3       # Weight for growth2h historical score
    unified_score_beta: float = 0.5        # Bonus per spike in last 10 days
    unified_score_gamma: float = 0.2       # Penalty for illiquidity (volume < 50k)
    unified_score_min_spikes: int = 2      # Min spikes required for repeater bonus
    unified_score_recency_days: int = 3    # Spikes within N days get extra weight

    # === Logging ===
    log_level: str = "INFO"
    log_file: str = "scalperbot.log"

    @property
    def watchlist_symbols(self) -> List[str]:
        """Get watchlist as list of symbols."""
        if not self.watchlist:
            return []
        return [s.strip().upper() for s in self.watchlist.split(",") if s.strip()]

    @property
    def admin_user_ids(self) -> List[int]:
        """Get admin Telegram user IDs as list."""
        if not self.telegram_admin_ids:
            return []
        try:
            return [int(x.strip()) for x in self.telegram_admin_ids.split(",") if x.strip()]
        except ValueError:
            return []

    def is_admin(self, user_id: int) -> bool:
        """Check if user is admin."""
        return user_id in self.admin_user_ids

    @property
    def is_low_liq_mode(self) -> bool:
        """Check if low liquidity mode is enabled."""
        return self.liq_mode.lower() == "low"

    @property
    def effective_max_spread_pct(self) -> float:
        """Get effective max spread based on liq mode."""
        return self.low_liq_max_spread_pct if self.is_low_liq_mode else self.max_spread_pct

    @property
    def effective_position_size(self) -> float:
        """Get effective position size based on liq mode."""
        if self.is_low_liq_mode:
            return self.position_size_usdt * (self.low_liq_position_size_pct / 100)
        return self.position_size_usdt

    @property
    def effective_sl_pct(self) -> float:
        """Get effective stop loss % based on liq mode."""
        if self.is_low_liq_mode:
            return self.base_sl_pct * self.low_liq_sl_multiplier
        return self.base_sl_pct

    def get_safe_debug_info(self) -> dict:
        """Get debug info WITHOUT secrets."""
        return {
            "mode": "DRY_RUN" if self.dry_run else "LIVE",
            "liq_mode": self.liq_mode,
            "watchlist": self.watchlist_symbols,
            "position_size_usdt": self.effective_position_size,
            "max_open_positions": self.max_open_positions,
            "poll_interval_sec": self.poll_interval_sec,
            "buy_pct_trigger": self.buy_pct_trigger,
            "buy_score_min": self.buy_score_min,
            "base_sl_pct": self.effective_sl_pct,
            "take_profit_pct": self.take_profit_pct,
            "max_spread_pct": self.effective_max_spread_pct,
            "use_limit_orders": self.use_limit_orders,
            "has_api_key": bool(self.mexc_api_key),
            "has_telegram_token": bool(self.telegram_bot_token),
        }


# Global settings instance
settings = Settings()
