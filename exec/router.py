from exchanges.ccxt_adapter import ExchangeAdapter
from config import config

class ExecutionRouter:
    def __init__(self):
        self.adapter = ExchangeAdapter()

    async def place_maker_order(self, symbol, side, size, price):
        if config.mode == 'DRY_RUN':
            print(f"[DRY] {side.upper()} {symbol} {size:.6f} @ {price:.2f}")
            return {'id': 'dry', 'status': 'filled'}
        return await self.adapter.create_order(
            symbol, 'limit', side, size, price,
            params={'postOnly': True}
        )
