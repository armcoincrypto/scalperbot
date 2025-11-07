import ccxt.pro as ccxtpro
from config import config
import asyncio

class ExchangeAdapter:
    def __init__(self):
        exchange_class = getattr(ccxtpro, config.exchange, None)
        if not exchange_class:
            raise ValueError(f"Exchange {config.exchange} not supported")
        self.exchange = exchange_class({
            'apiKey': config.api_key,
            'secret': config.api_secret,
            'enableRateLimit': True,
            'options': {'defaultType': 'spot'},
        })
        self.markets = {}

    async def load_markets(self):
        self.markets = await self.exchange.load_markets()

    async def create_order(self, symbol, type_, side, amount, price=None, params=None):
        if params is None:
            params = {}
        if config.mode == 'DRY_RUN':
            print(f"[DRY] {side.upper()} {symbol} {amount:.6f} @ {price:.2f}")
            return {'id': 'dry', 'status': 'filled', 'filled': amount}
        return await self.exchange.create_order(symbol, type_, side, amount, price, params)

    async def close(self):
        await self.exchange.close()
