"""
Telegram command handlers.
"""

from aiogram import Router, F
from aiogram.types import Message
from aiogram.filters import Command, CommandStart
from typing import Optional, Callable, Awaitable
from datetime import datetime, timezone, timedelta

from scalperbot.config import settings
from scalperbot.log import get_logger
from scalperbot.storage.db import get_database
from scalperbot.storage.repo import (
    PositionRepo, TradeRepo, SignalRepo, TickRepo, OutcomeRepo
)
from scalperbot.core.circuit_breaker import get_circuit_breaker

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
    liq = settings.liq_mode.upper()
    text = (
        f"ScalperBot v1.1\n"
        f"Mode: {mode} | Liquidity: {liq}\n\n"
        "TRADING:\n"
        "/run - Start trading engine\n"
        "/stop - Stop trading engine\n"
        "/status - Show current status\n"
        "/panic - Emergency kill switch\n\n"
        "CONFIGURATION:\n"
        "/watchlist - Show watchlist\n"
        "/setcoins X,Y,Z - Update watchlist\n"
        "/signal BTCUSDT +2 - Add signal\n"
        "/debug - Show debug info\n\n"
        "MONITORING:\n"
        "/positions - Open positions\n"
        "/pnl - Daily PnL summary\n"
        "/report - Detailed 24h report\n"
        "/signals - Recent signal quality\n"
        "/best - Best/worst symbols\n"
        "/breakers - Circuit breaker status"
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


@router.message(Command("report"))
async def cmd_report(message: Message):
    """Handle /report command - detailed 24h performance report."""
    try:
        db = await get_database()
        trades = TradeRepo(db)
        ticks = TickRepo(db)
        outcomes = OutcomeRepo(db)

        # Get daily summary
        summary = await trades.get_daily_summary()

        # Get tick decision counts
        decision_counts = await ticks.count_by_decision(hours=24)

        # Get outcome stats
        outcome_stats = await outcomes.get_stats()

        # Calculate additional metrics
        total_candidates = decision_counts.get("CANDIDATE", 0)
        total_filtered = decision_counts.get("FILTERED", 0)
        total_no_signal = decision_counts.get("NO_SIGNAL", 0)
        total_ticks = sum(decision_counts.values())

        # Profit factor
        wins = summary.get('winning_trades', 0)
        losses = summary.get('losing_trades', 0)
        win_rate = summary.get('win_rate', 0)

        # Circuit breaker status
        cb = get_circuit_breaker()
        cb_status = cb.get_status()

        text = (
            f"24H PERFORMANCE REPORT\n"
            f"{'='*30}\n\n"
            f"Trades: {summary['total_trades']}\n"
            f"  Wins: {wins} | Losses: {losses}\n"
            f"  Win Rate: {win_rate:.1f}%\n"
            f"  Total PnL: ${summary['total_pnl']:.2f}\n\n"
            f"SIGNAL ANALYSIS:\n"
            f"  Total ticks analyzed: {total_ticks}\n"
            f"  Candidates found: {total_candidates}\n"
            f"  Filtered out: {total_filtered}\n"
            f"  No signal: {total_no_signal}\n\n"
            f"OUTCOME ANALYSIS:\n"
        )

        if outcome_stats and outcome_stats.get('total'):
            tp_wins = outcome_stats.get('tp_wins', 0)
            sl_losses = outcome_stats.get('sl_losses', 0)
            total_outcomes = outcome_stats.get('total', 1)
            text += (
                f"  Signals analyzed: {total_outcomes}\n"
                f"  TP hit: {tp_wins} ({tp_wins/total_outcomes*100:.1f}%)\n"
                f"  SL hit: {sl_losses} ({sl_losses/total_outcomes*100:.1f}%)\n"
                f"  Avg 5m return: {outcome_stats.get('avg_return_5m', 0):.2f}%\n"
                f"  Avg MFE 5m: {outcome_stats.get('avg_mfe_5m', 0):.2f}%\n"
                f"  Avg MAE 5m: {outcome_stats.get('avg_mae_5m', 0):.2f}%\n"
            )
        else:
            text += "  No outcome data yet (collecting...)\n"

        text += (
            f"\nCIRCUIT BREAKERS:\n"
            f"  Can trade: {'YES' if cb_status['can_trade'] else 'NO'}\n"
            f"  Daily loss: ${cb_status['daily_pnl']:.2f} / ${cb_status['limits']['max_daily_loss']:.2f}\n"
            f"  Trades: {cb_status['daily_trades']} / {cb_status['limits']['max_daily_trades']}\n"
        )

        if cb_status['paused_symbols']:
            text += f"  Paused symbols: {', '.join(cb_status['paused_symbols'])}\n"

        await message.answer(text)
    except Exception as e:
        logger.error(f"Report error: {e}", exc_info=True)
        await message.answer(f"Error generating report: {e}")


@router.message(Command("signals"))
async def cmd_signals(message: Message):
    """Handle /signals command - show recent signal quality."""
    try:
        db = await get_database()
        ticks = TickRepo(db)

        # Get recent signals
        recent_signals = await ticks.get_signals_only(limit=20)

        if not recent_signals:
            await message.answer("No signals recorded yet")
            return

        lines = ["RECENT SIGNALS (last 20):"]
        lines.append("-" * 30)

        for sig in recent_signals[:10]:  # Show last 10
            ts = sig['timestamp'][:16] if sig.get('timestamp') else 'N/A'
            symbol = sig['symbol']
            score = sig.get('score_total', 0)
            decision = sig.get('decision', 'N/A')
            reason = sig.get('decision_reason', '')[:20]

            lines.append(
                f"\n{ts}\n"
                f"  {symbol}: Score {score:.2f}\n"
                f"  Decision: {decision}\n"
                f"  Reason: {reason}"
            )

        # Summary
        candidates = sum(1 for s in recent_signals if s.get('decision') == 'CANDIDATE')
        filtered = sum(1 for s in recent_signals if s.get('decision') == 'FILTERED')

        lines.append(f"\n{'='*30}")
        lines.append(f"Summary: {candidates} candidates, {filtered} filtered")

        await message.answer("\n".join(lines))
    except Exception as e:
        await message.answer(f"Error: {e}")


@router.message(Command("best"))
async def cmd_best(message: Message):
    """Handle /best command - show best/worst symbols from outcomes."""
    try:
        db = await get_database()

        # Query best/worst symbols by average return
        query = """
            SELECT
                t.symbol,
                COUNT(*) as signal_count,
                AVG(o.return_5m) as avg_return,
                SUM(CASE WHEN o.first_hit = 'TP' THEN 1 ELSE 0 END) as tp_hits,
                SUM(CASE WHEN o.first_hit = 'SL' THEN 1 ELSE 0 END) as sl_hits
            FROM ticks t
            JOIN outcomes o ON t.id = o.tick_id
            WHERE t.is_signal = 1
            GROUP BY t.symbol
            HAVING signal_count >= 5
            ORDER BY avg_return DESC
        """

        rows = await db.fetch_all(query)

        if not rows:
            await message.answer("Not enough data yet. Need at least 5 signals per symbol with outcomes.")
            return

        lines = ["SYMBOL PERFORMANCE ANALYSIS"]
        lines.append("=" * 30)
        lines.append("\nBest performers:")

        for i, row in enumerate(rows[:3]):
            wr = row['tp_hits'] / row['signal_count'] * 100 if row['signal_count'] > 0 else 0
            lines.append(
                f"  {i+1}. {row['symbol']}: {row['avg_return']:.2f}% avg "
                f"({row['signal_count']} signals, {wr:.0f}% TP rate)"
            )

        if len(rows) > 3:
            lines.append("\nWorst performers:")
            for i, row in enumerate(reversed(rows[-3:])):
                wr = row['tp_hits'] / row['signal_count'] * 100 if row['signal_count'] > 0 else 0
                lines.append(
                    f"  {i+1}. {row['symbol']}: {row['avg_return']:.2f}% avg "
                    f"({row['signal_count']} signals, {wr:.0f}% TP rate)"
                )

        # Parameter suggestions based on outcomes
        lines.append("\n" + "=" * 30)
        lines.append("PARAMETER SUGGESTIONS:")

        # Check if current thresholds are good
        outcomes = OutcomeRepo(db)
        stats = await outcomes.get_stats()

        if stats and stats.get('total', 0) > 20:
            tp_rate = stats.get('tp_wins', 0) / stats.get('total', 1) * 100
            avg_mfe = stats.get('avg_mfe_5m', 0)
            avg_mae = abs(stats.get('avg_mae_5m', 0))

            if tp_rate < 40:
                lines.append("  - Win rate low: Consider raising score threshold")
            if avg_mae > settings.base_sl_pct:
                lines.append(f"  - MAE ({avg_mae:.1f}%) > SL ({settings.base_sl_pct}%): Widen SL?")
            if avg_mfe < settings.take_profit_pct:
                lines.append(f"  - MFE ({avg_mfe:.1f}%) < TP ({settings.take_profit_pct}%): Lower TP?")

            lines.append(f"\n  Current: TP={settings.take_profit_pct}%, SL={settings.base_sl_pct}%")
            lines.append(f"  Observed: MFE={avg_mfe:.1f}%, MAE={avg_mae:.1f}%")
        else:
            lines.append("  Need more data (20+ labeled signals)")

        await message.answer("\n".join(lines))
    except Exception as e:
        logger.error(f"Best command error: {e}", exc_info=True)
        await message.answer(f"Error: {e}")


@router.message(Command("panic"))
async def cmd_panic(message: Message):
    """Handle /panic command - emergency kill switch."""
    if not is_admin(message.from_user.id):
        await message.answer("Not authorized")
        return

    cb = get_circuit_breaker()
    cb.kill_switch("Manual panic via Telegram")

    # Stop engine if running
    if _engine and _engine.running:
        await _engine.stop()

    await message.answer(
        "KILL SWITCH ACTIVATED\n"
        "- All trading stopped\n"
        "- Engine halted\n\n"
        "Use /reset_panic to resume"
    )


@router.message(Command("reset_panic"))
async def cmd_reset_panic(message: Message):
    """Handle /reset_panic command - reset kill switch."""
    if not is_admin(message.from_user.id):
        await message.answer("Not authorized")
        return

    cb = get_circuit_breaker()
    cb.reset_kill_switch()

    await message.answer("Kill switch reset. Use /run to start trading.")


@router.message(Command("breakers"))
async def cmd_breakers(message: Message):
    """Handle /breakers command - show circuit breaker status."""
    cb = get_circuit_breaker()
    status = cb.get_status()

    text = (
        f"CIRCUIT BREAKER STATUS\n"
        f"{'='*30}\n\n"
        f"Can Trade: {'YES' if status['can_trade'] else 'NO'}\n"
    )

    if not status['can_trade']:
        text += f"Blocked: {status['blocked_reason']}\n"

    text += (
        f"\nDaily Stats:\n"
        f"  PnL: ${status['daily_pnl']:.2f}\n"
        f"  Trades: {status['daily_trades']}\n"
        f"  Wins/Losses: {status['daily_wins']}/{status['daily_losses']}\n"
        f"  Win Rate: {status['win_rate']:.1f}%\n\n"
        f"Limits:\n"
        f"  Max daily loss: ${status['limits']['max_daily_loss']:.2f}\n"
        f"  Max daily trades: {status['limits']['max_daily_trades']}\n"
        f"  Max consecutive losses: {status['limits']['max_consecutive_losses']}\n\n"
        f"Breakers Active:\n"
        f"  Daily loss: {'TRIPPED' if status['breakers_active']['daily_loss'] else 'OK'}\n"
        f"  Max trades: {'TRIPPED' if status['breakers_active']['max_trades'] else 'OK'}\n"
        f"  API storm: {'TRIPPED' if status['breakers_active']['api_storm'] else 'OK'}\n"
        f"  Kill switch: {'ACTIVE' if status['is_killed'] else 'OFF'}\n"
    )

    if status['paused_symbols']:
        text += f"\nPaused symbols: {', '.join(status['paused_symbols'])}"

    text += f"\n\nAPI errors (1m/5m): {status['api_errors_1m']}/{status['api_errors_5m']}"

    await message.answer(text)
