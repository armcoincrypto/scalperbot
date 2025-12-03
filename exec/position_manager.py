"""
Position Manager - Tracks open positions and handles exit logic (TP/SL)
"""
import logging
from typing import Dict, List, Optional
from datetime import datetime, timezone
from config import settings

logger = logging.getLogger(__name__)


class PositionManager:
    """
    Manages open positions and monitors for exit conditions:
    - Take profit (TP)
    - Stop loss (SL)
    - Trailing stop (optional)
    - Max hold time (optional)
    """

    def __init__(
        self,
        db,
        exchange,
        take_profit_pct: float = None,
        stop_loss_pct: float = None,
        enable_trailing_stop: bool = False,
        trailing_stop_pct: float = 0.5,
        max_hold_minutes: int = None
    ):
        self.db = db
        self.exchange = exchange
        self.take_profit_pct = take_profit_pct or getattr(settings, 'take_profit_pct', 1.5)
        self.stop_loss_pct = stop_loss_pct or getattr(settings, 'stop_loss_pct', 1.0)
        self.enable_trailing_stop = enable_trailing_stop
        self.trailing_stop_pct = trailing_stop_pct
        self.max_hold_minutes = max_hold_minutes

        # In-memory position tracking: {symbol: position_data}
        self.positions: Dict[str, Dict] = {}

        # High water marks for trailing stops
        self.high_water_marks: Dict[str, float] = {}

        logger.info(f"PositionManager initialized: TP={self.take_profit_pct}%, SL={self.stop_loss_pct}%")

    def has_open_position(self, symbol: str) -> bool:
        """Check if we have an open position for this symbol"""
        return symbol in self.positions

    def get_open_positions(self) -> Dict[str, Dict]:
        """Get all open positions"""
        return self.positions.copy()

    def open_position(
        self,
        symbol: str,
        entry_price: float,
        quantity: float,
        trade_id: int,
        side: str = 'buy'
    ):
        """Record a new open position"""
        self.positions[symbol] = {
            'symbol': symbol,
            'entry_price': entry_price,
            'quantity': quantity,
            'trade_id': trade_id,
            'side': side,
            'entry_time': datetime.now(timezone.utc),
            'high_price': entry_price  # For trailing stop
        }
        self.high_water_marks[symbol] = entry_price
        logger.info(f"Position opened: {symbol} @ {entry_price:.4f}, qty={quantity:.6f}")

    def close_position(self, symbol: str, exit_price: float, reason: str) -> Optional[Dict]:
        """
        Close a position and calculate PnL
        Returns position data with PnL info
        """
        if symbol not in self.positions:
            logger.warning(f"No open position for {symbol}")
            return None

        position = self.positions.pop(symbol)
        self.high_water_marks.pop(symbol, None)

        entry_price = position['entry_price']
        quantity = position['quantity']

        # Calculate PnL
        if position['side'] == 'buy':
            pnl = (exit_price - entry_price) * quantity
            pnl_pct = ((exit_price - entry_price) / entry_price) * 100
        else:
            pnl = (entry_price - exit_price) * quantity
            pnl_pct = ((entry_price - exit_price) / entry_price) * 100

        position['exit_price'] = exit_price
        position['exit_time'] = datetime.now(timezone.utc)
        position['pnl'] = pnl
        position['pnl_pct'] = pnl_pct
        position['exit_reason'] = reason

        logger.info(f"Position closed: {symbol} @ {exit_price:.4f}, PnL: ${pnl:.2f} ({pnl_pct:+.2f}%) - {reason}")

        return position

    def check_exits(self, current_prices: Dict[str, float]) -> List[Dict]:
        """
        Check all positions for exit conditions
        Returns list of positions that should be closed with reason
        """
        exits = []

        for symbol, position in list(self.positions.items()):
            if symbol not in current_prices:
                continue

            current_price = current_prices[symbol]
            entry_price = position['entry_price']

            # Update high water mark for trailing stop
            if current_price > self.high_water_marks.get(symbol, 0):
                self.high_water_marks[symbol] = current_price
                position['high_price'] = current_price

            # Calculate current PnL %
            pnl_pct = ((current_price - entry_price) / entry_price) * 100

            # Check take profit
            if pnl_pct >= self.take_profit_pct:
                exits.append({
                    'symbol': symbol,
                    'position': position,
                    'current_price': current_price,
                    'reason': f'TAKE_PROFIT ({pnl_pct:.2f}% >= {self.take_profit_pct}%)',
                    'pnl_pct': pnl_pct
                })
                continue

            # Check stop loss
            if pnl_pct <= -self.stop_loss_pct:
                exits.append({
                    'symbol': symbol,
                    'position': position,
                    'current_price': current_price,
                    'reason': f'STOP_LOSS ({pnl_pct:.2f}% <= -{self.stop_loss_pct}%)',
                    'pnl_pct': pnl_pct
                })
                continue

            # Check trailing stop
            if self.enable_trailing_stop and pnl_pct > 0:
                high_price = self.high_water_marks.get(symbol, entry_price)
                drop_from_high = ((high_price - current_price) / high_price) * 100

                if drop_from_high >= self.trailing_stop_pct:
                    exits.append({
                        'symbol': symbol,
                        'position': position,
                        'current_price': current_price,
                        'reason': f'TRAILING_STOP (dropped {drop_from_high:.2f}% from high)',
                        'pnl_pct': pnl_pct
                    })
                    continue

            # Check max hold time
            if self.max_hold_minutes:
                hold_time = (datetime.now(timezone.utc) - position['entry_time']).total_seconds() / 60
                if hold_time >= self.max_hold_minutes:
                    exits.append({
                        'symbol': symbol,
                        'position': position,
                        'current_price': current_price,
                        'reason': f'MAX_HOLD_TIME ({hold_time:.0f} min >= {self.max_hold_minutes} min)',
                        'pnl_pct': pnl_pct
                    })

        return exits

    def sync_from_db(self, verify_on_exchange: bool = False):
        """
        Sync positions from database on startup.
        In LIVE mode, also imports any untracked positions from exchange.

        Args:
            verify_on_exchange: If True, verify positions exist on exchange (LIVE mode)
        """
        try:
            open_trades = self.db.get_open_positions()

            # Get actual exchange positions
            exchange_positions = {}
            if verify_on_exchange:
                try:
                    balance = self.exchange.fetch_balance()
                    for currency, info in balance.items():
                        if isinstance(info, dict):
                            total = info.get('total', 0) or 0
                            if total > 0 and currency not in ['USDT', 'USD']:
                                exchange_positions[currency] = total
                    logger.info(f"Exchange balances: {exchange_positions}")
                except Exception as e:
                    logger.warning(f"Could not fetch exchange positions: {e}")

            # Group trades by symbol - critical for detecting duplicates
            trades_by_symbol: Dict[str, List[Dict]] = {}
            for trade in open_trades:
                symbol = trade['symbol']
                if symbol not in trades_by_symbol:
                    trades_by_symbol[symbol] = []
                trades_by_symbol[symbol].append(trade)

            synced_count = 0
            skipped_count = 0
            db_tracked_currencies = set()

            # Process each symbol - only keep ONE trade per symbol
            for symbol, trades in trades_by_symbol.items():
                base_currency = symbol.split('/')[0] if '/' in symbol else symbol
                db_tracked_currencies.add(base_currency)

                exchange_qty = exchange_positions.get(base_currency, 0) if verify_on_exchange else None

                # If multiple trades for same symbol, keep only the best match
                if len(trades) > 1:
                    qty_str = f"{exchange_qty:.6f}" if exchange_qty else "N/A"
                    logger.warning(
                        f"Found {len(trades)} duplicate DB entries for {symbol}! "
                        f"Exchange qty: {qty_str}"
                    )

                    if verify_on_exchange and exchange_qty > 0:
                        # Find the trade with quantity closest to exchange
                        best_trade = min(trades, key=lambda t: abs(t['quantity'] - exchange_qty))
                        logger.info(
                            f"Keeping trade ID {best_trade['id']} (qty={best_trade['quantity']:.6f}) "
                            f"as best match for exchange qty {exchange_qty:.6f}"
                        )

                        # Mark all other trades as phantom
                        for trade in trades:
                            if trade['id'] != best_trade['id']:
                                logger.warning(
                                    f"Marking duplicate trade ID {trade['id']} as PHANTOM_CLOSED "
                                    f"(qty={trade['quantity']:.6f})"
                                )
                                self.db.update_trade_status(trade['id'], 'PHANTOM_CLOSED')
                                skipped_count += 1

                        trades = [best_trade]
                    else:
                        # No exchange data - keep the most recent trade
                        best_trade = trades[0]  # Already sorted by created_at DESC
                        for trade in trades[1:]:
                            logger.warning(
                                f"Marking duplicate trade ID {trade['id']} as PHANTOM_CLOSED "
                                f"(keeping ID {best_trade['id']})"
                            )
                            self.db.update_trade_status(trade['id'], 'PHANTOM_CLOSED')
                            skipped_count += 1
                        trades = [best_trade]

                # Now process the single remaining trade for this symbol
                trade = trades[0]

                # In LIVE mode, verify position exists on exchange
                if verify_on_exchange:
                    if exchange_qty < trade['quantity'] * 0.9:  # Allow 10% tolerance
                        logger.warning(
                            f"SKIPPING phantom position {symbol}: "
                            f"DB qty={trade['quantity']:.6f}, Exchange qty={exchange_qty:.6f}"
                        )
                        self.db.update_trade_status(trade['id'], 'PHANTOM_CLOSED')
                        skipped_count += 1
                        continue

                    # If exchange has MORE than DB, update quantity in memory to match
                    if exchange_qty > trade['quantity'] * 1.1:  # More than 10% extra
                        logger.warning(
                            f"Exchange has MORE {symbol} than DB tracks! "
                            f"DB: {trade['quantity']:.6f}, Exchange: {exchange_qty:.6f}"
                        )
                        logger.info(f"Using exchange quantity: {exchange_qty:.6f}")
                        trade['quantity'] = exchange_qty

                self.positions[symbol] = {
                    'symbol': symbol,
                    'entry_price': trade['price'],
                    'quantity': trade['quantity'],
                    'trade_id': trade['id'],
                    'side': trade['side'],
                    'entry_time': trade.get('created_at', datetime.now(timezone.utc)),
                    'high_price': trade['price']
                }
                self.high_water_marks[symbol] = trade['price']
                synced_count += 1

            # In LIVE mode, import any untracked positions from exchange
            if verify_on_exchange:
                imported_count = 0
                for currency, qty in exchange_positions.items():
                    if currency in db_tracked_currencies:
                        continue  # Already tracked

                    symbol = f"{currency}/USDT"
                    try:
                        # Get current price
                        ticker = self.exchange.fetch_ticker(symbol)
                        price = ticker.get('last', 0)
                        value_usd = qty * price if price else 0

                        if value_usd < 1:  # Skip dust positions
                            continue

                        logger.warning(f"UNTRACKED POSITION FOUND: {symbol} = {qty:.6f} (${value_usd:.2f})")
                        logger.info(f"Auto-importing {symbol} to database...")

                        # Import to database
                        notional = qty * price
                        trade_id = self.db.log_trade(
                            symbol=symbol,
                            side='buy',
                            price=price,
                            quantity=qty,
                            notional=notional,
                            signal_reason='AUTO_IMPORTED_ON_STARTUP',
                            order_id=f'IMPORT_{currency}_{datetime.now().strftime("%Y%m%d%H%M%S")}',
                            status='OPEN'
                        )

                        # Add to position manager
                        self.positions[symbol] = {
                            'symbol': symbol,
                            'entry_price': price,
                            'quantity': qty,
                            'trade_id': trade_id,
                            'side': 'buy',
                            'entry_time': datetime.now(timezone.utc),
                            'high_price': price
                        }
                        self.high_water_marks[symbol] = price
                        imported_count += 1
                        logger.info(f"Imported {symbol}: {qty:.6f} @ ${price:.4f} -> Trade ID: {trade_id}")

                    except Exception as e:
                        logger.warning(f"Could not import {symbol}: {e}")

                if imported_count > 0:
                    logger.info(f"Auto-imported {imported_count} untracked positions from exchange")

            logger.info(f"Synced {synced_count} open positions from database")
            if skipped_count > 0:
                logger.warning(f"Cleaned up {skipped_count} phantom/duplicate positions")

        except Exception as e:
            logger.warning(f"Could not sync positions from DB: {e}")

    def get_position_summary(self) -> str:
        """Get summary of open positions"""
        if not self.positions:
            return "No open positions"

        lines = [f"Open Positions ({len(self.positions)}):"]
        for symbol, pos in self.positions.items():
            lines.append(f"  {symbol}: {pos['quantity']:.6f} @ {pos['entry_price']:.4f}")
        return "\n".join(lines)
