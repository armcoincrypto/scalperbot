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

    # Trading pairs (10 pairs - major coins with good liquidity on MEXC)
    trading_pairs: List[str] = [
        "BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT", "BCH/USDT",
        "LTC/USDT", "XLM/USDT", "ADA/USDT", "TRX/USDT", "DOGE/USDT"
    ]

    # Strategy parameters
    strategy_interval: int = 60  # Run strategy every N seconds
    data_poll_interval: int = 10  # Poll market data every N seconds

    # GREEN filter thresholds (momentum breakout strategy)
    green2_bb_period: int = 20
    green2_bb_std: float = 2.0
    green3_volume_threshold: float = 1.2  # Volume Z-score threshold (lower = more signals)
    green3_enabled: bool = True  # Enable volume confirmation filter
    green4_breakout_period: int = 10
    green4_breakout_buffer_bps: int = 10  # Basis points (0.1%)

    # Position sizing
    position_size_usd: float = 100.0  # Default position size
    max_positions: int = 3

    # Per-symbol minimum notional (to avoid failed orders)
    # Set higher for expensive coins, lower for cheap ones
    per_symbol_min_notional: dict = {
        "BTC/USDT": 200.0,
        "ETH/USDT": 100.0,
        "default": 25.0
    }

    # Risk management
    daily_loss_limit_pct: float = 3.0  # Stop trading if down 3% for the day

    # Exit settings (Take Profit / Stop Loss)
    take_profit_pct: float = 2.0  # Close position when profit reaches X% (was 1.5%)
    stop_loss_pct: float = 1.0  # Close position when loss reaches X%

    # Advanced exit settings
    max_hold_hours: float = 6.0  # Close position after N hours (0 = disabled)
    trailing_stop_enabled: bool = True  # Enable trailing stop
    trailing_start_pct: float = 0.5  # Start trailing after +X% profit
    trailing_offset_pct: float = 0.3  # Trail by X% from high

    # Database
    database_path: str = "scalperbot/trades.db"

    # Telegram (optional)
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    # Telegram Status Reports
    status_enabled: bool = True  # Enable hourly status reports
    status_interval_min: int = 60  # Send status every N minutes
    early_warn_enabled: bool = False  # Enable early warning alerts (disabled by default)
    early_warn_pct: float = 0.1  # Alert when price within X% of breakout
    early_warn_cooldown_min: int = 60  # One early warning per symbol per N minutes

    # Logging
    log_level: str = "INFO"
    log_file: str = "bot.log"


# Global settings instance
settings = Settings()
