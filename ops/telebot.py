from aiogram import Bot, Dispatcher, types
from config import config
import asyncio

bot = Bot(token=config.telegram_bot_token)
dp = Dispatcher()

@dp.message()
async def handle(message: types.Message):
    text = message.text.strip().lower()
    if text == '/start_bot':
        await message.reply("Bot started! 🚀")
    elif text == '/stop_bot':
        await message.reply("Bot stopped. 🛑")
    elif text == '/status':
        await message.reply(f"<b>Status</b>\nMode: {config.mode}\nStrategy: {config.strategy}")
    elif text.startswith('/mode '):
        mode = text.split()[1].upper()
        if mode in ['DRY_RUN', 'LIVE']:
            config.mode = mode
            await message.reply(f"Mode → {mode}")
        else:
            await message.reply("Use: /mode DRY_RUN or /mode LIVE")
    elif text.startswith('/risk '):
        try:
            risk = float(text.split()[1])
            config.risk_pct = risk / 100
            await message.reply(f"Risk per trade → {risk}%")
        except:
            await message.reply("Use: /risk 0.8")

async def start_telebot():
    print("Telegram bot running...")
    await dp.start_polling(bot)
