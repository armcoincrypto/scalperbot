"""
Trend Following Strategy with HTF Confirmation
Professional-grade entry/exit logic with:
- Higher Timeframe (1H) trend confirmation
- EMA crossover entries (EMA8 x EMA20)
- ATR-based dynamic TP/SL/Trailing
- RSI momentum filters
- Risk-based position sizing
"""
import pandas as pd
import numpy as np
from typing import Optional, Dict, Any, Tuple, List
from datetime import datetime, timedelta
import logging

logger = logging.getLogger(__name__)


class SmartBreakoutStrategy:
    """
    Trend Following Strategy with HTF confirmation and ATR exits

    Entry Conditions:
    1. HTF (1H) trend UP (price > EMA20)
    2. EMA8 crosses above EMA20 (bullish crossover)
    3. Price above EMA20
    4. RSI 45-70 (momentum but not overbought)

    Exit Conditions:
    - Stop Loss: 1.0 x ATR below entry
    - Take Profit: 2.0 x ATR above entry
    - Trailing Stop: Activates at 1.0 ATR profit, trails by 0.5 ATR
    """

    def __init__(self, candle_store, exchange=None):
        self.candle_store = candle_store
        self.exchange = exchange

        # Entry parameters
        self.htf_timeframe = '1h'
        self.ltf_timeframe = '15m'  # Can be 5m or 15m
        self.rsi_period = 14
        self.ema_fast = 8   # Fast EMA for crossover
        self.ema_slow = 20  # Slow EMA for crossover

        # Exit parameters (ATR-based)
        self.sl_atr_mult = 1.0
        self.tp_atr_mult = 2.0
        self.trailing_start_atr = 1.0
        self.trailing_step_atr = 0.5

        # Risk management - TREND strategy RSI range
        self.rsi_entry_min = 45
        self.rsi_entry_max = 70

        # Store for HTF data
        self.htf_data: Dict[str, pd.DataFrame] = {}

        # Active positions for exit management
        self.positions: Dict[str, Dict] = {}

    def calculate_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """Calculate all required indicators"""
        df = df.copy()

        # EMAs
        df['ema5'] = df['close'].ewm(span=5, adjust=False).mean()
        df['ema8'] = df['close'].ewm(span=8, adjust=False).mean()
        df['ema20'] = df['close'].ewm(span=20, adjust=False).mean()

        # RSI
        delta = df['close'].diff()
        gain = delta.where(delta > 0, 0).rolling(window=self.rsi_period).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=self.rsi_period).mean()
        rs = gain / loss.replace(0, np.inf)
        df['rsi'] = 100 - (100 / (1 + rs))

        # ATR
        high_low = df['high'] - df['low']
        high_close = abs(df['high'] - df['close'].shift())
        low_close = abs(df['low'] - df['close'].shift())
        true_range = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
        df['atr'] = true_range.rolling(window=14).mean()

        # Volume
        df['volume_ma'] = df['volume'].rolling(window=20).mean()
        df['volume_ratio'] = df['volume'] / df['volume_ma']

        # Bollinger Bands for additional context
        df['bb_middle'] = df['close'].rolling(window=20).mean()
        df['bb_std'] = df['close'].rolling(window=20).std()
        df['bb_upper'] = df['bb_middle'] + (2 * df['bb_std'])
        df['bb_lower'] = df['bb_middle'] - (2 * df['bb_std'])

        # Highest high for breakout
        df['highest_high'] = df['high'].rolling(window=self.breakout_lookback).max()

        return df

    def update_htf_data(self, symbol: str, df_1h: pd.DataFrame):
        """Update stored HTF data for a symbol"""
        df = df_1h.copy()

        # Calculate HTF indicators
        df['htf_ema20'] = df['close'].ewm(span=20, adjust=False).mean()
        df['htf_ema50'] = df['close'].ewm(span=50, adjust=False).mean()

        # HTF RSI
        delta = df['close'].diff()
        gain = delta.where(delta > 0, 0).rolling(window=14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
        rs = gain / loss.replace(0, np.inf)
        df['htf_rsi'] = 100 - (100 / (1 + rs))

        # Trend direction
        df['htf_trend_up'] = df['close'] > df['htf_ema20']

        self.htf_data[symbol] = df
        logger.debug(f"Updated HTF data for {symbol}: {len(df)} candles")

    def get_htf_context(self, symbol: str) -> Dict:
        """Get current HTF trend context"""
        if symbol not in self.htf_data or self.htf_data[symbol].empty:
            return {
                'trend_up': False,
                'rsi': 50,
                'above_ema': False,
                'reason': 'No HTF data'
            }

        df = self.htf_data[symbol]
        row = df.iloc[-1]

        return {
            'trend_up': bool(row.get('htf_trend_up', False)),
            'rsi': float(row.get('htf_rsi', 50)),
            'above_ema': bool(row['close'] > row.get('htf_ema20', row['close'])),
            'ema20': float(row.get('htf_ema20', 0)),
            'price': float(row['close']),
            'reason': 'OK'
        }

    def check_entry(self, symbol: str, df: pd.DataFrame) -> Tuple[bool, str, Dict]:
        """
        Check all entry conditions using TREND strategy (EMA crossover)

        Returns: (should_enter, reason, signal_details)
        """
        if len(df) < 50:
            return False, "Not enough data", {}

        df = self.calculate_indicators(df)
        row = df.iloc[-1]
        prev_row = df.iloc[-2]

        # Get HTF context
        htf = self.get_htf_context(symbol)

        # === HTF FILTER ===
        if not htf['trend_up']:
            return False, f"HTF trend DOWN (price below EMA20)", {}

        # === EMA CROSSOVER CHECK ===
        # EMA8 must cross above EMA20 (current bar)
        ema8_now = row['ema8']
        ema20_now = row['ema20']
        ema8_prev = prev_row['ema8']
        ema20_prev = prev_row['ema20']

        if pd.isna(ema8_now) or pd.isna(ema20_now) or pd.isna(ema8_prev) or pd.isna(ema20_prev):
            return False, "EMA not available", {}

        # Check for bullish crossover: EMA8 was below/equal EMA20, now above
        ema_crossover = (ema8_prev <= ema20_prev) and (ema8_now > ema20_now)

        if not ema_crossover:
            return False, "No EMA crossover (waiting for EMA8 > EMA20)", {}

        # === PRICE ABOVE EMA20 ===
        if row['close'] <= ema20_now:
            return False, f"Price below EMA20", {}

        # === RSI FILTER ===
        rsi = row['rsi']
        if pd.isna(rsi):
            return False, "RSI not available", {}

        if rsi < self.rsi_entry_min or rsi > self.rsi_entry_max:
            return False, f"RSI out of range: {rsi:.1f} (need {self.rsi_entry_min}-{self.rsi_entry_max})", {}

        # === ENTRY CONFIRMED ===
        entry_reason = "EMA8 crossed above EMA20"

        # Build signal
        atr = row['atr'] if not pd.isna(row['atr']) else row['close'] * 0.01
        entry_price = row['close']

        signal = {
            'symbol': symbol,
            'action': 'BUY',
            'price': entry_price,
            'timestamp': row.get('timestamp', datetime.utcnow()),
            'reason': entry_reason,
            'atr': atr,
            'stop_loss': entry_price - (atr * self.sl_atr_mult),
            'take_profit': entry_price + (atr * self.tp_atr_mult),
            'trailing_start': entry_price + (atr * self.trailing_start_atr),
            'filters': {
                'htf_trend': 'UP',
                'ema8': f"{ema8_now:.4f}",
                'ema20': f"{ema20_now:.4f}",
                'ltf_rsi': f"{rsi:.1f}",
                'trigger': entry_reason
            }
        }

        logger.info(f"\n{'='*60}")
        logger.info(f"SIGNAL GENERATED: {symbol} BUY @ {entry_price:.4f}")
        logger.info(f"  Reason: {entry_reason}")
        logger.info(f"  HTF: Trend UP")
        logger.info(f"  EMA8: {ema8_now:.4f} > EMA20: {ema20_now:.4f}")
        logger.info(f"  RSI: {rsi:.1f}")
        logger.info(f"  SL: {signal['stop_loss']:.4f} ({self.sl_atr_mult} ATR)")
        logger.info(f"  TP: {signal['take_profit']:.4f} ({self.tp_atr_mult} ATR)")
        logger.info(f"{'='*60}")

        return True, entry_reason, signal

    def register_position(self, symbol: str, entry_price: float, atr: float, trade_id: int):
        """Register a new position for exit management"""
        self.positions[symbol] = {
            'trade_id': trade_id,
            'entry_price': entry_price,
            'entry_time': datetime.utcnow(),
            'atr': atr,
            'stop_loss': entry_price - (atr * self.sl_atr_mult),
            'take_profit': entry_price + (atr * self.tp_atr_mult),
            'trailing_start': entry_price + (atr * self.trailing_start_atr),
            'trailing_stop': None,
            'highest_price': entry_price
        }
        logger.info(f"Registered position for {symbol}: Entry={entry_price:.4f}")

    def check_exit(self, symbol: str, current_price: float) -> Tuple[bool, str, float]:
        """
        Check if position should be closed

        Returns: (should_exit, reason, exit_price)
        """
        if symbol not in self.positions:
            return False, "", 0

        pos = self.positions[symbol]
        entry_price = pos['entry_price']
        atr = pos['atr']

        # Update highest price
        if current_price > pos['highest_price']:
            pos['highest_price'] = current_price

            # Update trailing stop if activated
            if current_price >= pos['trailing_start']:
                new_trailing = current_price - (atr * self.trailing_step_atr)
                if pos['trailing_stop'] is None or new_trailing > pos['trailing_stop']:
                    pos['trailing_stop'] = new_trailing
                    logger.debug(f"{symbol}: Trailing stop updated to {new_trailing:.4f}")

        # Check stop loss
        if current_price <= pos['stop_loss']:
            return True, 'STOP_LOSS', pos['stop_loss']

        # Check trailing stop
        if pos['trailing_stop'] and current_price <= pos['trailing_stop']:
            return True, 'TRAILING_STOP', pos['trailing_stop']

        # Check take profit
        if current_price >= pos['take_profit']:
            return True, 'TAKE_PROFIT', pos['take_profit']

        return False, "", 0

    def close_position(self, symbol: str):
        """Remove position from tracking"""
        if symbol in self.positions:
            del self.positions[symbol]
            logger.info(f"Position closed for {symbol}")

    def get_position_info(self, symbol: str) -> Optional[Dict]:
        """Get current position info"""
        return self.positions.get(symbol)

    def generate_signal(self, symbol: str) -> Optional[Dict]:
        """Generate trading signal for a symbol"""
        # Get LTF candles (15m or 5m)
        df = self.candle_store.get_candles(symbol, self.ltf_timeframe, limit=100)

        if df is None or df.empty:
            logger.debug(f"{symbol}: No LTF candle data")
            return None

        if len(df) < 50:
            logger.debug(f"{symbol}: Only {len(df)}/50 candles - need more data")
            return None

        # Check if we already have a position
        if symbol in self.positions:
            logger.debug(f"{symbol}: Already have position, skipping entry check")
            return None

        # Check entry
        should_enter, reason, signal = self.check_entry(symbol, df)

        if should_enter:
            return signal
        else:
            logger.debug(f"{symbol}: {reason}")
            return None

    def run_for_all_symbols(self, symbols: List[str]) -> List[Dict]:
        """Run strategy for all symbols"""
        signals = []

        for symbol in symbols:
            try:
                signal = self.generate_signal(symbol)
                if signal:
                    signals.append(signal)
            except Exception as e:
                logger.error(f"Error running strategy for {symbol}: {e}", exc_info=True)

        if signals:
            logger.info(f"Generated {len(signals)} signals")

        return signals

    def check_all_positions(self, get_price_func) -> List[Dict]:
        """
        Check all open positions for exit conditions

        Args:
            get_price_func: Function that takes symbol and returns current price

        Returns: List of exit signals
        """
        exits = []

        for symbol in list(self.positions.keys()):
            try:
                current_price = get_price_func(symbol)
                if current_price is None:
                    continue

                should_exit, reason, exit_price = self.check_exit(symbol, current_price)

                if should_exit:
                    pos = self.positions[symbol]
                    pnl = (exit_price - pos['entry_price']) / pos['entry_price'] * 100

                    exits.append({
                        'symbol': symbol,
                        'action': 'SELL',
                        'price': exit_price,
                        'reason': reason,
                        'trade_id': pos['trade_id'],
                        'entry_price': pos['entry_price'],
                        'pnl_pct': pnl
                    })

                    logger.info(f"\n{'='*60}")
                    logger.info(f"EXIT SIGNAL: {symbol} SELL @ {exit_price:.4f}")
                    logger.info(f"  Reason: {reason}")
                    logger.info(f"  Entry: {pos['entry_price']:.4f}")
                    logger.info(f"  PnL: {pnl:+.2f}%")
                    logger.info(f"{'='*60}")

            except Exception as e:
                logger.error(f"Error checking position for {symbol}: {e}", exc_info=True)

        return exits


def calculate_position_size(
    equity: float,
    entry_price: float,
    stop_loss: float,
    risk_pct: float = 0.5,
    max_position_pct: float = 10.0
) -> Dict:
    """
    Calculate position size based on risk

    Args:
        equity: Total account equity
        entry_price: Entry price
        stop_loss: Stop loss price
        risk_pct: Percent of equity to risk (default 0.5%)
        max_position_pct: Max position size as % of equity (default 10%)

    Returns:
        Dict with position sizing info
    """
    stop_loss_pct = abs((entry_price - stop_loss) / entry_price)

    if stop_loss_pct == 0:
        stop_loss_pct = 0.01  # Default 1% if no SL

    # Risk amount
    risk_amount = equity * (risk_pct / 100)

    # Position size based on risk
    position_usd = risk_amount / stop_loss_pct

    # Cap at max position
    max_position = equity * (max_position_pct / 100)
    position_usd = min(position_usd, max_position)

    # Quantity in base currency
    quantity = position_usd / entry_price

    return {
        'quantity': quantity,
        'notional_usd': position_usd,
        'risk_amount': risk_amount,
        'stop_loss_pct': stop_loss_pct * 100,
        'risk_pct': risk_pct,
        'can_trade': True,
        'reason': 'OK'
    }
