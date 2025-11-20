"""
Order Execution Router
Handles order placement with proper quantization and validation
"""
import logging
from typing import Optional, Dict, Any
from decimal import Decimal, ROUND_DOWN
from scalperbot.exchanges.adapter import MEXCAdapter
from scalperbot.datafeed.orderbook import OrderBook

logger = logging.getLogger(__name__)


class OrderRouter:
    """
    Order execution router
    - Quantizes order sizes to exchange requirements
    - Validates MIN_NOTIONAL
    - Places maker/taker orders
    """

    def __init__(self, exchange: MEXCAdapter, orderbook: OrderBook):
        self.exchange = exchange
        self.orderbook = orderbook

        # Cache market info
        self.market_info_cache: Dict[str, Dict] = {}

    def get_market_info(self, symbol: str) -> Dict[str, Any]:
        """Get market info with caching"""
        if symbol not in self.market_info_cache:
            self.market_info_cache[symbol] = self.exchange.get_market_info(symbol)
        return self.market_info_cache[symbol]

    def quantize_amount(self, amount: float, precision: int) -> float:
        """Quantize amount to exchange precision"""
        if precision == 0:
            return float(int(amount))

        multiplier = 10 ** precision
        quantized = int(amount * multiplier) / multiplier
        return quantized

    def quantize_price(self, price: float, precision: int) -> float:
        """Quantize price to exchange precision"""
        if precision == 0:
            return float(int(price))

        multiplier = 10 ** precision
        quantized = int(price * multiplier) / multiplier
        return quantized

    def validate_order(
        self,
        symbol: str,
        amount: float,
        price: float
    ) -> tuple[bool, str]:
        """
        Validate order against exchange requirements
        Returns (is_valid, reason)
        """
        market_info = self.get_market_info(symbol)

        if not market_info:
            return False, "Could not fetch market info"

        # Check minimum amount
        min_amount = market_info.get('min_amount', 0)
        if amount < min_amount:
            return False, f"Amount {amount} < min {min_amount}"

        # Check minimum notional (cost)
        min_cost = market_info.get('min_cost', 0)
        notional = amount * price

        if notional < min_cost:
            return False, f"Notional ${notional:.2f} < min ${min_cost:.2f}"

        return True, "OK"

    def place_market_order(
        self,
        symbol: str,
        side: str,
        quantity: float
    ) -> Optional[Dict[str, Any]]:
        """
        Place a market order
        side: 'buy' or 'sell'
        """
        try:
            # Get market info
            market_info = self.get_market_info(symbol)
            amount_precision = market_info.get('amount_precision', 8)

            # Quantize amount
            quantized_amount = self.quantize_amount(quantity, amount_precision)

            # Get current price for validation
            mid_price = self.orderbook.get_mid_price(symbol)
            if not mid_price:
                logger.error(f"❌ No price data for {symbol}")
                return None

            # Validate order
            is_valid, reason = self.validate_order(symbol, quantized_amount, mid_price)
            if not is_valid:
                logger.error(f"❌ Order validation failed: {reason}")
                return None

            # Place market order
            logger.info(f"🔵 Placing market {side} order: {quantized_amount} {symbol}")
            order = self.exchange.create_market_order(symbol, side, quantized_amount)

            logger.info(f"✅ Market order placed: {order.get('id')}")
            return order

        except Exception as e:
            logger.error(f"❌ Error placing market order: {e}", exc_info=True)
            return None

    def place_limit_order(
        self,
        symbol: str,
        side: str,
        quantity: float,
        price: float
    ) -> Optional[Dict[str, Any]]:
        """
        Place a limit order (maker order)
        side: 'buy' or 'sell'
        """
        try:
            # Get market info
            market_info = self.get_market_info(symbol)
            amount_precision = market_info.get('amount_precision', 8)
            price_precision = market_info.get('price_precision', 8)

            # Quantize
            quantized_amount = self.quantize_amount(quantity, amount_precision)
            quantized_price = self.quantize_price(price, price_precision)

            # Validate
            is_valid, reason = self.validate_order(symbol, quantized_amount, quantized_price)
            if not is_valid:
                logger.error(f"❌ Order validation failed: {reason}")
                return None

            # Place limit order
            logger.info(f"🔵 Placing limit {side} order: {quantized_amount} {symbol} @ {quantized_price}")
            order = self.exchange.create_limit_order(symbol, side, quantized_amount, quantized_price)

            logger.info(f"✅ Limit order placed: {order.get('id')}")
            return order

        except Exception as e:
            logger.error(f"❌ Error placing limit order: {e}", exc_info=True)
            return None

    def place_maker_order(
        self,
        symbol: str,
        side: str,
        quantity: float,
        offset_bps: int = 5
    ) -> Optional[Dict[str, Any]]:
        """
        Place a maker order (limit order inside the spread)
        side: 'buy' or 'sell'
        offset_bps: offset from best bid/ask in basis points
        """
        try:
            # Get best bid/ask
            if side == 'buy':
                best_price = self.orderbook.get_best_bid(symbol)
            else:  # sell
                best_price = self.orderbook.get_best_ask(symbol)

            if not best_price:
                logger.error(f"❌ No orderbook data for {symbol}")
                return None

            # Apply offset (move slightly into the spread for maker rebates)
            offset = best_price * (offset_bps / 10000)
            if side == 'buy':
                limit_price = best_price + offset  # Buy slightly higher
            else:
                limit_price = best_price - offset  # Sell slightly lower

            return self.place_limit_order(symbol, side, quantity, limit_price)

        except Exception as e:
            logger.error(f"❌ Error placing maker order: {e}", exc_info=True)
            return None

    def cancel_order(self, order_id: str, symbol: str) -> bool:
        """Cancel an order"""
        return self.exchange.cancel_order(order_id, symbol)

    def get_order_status(self, order_id: str, symbol: str) -> Optional[Dict]:
        """Get order status"""
        return self.exchange.fetch_order(order_id, symbol)
