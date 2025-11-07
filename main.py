import asyncio
import logging
from datetime import datetime
from config import config
from datafeed import MEXCWebSocket, CandleStore, OrderBook
from strategies.momentum_breakout import MomentumBreakout
from risk.sizer import calculate_size
from risk.breakers import Breakers
from exec.router import ExecutionRouter
from ops.telebot import start_telebot

logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S PST',
    handlers=[logging.FileHandler("scalperbot.log"), logging.StreamHandler()]
)
log = logging.getLogger()

candles = CandleStore()
books = {}
strategies = {}
router = ExecutionRouter()
breakers = Breakers(config)

async def on_message(msg):
    channel = msg.get('channel', '')
    data = msg.get('data', {})
    symbol = msg.get('symbol', '').replace('USDT', '/USDT')

    if 'kline' in channel and symbol:
        kline = data.get('kline', [])
        if kline:
            candles.update(symbol, kline)

    elif 'depth' in channel and symbol:
        if symbol not in books:
            books[symbol] = OrderBook()
        book = books[symbol]
        if 'lastUpdateId' in data:
            book.apply_snapshot(data)
        else:
            book.apply_delta(data)

    elif 'deals' in channel and symbol:
        price = float(data.get('price', 0))
        log.info(f"TRADE {symbol} {price}")

async def trading_loop(symbol):
    log.info(f"SUB {symbol} subscribed")
    strategies[symbol] = MomentumBreakout(symbol, config)
    await strategies[symbol].warmup({'1m': candles.ohlcv(symbol), '1h': candles.ohlcv(symbol, '1h')})

    trade_count = {symbol: 0}
    while True:
        await asyncio.sleep(5)
        data = {'1m': candles.ohlcv(symbol), '1h': candles.ohlcv(symbol, '1h')}
        signal = await strategies[symbol].generate_signal(data)
        if signal and not breakers.check_daily_loss():
            price = data['1m']['close'].iloc[-1]
            size = calculate_size(config.equity_usd_cap, price, signal['sl'], config.risk_pct)
            log.info(f"SIGNAL {signal['side'].upper()} {symbol} @ {price:.2f}")
            order = await router.place_maker_order(symbol, signal['side'], size, price)
            if order.get('status') == 'filled':
                trade_count[symbol] += 1
                log.info(f"FILL {symbol} FILLED @ {order.get('price', price):.2f}")
                pnl = (signal['tp'] - price) * size if signal['side'] == 'buy' else (price - signal['tp']) * size
                log.info(f"PNL {symbol} #{trade_count[symbol]}: +${pnl:.2f}")

async def main():
    log.info(f"ScalperBot STARTED | MODE={config.mode} | EXCHANGE={config.exchange}")
    ws = MEXCWebSocket(on_message)
    await ws.connect()

    symbols = ['BTC/USDT', 'ETH/USDT', 'SOL/USDT', 'XRP/USDT']
    for sym in symbols:
        await ws.subscribe_symbol(sym)

    tasks = [trading_loop(sym) for sym in symbols]
    tasks.append(start_telebot())
    await asyncio.gather(*tasks)

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("Bot stopped by user")
