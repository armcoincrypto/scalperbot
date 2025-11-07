import os
from dotenv import load_dotenv
import yaml
from typing import Dict, Any

load_dotenv()

class Config:
    def __init__(self):
        self.mode = os.getenv('MODE', 'DRY_RUN').upper()
        self.base_currency = os.getenv('BASE_CURRENCY', 'USDT')
        self.equity_usd_cap = float(os.getenv('EQUITY_USD_CAP', 10000))
        self.timezone = os.getenv('TIMEZONE', 'UTC')

        self.exchange = os.getenv('EXCHANGE', 'binance').lower()
        self.api_key = os.getenv('API_KEY')
        self.api_secret = os.getenv('API_SECRET')
        self.subaccount = os.getenv('SUBACCOUNT')
        self.proxy_url = os.getenv('PROXY_URL')

        self.risk_pct = float(os.getenv('RISK_PCT', 0.008))
        self.daily_loss_pct = float(os.getenv('DAILY_LOSS_PCT', 0.03))
        self.weekly_dd_pct = float(os.getenv('WEEKLY_DD_PCT', 0.08))
        self.max_concurrent = int(os.getenv('MAX_CONCURRENT', 4))

        self.strategy = os.getenv('STRATEGY', 'momentum_breakout').lower()
        self.tp_bps = int(os.getenv('TP_BPS', 60))
        self.sl_bps = int(os.getenv('SL_BPS', 35))
        self.trail_bps = int(os.getenv('TRAIL_BPS', 25))
        self.vol_z = float(os.getenv('VOL_Z', 1.5))
        self.min_depth_usd = int(os.getenv('MIN_DEPTH_USD', 250000))

        self.ws_reconnect_jitter = os.getenv('WS_RECONNECT_JITTER', '0.3-1.2')
        self.candle_backfill_min = int(os.getenv('CANDLE_BACKFILL_MIN', 2000))

        self.telegram_bot_token = os.getenv('TELEGRAM_BOT_TOKEN')
        self.admin_chat_id = os.getenv('ADMIN_CHAT_ID')
        self.prometheus_port = int(os.getenv('PROMETHEUS_PORT', 8000))
        self.log_level = os.getenv('LOG_LEVEL', 'INFO').upper()

        self.strategy_presets = self._load_yaml_presets()

    def _load_yaml_presets(self) -> Dict[str, Any]:
        path = 'strategy_presets.yaml'
        if os.path.exists(path):
            with open(path, 'r') as f:
                return yaml.safe_load(f) or {}
        return {}

config = Config()
