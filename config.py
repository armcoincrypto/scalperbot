"""
ScalperBot Configuration
Pydantic-based settings loaded from .env file
"""
from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import List, Dict
import json


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

    # Strategy selection: "momentum_breakout" (original) or "smart_breakout" (improved)
    strategy_type: str = "smart_breakout"

    # GREEN filter thresholds (momentum breakout strategy - ORIGINAL)
    green2_bb_period: int = 20
    green2_bb_std: float = 2.0
    green3_volume_threshold: float = 1.2  # Volume Z-score threshold (lowered from 1.5)
    green3_enabled: bool = True  # ENABLED - important for signal quality
    green4_breakout_period: int = 10
    green4_breakout_buffer_bps: int = 10  # Basis points (0.1%)

    # Smart Breakout Strategy parameters (IMPROVED)
    smart_ema_period: int = 20  # EMA period for trend detection
    smart_rsi_period: int = 14  # RSI period
    smart_atr_period: int = 14  # ATR period for dynamic TP/SL
    smart_volume_multiplier: float = 1.5  # Volume must be this multiple of average
    smart_breakout_lookback: int = 20  # Lookback for resistance detection
    smart_rsi_oversold: int = 40  # RSI lower bound
    smart_rsi_overbought: int = 70  # RSI upper bound (avoid buying overbought)
    smart_htf_rsi_limit: int = 75  # Higher timeframe RSI limit
    smart_use_dynamic_targets: bool = True  # Use ATR-based TP/SL instead of fixed

    # Position sizing
    position_size_usd: float = 100.0  # Default/fallback position size
    max_positions: int = 3

    # Volatility-based position sizing (expert recommendation #6)
    use_volatility_sizing: bool = True  # Enable risk-based sizing
    risk_per_trade_pct: float = 0.3  # Risk 0.3% of equity per trade
    min_position_usd: float = 10.0  # Minimum position size
    max_position_usd: float = 500.0  # Maximum position size cap

    # Correlated exposure management (expert recommendation #8)
    max_correlated_positions: int = 2  # Max positions in same correlation group

    # Per-symbol minimum notional (to avoid MIN_NOTIONAL errors)
    # Format: JSON string like '{"BTC/USDT": 200, "ETH/USDT": 100}'
    per_symbol_min_notional: str = '{"BTC/USDT": 200, "ETH/USDT": 100, "SOL/USDT": 25, "XRP/USDT": 25, "LTC/USDT": 25}'

    # Take Profit / Stop Loss settings
    take_profit_pct: float = 2.0  # Take profit at 2% gain (was 1.5%)
    stop_loss_pct: float = 1.0  # Stop loss at 1% loss

    # Trailing stop settings
    trailing_enabled: bool = True  # Enable trailing stop
    trail_start_pct: float = 0.5  # Start trailing when +0.5% profit reached
    trail_offset_pct: float = 0.15  # Trail offset from peak (break-even + cushion)

    # Time-based exit
    max_hold_hours: float = 6.0  # Close position after N hours (was unlimited)

    # Pullback entry (better entries - avoid buying at the top)
    pullback_entry_enabled: bool = True  # Wait for small retracement
    pullback_entry_pct: float = 0.3  # Require 0.3% pullback from breakout
    pullback_max_candles: int = 3  # Max candles to wait for pullback

    # Risk management
    daily_loss_limit_pct: float = 3.0  # Stop trading if down 3% for the day

    # Position monitoring interval
    position_check_interval: int = 10  # Check positions every N seconds

    # Database
    database_path: str = "scalperbot/trades.db"

    # Telegram (optional)
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    # Logging
    log_level: str = "INFO"
    log_file: str = "bot.log"

    def get_min_notional(self, symbol: str) -> float:
        """Get minimum notional for a symbol"""
        try:
            min_notional_dict = json.loads(self.per_symbol_min_notional)
            return min_notional_dict.get(symbol, self.position_size_usd)
        except json.JSONDecodeError:
            return self.position_size_usd


# Global settings instance
settings = Settings()
