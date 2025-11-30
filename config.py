"""
ScalperBot Configuration
Pydantic-based settings loaded from .env file
"""
from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import List


class Settings(BaseSettings):
    """Application settings from environment variables"""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore"
    )

    # MEXC API credentials
    mexc_api_key: str = ""
    mexc_api_secret: str = ""

    # Trading mode
    dry_run: bool = True

    # Trading pairs
    trading_pairs: List[str] = ["BTC/USDT", "ETH/USDT", "SOL/USDT", "XRP/USDT"]

    # Strategy parameters
    strategy_interval: int = 60  # Run strategy every N seconds
    data_poll_interval: int = 10  # Poll market data every N seconds

    # GREEN filter thresholds (momentum breakout strategy)
    green2_bb_period: int = 20
    green2_bb_std: float = 2.0
    green2_lookback_hours: int = 48  # Hours of BB width history for percentile calc
    green3_volume_threshold: float = 1.5  # Volume Z-score threshold
    green3_enabled: bool = False  # Disabled by default (market volume too low)
    green4_breakout_period: int = 10
    green4_breakout_buffer_bps: int = 10  # Basis points (0.1%)

    # Dynamic Squeeze Policy parameters
    squeeze_no_signal_timeout_minutes: int = 30  # Relax after N minutes without signals
    squeeze_permissive_duration_minutes: int = 15  # Stay permissive for M minutes
    squeeze_dry_run_loss_breaker_pct: float = 2.0  # Auto-revert if losses exceed this %
    squeeze_enable_monitoring: bool = True  # Enable dry-run monitoring
    squeeze_enable_relaxed_session: bool = True  # Enable temporary relaxed thresholds
    squeeze_relaxed_session_hours: int = 48  # Auto-revert after N hours

    # Position sizing
    position_size_usd: float = 100.0  # Default position size
    max_positions: int = 3
    max_positions_per_symbol: int = 1  # Only 1 position per trading pair
    trade_cooldown_seconds: int = 300  # 5 minute cooldown between trades on same symbol

    # Risk management
    daily_loss_limit_pct: float = 3.0  # Stop trading if down 3% for the day

    # Take Profit / Stop Loss (Position Exit)
    take_profit_pct: float = 1.5  # Close position at +1.5% profit
    stop_loss_pct: float = 1.0  # Close position at -1.0% loss
    max_hold_hours: int = 24  # Close position after 24 hours regardless of PnL

    # Database
    database_path: str = "scalperbot/trades.db"

    # Telegram (optional)
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    # Logging
    log_level: str = "INFO"
    log_file: str = "bot.log"


# Global settings instance
settings = Settings()
