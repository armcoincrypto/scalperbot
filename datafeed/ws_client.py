import asyncio
import json
import time
import hmac
import hashlib
from typing import Callable
import websockets
from config import config

class MEXCWebSocket:
    BASE = "wss://wbs.mexc.com/ws"

    def __init__(self, on_message: Callable):
        self.on_message = on_message
        self.ws = None
        self.ping_task = None

    async def _sign(self) -> str:
        if not config.api_key:
            return ""
        ts = str(int(time.time() * 1000))
        msg = config.api_key + ts
        sign = hmac.new(config.api_secret.encode(), msg.encode(), hashlib.sha256).hexdigest()
        return json.dumps({"method": "LOGIN", "params": [config.api_key, ts, sign]})

    async def connect(self):
        self.ws = await websockets.connect(self.BASE, ping_interval=None)
        await self.ws.send(json.dumps({
            "method": "SUBSCRIPTION",
            "params": [
                "spot@public.deals.v3.api@BTCUSDT",
                "spot@public.depth.v3.api@BTCUSDT@5",
                "spot@public.kline.v3.api@BTCUSDT@Min1"
            ]
        }))
        login = await self._sign()
        if login:
            await self.ws.send(login)
        self.ping_task = asyncio.create_task(self._keep_alive())
        asyncio.create_task(self._listener())

    async def _keep_alive(self):
        while True:
            await asyncio.sleep(20)
            try:
                await self.ws.send(json.dumps({"method": "PING"}))
            except:
                break

    async def _listener(self):
        async for msg in self.ws:
            data = json.loads(msg)
            await self.on_message(data)

    async def subscribe_symbol(self, symbol: str):
        s = symbol.replace('/', '').upper()
        await self.ws.send(json.dumps({
            "method": "SUBSCRIPTION",
            "params": [
                f"spot@public.deals.v3.api@{s}",
                f"spot@public.depth.v3.api@{s}@5",
                f"spot@public.kline.v3.api@{s}@Min1"
            ]
        }))

    async def close(self):
        if self.ping_task:
            self.ping_task.cancel()
        if self.ws:
            await self.ws.close()
