"""
Telegram command handlers.
"""

from aiogram import Router, F
from aiogram.types import Message
from aiogram.filters import Command, CommandStart
from typing import Optional, Callable, Awaitable

from scalperbot.config import settings
from scalperbot.log import get_logger
from scalperbot.storage.db import get_database
from scalperbot.storage.repo import PositionRepo, TradeRepo, SignalRepo

logger = get_logger(__name__)

router = Router()

# Engine reference (set by bot.py)
_engine = None
_start_callback: Optional[Callable[[], Awaitable]] = None
_stop_callback: Optional[Callable[[], Awaitable]] = None


def set_engine(engine):
    """Set engine reference for handlers."""
    global _engine
    _engine = engine


def set_callbacks(start_cb, stop_cb):
    """Set start/stop callbacks."""
    global _start_callback, _stop_callback
    _start_callback = start_cb
    _stop_callback = stop_cb


def is_admin(user_id: int) -> bool:
    """Check if user is admin."""
    return settings.is_admin(user_id)


@router.message(CommandStart())
async def cmd_start(message: Message):
    """Handle /start command."""
    mode = "DRY_RUN" if settings.dry_run else "LIVE"
    text = (
        f"ScalperBot v1.0\n"
        f"Mode: {mode}\n\n"
        "Commands:\n"
        "/run - Start trading engine\n"
        "/stop - Stop trading engine\n"
        "/status - Show current status\n"
        "/watchlist - Show watchlist\n"
        "/setcoins BTCUSDT,ETHUSDT - Update watchlist\n"
        "/signal BTCUSDT +2 - Add external signal\n"
        "/positions - Show open positions\n"
        "/pnl - Show daily PnL summary\n"
        "/debug - Show debug info"
    )
    await message.answer(text)


@router.message(Command("run"))
async def cmd_run(message: Message):
    """Handle /run command - start trading."""
    if not is_admin(message.from_user.id):
        await message.answer("Not authorized")
        return

    if _engine and _engine.running:
        await message.answer("Engine already running")
        return

    if _start_callback:
        await message.answer("Starting engine...")
        await _start_callback()
    else:
        await message.answer("Engine not configured")


@router.message(Command("stop"))
async def cmd_stop(message: Message):
    """Handle /stop command - stop trading."""
    if not is_admin(message.from_user.id):
        await message.answer("Not authorized")
        return

    if _engine and not _engine.running:
        await message.answer("Engine not running")
        return

    if _stop_callback:
        await message.answer("Stopping engine...")
        await _stop_callback()
    else:
        await message.answer("Engine not configured")


@router.message(Command("status"))
async def cmd_status(message: Message):
    """Handle /status command."""
    if not _engine:
        await message.answer("Engine not initialized")
        return

    status = await _engine.get_status()

    text = (
        f"Status: {'RUNNING' if status['running'] else 'STOPPED'}\n"
        f"Mode: {status['mode']}\n"
        f"Uptime: {status['uptime'] or 'N/A'}\n"
        f"Watchlist: {len(status['watchlist'])} symbols\n"
        f"Open positions: {status['open_positions']}\n"
        f"Daily PnL: ${status['daily_pnl']:.2f}\n"
        f"Daily trades: {status['daily_trades']}\n"
        f"Win rate: {status['win_rate']:.1f}%"
    )
    await message.answer(text)


@router.message(Command("watchlist"))
async def cmd_watchlist(message: Message):
    """Handle /watchlist command."""
    if not _engine:
        await message.answer("Engine not initialized")
        return

    symbols = _engine.watchlist or settings.watchlist_symbols
    if not symbols:
        await message.answer("Watchlist is empty")
        return

    text = "Watchlist:\n" + "\n".join(f"  {s}" for s in symbols)
    await message.answer(text)


@router.message(Command("setcoins"))
async def cmd_setcoins(message: Message):
    """Handle /setcoins command - update watchlist."""
    if not is_admin(message.from_user.id):
        await message.answer("Not authorized")
        return

    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await message.answer("Usage: /setcoins BTCUSDT,ETHUSDT,BNBUSDT")
        return

    symbols = [s.strip().upper() for s in args[1].split(",") if s.strip()]
    if not symbols:
        await message.answer("No valid symbols provided")
        return

    if _engine:
        _engine.set_watchlist(symbols)

    await message.answer(f"Watchlist updated: {', '.join(symbols)}")


@router.message(Command("signal"))
async def cmd_signal(message: Message):
    """Handle /signal command - add external signal."""
    if not is_admin(message.from_user.id):
        await message.answer("Not authorized")
        return

    args = message.text.split()
    if len(args) < 3:
        await message.answer("Usage: /signal BTCUSDT +2")
        return

    symbol = args[1].upper()
    try:
        weight = float(args[2])
    except ValueError:
        await message.answer("Invalid weight. Use number like +2 or -3")
        return

    try:
        db = await get_database()
        signals = SignalRepo(db)
        await signals.add_signal(symbol, weight, source="telegram")
        await message.answer(f"Signal added: {symbol} {weight:+.1f}")
    except Exception as e:
        await message.answer(f"Error: {e}")


@router.message(Command("positions"))
async def cmd_positions(message: Message):
    """Handle /positions command."""
    try:
        db = await get_database()
        positions = PositionRepo(db)
        open_pos = await positions.get_open()

        if not open_pos:
            await message.answer("No open positions")
            return

        lines = ["Open positions:"]
        for p in open_pos:
            pnl_pct = ((p['current_price'] - p['entry_price']) / p['entry_price']) * 100
            lines.append(
                f"\n{p['symbol']}:\n"
                f"  Entry: {p['entry_price']:.6f}\n"
                f"  Current: {p['current_price']:.6f}\n"
                f"  PnL: {pnl_pct:+.2f}%\n"
                f"  SL: {p['stop_loss']:.6f}\n"
                f"  TP: {p['take_profit']:.6f}"
            )

        await message.answer("\n".join(lines))
    except Exception as e:
        await message.answer(f"Error: {e}")


@router.message(Command("pnl"))
async def cmd_pnl(message: Message):
    """Handle /pnl command - daily summary."""
    try:
        db = await get_database()
        trades = TradeRepo(db)
        summary = await trades.get_daily_summary()

        text = (
            f"Daily Summary:\n"
            f"  Trades: {summary['total_trades']}\n"
            f"  Winning: {summary['winning_trades']}\n"
            f"  Losing: {summary['losing_trades']}\n"
            f"  Win rate: {summary['win_rate']:.1f}%\n"
            f"  Total PnL: ${summary['total_pnl']:.2f}"
        )
        await message.answer(text)
    except Exception as e:
        await message.answer(f"Error: {e}")


@router.message(Command("debug"))
async def cmd_debug(message: Message):
    """Handle /debug command - show debug info."""
    if not is_admin(message.from_user.id):
        await message.answer("Not authorized")
        return

    info = settings.get_safe_debug_info()
    lines = ["Debug info:"]
    for key, value in info.items():
        lines.append(f"  {key}: {value}")

    await message.answer("\n".join(lines))
