"""
Position Sizing Module
Calculates position sizes based on volatility and risk parameters
Uses database for position tracking instead of simple counter

Implements expert recommendation #6: Volatility-based position sizing
- Risk per trade: configurable % of account equity
- Position size = (equity × risk_pct) / stop_loss_pct
- Caps per-symbol notional to prevent oversized bets
"""
import logging
from typing import Optional, Dict, Any, List
from config import settings

logger = logging.getLogger(__name__)


# Correlation groups for exposure management
CORRELATION_GROUPS = {
    'btc_correlated': ['BTC/USDT', 'ETH/USDT', 'SOL/USDT', 'AVAX/USDT'],
    'altcoins_high_beta': ['DOGE/USDT', 'XRP/USDT', 'ADA/USDT', 'TRX/USDT'],
    'defi': ['LINK/USDT', 'UNI/USDT', 'AAVE/USDT'],
    'layer2': ['POL/USDT', 'ARB/USDT', 'OP/USDT'],
    'other': ['LTC/USDT', 'BCH/USDT', 'XLM/USDT', 'BNB/USDT', 'NEAR/USDT']
}


class PositionSizer:
    """
    Calculates position sizes for trades with volatility-based sizing
    - Risk-based position sizing (% of equity)
    - ATR/SL-adjusted sizing
    - Database-backed position tracking
    - Max position limits
    - Correlated exposure management
    """

    def __init__(self, db=None, exchange=None, default_size_usd: float = None, max_positions: int = None):
        self.db = db  # TradeDB instance for position tracking
        self.exchange = exchange  # Exchange adapter for balance queries
        self.default_size_usd = default_size_usd or settings.position_size_usd
        self.max_positions = max_positions or settings.max_positions

        # Risk-based sizing parameters
        self.risk_per_trade_pct = getattr(settings, 'risk_per_trade_pct', 0.3)  # 0.3% of equity per trade
        self.use_volatility_sizing = getattr(settings, 'use_volatility_sizing', True)
        self.min_position_usd = 10.0  # Minimum $10 position
        self.max_position_usd = 500.0  # Maximum $500 per position

        # Correlated exposure limits
        self.max_correlated_positions = getattr(settings, 'max_correlated_positions', 2)
        self.correlated_size_reduction = 0.7  # Reduce size by 30% when 2+ correlated

        # Cache equity
        self._cached_equity = None
        self._equity_cache_time = 0

    def get_current_position_count(self) -> int:
        """Get current position count from database"""
        if self.db:
            return self.db.get_position_count()
        return 0

    def has_position(self, symbol: str) -> bool:
        """Check if we already have an open position for this symbol"""
        if self.db:
            position = self.db.get_position_by_symbol(symbol)
            return position is not None
        return False

    def get_open_symbols(self) -> List[str]:
        """Get list of symbols with open positions"""
        if self.db:
            positions = self.db.get_open_positions()
            return [p['symbol'] for p in positions]
        return []

    def get_correlation_group(self, symbol: str) -> str:
        """Get the correlation group for a symbol"""
        for group_name, symbols in CORRELATION_GROUPS.items():
            if symbol in symbols:
                return group_name
        return 'other'

    def count_correlated_positions(self, symbol: str) -> int:
        """Count how many open positions are in the same correlation group"""
        target_group = self.get_correlation_group(symbol)
        open_symbols = self.get_open_symbols()

        count = 0
        for open_symbol in open_symbols:
            if self.get_correlation_group(open_symbol) == target_group:
                count += 1

        return count

    def get_equity(self) -> float:
        """Get current account equity (USDT balance)"""
        import time

        # Use cached value if recent (within 60 seconds)
        if self._cached_equity and (time.time() - self._equity_cache_time) < 60:
            return self._cached_equity

        if self.exchange:
            try:
                balance = self.exchange.fetch_balance()
                equity = balance.get('USDT', {}).get('free', 0)
                self._cached_equity = equity
                self._equity_cache_time = time.time()
                return equity
            except Exception as e:
                logger.warning(f"Failed to fetch balance: {e}")

        # Fallback to default
        return self.default_size_usd * 10  # Assume $1000 if can't fetch

    def calculate_volatility_size(
        self,
        symbol: str,
        price: float,
        stop_loss_pct: float,
        equity: float
    ) -> float:
        """
        Calculate position size based on volatility (stop loss distance)

        Formula: position_size = (equity × risk_pct) / stop_loss_pct
        Example: $800 equity, 0.3% risk, 1% SL → $240 position

        Args:
            symbol: Trading pair
            price: Entry price
            stop_loss_pct: Stop loss percentage (e.g., 1.0 for 1%)
            equity: Account equity in USDT

        Returns:
            Position size in USDT
        """
        if stop_loss_pct <= 0:
            stop_loss_pct = 1.0  # Default 1% SL

        # Calculate risk amount in dollars
        risk_amount = equity * (self.risk_per_trade_pct / 100)

        # Position size = risk_amount / (stop_loss_pct / 100)
        position_usd = risk_amount / (stop_loss_pct / 100)

        logger.debug(f"Volatility sizing: equity=${equity:.2f}, risk={self.risk_per_trade_pct}%, "
                    f"SL={stop_loss_pct}% → position=${position_usd:.2f}")

        return position_usd

    def calculate_size(
        self,
        symbol: str,
        price: float,
        balance_usd: Optional[float] = None,
        stop_loss_pct: Optional[float] = None,
        atr_value: Optional[float] = None
    ) -> Dict[str, Any]:
        """
        Calculate position size in base currency with volatility-based sizing

        Args:
            symbol: Trading pair (e.g., 'BTC/USDT')
            price: Current price
            balance_usd: Available balance (optional, will fetch if not provided)
            stop_loss_pct: Stop loss percentage for volatility sizing
            atr_value: ATR value for dynamic SL calculation

        Returns:
            Dict with 'quantity', 'notional_usd', 'can_trade', sizing details
        """
        current_positions = self.get_current_position_count()

        # Check if we can open more positions
        if current_positions >= self.max_positions:
            logger.warning(f"⚠️ Max positions ({self.max_positions}) reached")
            return {
                'quantity': 0,
                'notional_usd': 0,
                'can_trade': False,
                'reason': 'Max positions reached',
                'sizing_method': 'blocked'
            }

        # Check if we already have a position for this symbol
        if self.has_position(symbol):
            logger.warning(f"⚠️ Already have an open position for {symbol}")
            return {
                'quantity': 0,
                'notional_usd': 0,
                'can_trade': False,
                'reason': f'Already have position in {symbol}',
                'sizing_method': 'blocked'
            }

        # Get equity for sizing
        equity = balance_usd if balance_usd else self.get_equity()

        # Determine sizing method
        if self.use_volatility_sizing and stop_loss_pct:
            # Volatility-based sizing
            notional_usd = self.calculate_volatility_size(symbol, price, stop_loss_pct, equity)
            sizing_method = 'volatility'
        elif self.use_volatility_sizing and atr_value:
            # Use ATR for dynamic SL
            dynamic_sl_pct = (atr_value / price) * 100
            dynamic_sl_pct = max(0.5, min(3.0, dynamic_sl_pct))  # Clamp 0.5% - 3%
            notional_usd = self.calculate_volatility_size(symbol, price, dynamic_sl_pct, equity)
            sizing_method = 'atr_dynamic'
        else:
            # Fallback to fixed sizing
            notional_usd = self.default_size_usd
            sizing_method = 'fixed'

        # Apply per-symbol minimum
        min_notional = settings.get_min_notional(symbol)
        notional_usd = max(notional_usd, min_notional)

        # Apply global min/max limits
        notional_usd = max(self.min_position_usd, min(self.max_position_usd, notional_usd))

        # Check correlated exposure and reduce size if needed
        correlated_count = self.count_correlated_positions(symbol)
        size_multiplier = 1.0

        if correlated_count >= self.max_correlated_positions:
            logger.warning(f"⚠️ Max correlated positions ({self.max_correlated_positions}) "
                          f"in {self.get_correlation_group(symbol)} group")
            return {
                'quantity': 0,
                'notional_usd': 0,
                'can_trade': False,
                'reason': f'Max correlated exposure in {self.get_correlation_group(symbol)}',
                'sizing_method': 'blocked_correlated'
            }
        elif correlated_count > 0:
            # Reduce size for additional correlated positions
            size_multiplier = self.correlated_size_reduction
            notional_usd *= size_multiplier
            logger.info(f"📊 Reducing size by {(1-size_multiplier)*100:.0f}% due to "
                       f"{correlated_count} correlated position(s)")

        # Final balance check
        if equity < notional_usd:
            logger.warning(f"⚠️ Insufficient balance: ${equity:.2f} < ${notional_usd:.2f}")
            return {
                'quantity': 0,
                'notional_usd': 0,
                'can_trade': False,
                'reason': 'Insufficient balance',
                'sizing_method': sizing_method
            }

        # Calculate quantity in base currency
        quantity = notional_usd / price

        logger.info(f"Position size for {symbol}: {quantity:.6f} (${notional_usd:.2f} @ {price:.4f}) "
                   f"[{sizing_method}]")

        return {
            'quantity': quantity,
            'notional_usd': notional_usd,
            'can_trade': True,
            'reason': 'OK',
            'sizing_method': sizing_method,
            'risk_pct': self.risk_per_trade_pct,
            'stop_loss_pct': stop_loss_pct or 1.0,
            'equity': equity,
            'correlated_count': correlated_count,
            'size_multiplier': size_multiplier
        }

    def can_open_position(self, symbol: str = None) -> bool:
        """Check if we can open a new position"""
        current_positions = self.get_current_position_count()

        if current_positions >= self.max_positions:
            return False

        if symbol and self.has_position(symbol):
            return False

        # Check correlated exposure
        if symbol:
            correlated_count = self.count_correlated_positions(symbol)
            if correlated_count >= self.max_correlated_positions:
                return False

        return True

    def get_status(self) -> Dict[str, Any]:
        """Get position sizer status"""
        current_positions = self.get_current_position_count()
        equity = self.get_equity()

        return {
            'current_positions': current_positions,
            'max_positions': self.max_positions,
            'can_open': current_positions < self.max_positions,
            'equity': equity,
            'risk_per_trade_pct': self.risk_per_trade_pct,
            'use_volatility_sizing': self.use_volatility_sizing,
            'open_symbols': self.get_open_symbols()
        }
