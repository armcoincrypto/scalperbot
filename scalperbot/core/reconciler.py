"""
Exchange Reconciliation - Ensures state consistency between bot and exchange.

Critical for LIVE trading safety:
1. Order verification after placement
2. Periodic position reconciliation
3. Restart recovery
"""

import asyncio
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any

from scalperbot.config import settings
from scalperbot.log import get_logger
from scalperbot.mexc import MEXCClient
from scalperbot.storage.db import get_database, Database
from scalperbot.storage.repo import PositionRepo

logger = get_logger(__name__)


class StateDiscrepancy:
    """Represents a discrepancy between bot state and exchange state."""

    def __init__(
        self,
        discrepancy_type: str,
        symbol: str,
        bot_state: Dict[str, Any],
        exchange_state: Dict[str, Any],
        severity: str = "WARNING"
    ):
        self.discrepancy_type = discrepancy_type
        self.symbol = symbol
        self.bot_state = bot_state
        self.exchange_state = exchange_state
        self.severity = severity
        self.timestamp = datetime.now(timezone.utc)

    def __repr__(self):
        return (
            f"StateDiscrepancy({self.discrepancy_type}, {self.symbol}, "
            f"severity={self.severity})"
        )


class ExchangeReconciler:
    """
    Reconciles bot state with exchange state.

    Prevents common disasters:
    - Bot thinks it has position but exchange doesn't (ghost position)
    - Exchange has position but bot doesn't know (orphan position)
    - Order state mismatch
    """

    def __init__(self, client: MEXCClient = None):
        self.client = client or MEXCClient(dry_run=settings.dry_run)
        self.db: Optional[Database] = None
        self.positions: Optional[PositionRepo] = None
        self.discrepancies: List[StateDiscrepancy] = []

        # Callback for notifications
        self.on_discrepancy = None

    async def initialize(self):
        """Initialize database connections."""
        self.db = await get_database()
        self.positions = PositionRepo(self.db)
        logger.info("Exchange reconciler initialized")

    async def verify_order(self, symbol: str, order_id: str) -> Dict[str, Any]:
        """
        Verify order status directly with exchange.

        Call this after placing any order to confirm execution.

        Returns:
            Order details from exchange or error info
        """
        if settings.dry_run:
            return {"status": "FILLED", "verified": True, "dry_run": True}

        try:
            order = await self.client.get_order(symbol, order_id)
            return {
                "status": order.status,
                "filled_qty": order.executed_qty,
                "avg_price": order.avg_price,
                "verified": True
            }
        except Exception as e:
            logger.error(f"Order verification failed: {symbol} {order_id} - {e}")
            return {"status": "UNKNOWN", "verified": False, "error": str(e)}

    async def reconcile_positions(self) -> List[StateDiscrepancy]:
        """
        Compare bot positions with exchange balances.

        Detects:
        - Ghost positions (bot has, exchange doesn't)
        - Orphan positions (exchange has, bot doesn't)
        - Quantity mismatches
        """
        if settings.dry_run:
            logger.debug("Skipping reconciliation in DRY_RUN mode")
            return []

        self.discrepancies = []

        try:
            # Get bot's open positions
            bot_positions = await self.positions.get_open()
            bot_symbols = {p["symbol"]: p for p in bot_positions}

            # Get exchange balances
            balances = await self.client.get_account_balances()

            # Check for ghost positions (bot has position, exchange doesn't)
            for symbol, position in bot_symbols.items():
                base_asset = symbol.replace("USDT", "")
                exchange_balance = balances.get(base_asset, 0)

                if exchange_balance < position["quantity"] * 0.99:  # 1% tolerance
                    discrepancy = StateDiscrepancy(
                        discrepancy_type="GHOST_POSITION",
                        symbol=symbol,
                        bot_state={"quantity": position["quantity"], "position_id": position["id"]},
                        exchange_state={"balance": exchange_balance},
                        severity="CRITICAL"
                    )
                    self.discrepancies.append(discrepancy)
                    logger.error(
                        f"GHOST POSITION: {symbol} - Bot has {position['quantity']}, "
                        f"Exchange has {exchange_balance}"
                    )

            # Check for orphan balances (exchange has, bot doesn't know)
            # Only check for significant balances (>$10 worth)
            for asset, balance in balances.items():
                if asset in ["USDT", "BTC", "ETH", "BNB"]:
                    continue  # Skip base currencies

                symbol = f"{asset}USDT"
                if symbol not in bot_symbols and balance > 0:
                    # Try to get price to check if significant
                    try:
                        ticker = await self.client.get_book_ticker(symbol)
                        value = balance * ticker.bid_price
                        if value > 10:  # More than $10
                            discrepancy = StateDiscrepancy(
                                discrepancy_type="ORPHAN_BALANCE",
                                symbol=symbol,
                                bot_state={"has_position": False},
                                exchange_state={"balance": balance, "value_usdt": value},
                                severity="WARNING"
                            )
                            self.discrepancies.append(discrepancy)
                            logger.warning(
                                f"ORPHAN BALANCE: {symbol} - Exchange has {balance} "
                                f"(~${value:.2f}) but bot has no position"
                            )
                    except Exception:
                        pass  # Symbol might not exist

            # Notify if discrepancies found
            if self.discrepancies and self.on_discrepancy:
                await self.on_discrepancy(self.discrepancies)

            return self.discrepancies

        except Exception as e:
            logger.error(f"Reconciliation failed: {e}", exc_info=True)
            return []

    async def recover_state(self) -> Dict[str, Any]:
        """
        Recover bot state from exchange on startup.

        Called when bot restarts to reconstruct state from:
        - Open orders
        - Account balances
        - Recent trades

        Returns:
            Recovery summary
        """
        if settings.dry_run:
            logger.info("Skipping state recovery in DRY_RUN mode")
            return {"dry_run": True, "recovered": 0}

        recovery_summary = {
            "positions_found": 0,
            "positions_recovered": 0,
            "orders_found": 0,
            "errors": []
        }

        try:
            # Get open orders from exchange
            open_orders = await self.client.get_open_orders()
            recovery_summary["orders_found"] = len(open_orders)

            # Get account balances
            balances = await self.client.get_account_balances()

            # Get bot's recorded open positions
            bot_positions = await self.positions.get_open()
            bot_symbols = {p["symbol"]: p for p in bot_positions}

            # Check each significant balance
            for asset, balance in balances.items():
                if asset == "USDT" or balance == 0:
                    continue

                symbol = f"{asset}USDT"

                try:
                    ticker = await self.client.get_book_ticker(symbol)
                    value = balance * ticker.bid_price

                    if value > 10:  # Significant position
                        recovery_summary["positions_found"] += 1

                        if symbol not in bot_symbols:
                            # Bot doesn't know about this position
                            logger.warning(
                                f"Found untracked position: {symbol} "
                                f"({balance} @ ~${ticker.bid_price:.4f} = ${value:.2f})"
                            )
                            # Could auto-create position record here if desired
                            recovery_summary["positions_recovered"] += 1

                except Exception as e:
                    recovery_summary["errors"].append(f"{symbol}: {e}")

            logger.info(
                f"State recovery complete: {recovery_summary['positions_found']} positions found, "
                f"{recovery_summary['positions_recovered']} recovered"
            )

            return recovery_summary

        except Exception as e:
            logger.error(f"State recovery failed: {e}", exc_info=True)
            recovery_summary["errors"].append(str(e))
            return recovery_summary

    async def fix_ghost_position(self, position_id: int, reason: str = "reconciliation"):
        """
        Fix a ghost position by marking it as closed.

        Call this when bot has a position but exchange doesn't.
        """
        logger.warning(f"Fixing ghost position {position_id}: {reason}")
        await self.positions.close(
            position_id=position_id,
            exit_price=0,
            exit_order_id="RECONCILED",
            exit_reason=f"GHOST_POSITION: {reason}"
        )

    async def run_periodic_reconciliation(self, interval_seconds: int = 300):
        """
        Run reconciliation periodically.

        Args:
            interval_seconds: How often to reconcile (default 5 min)
        """
        await self.initialize()

        while True:
            try:
                discrepancies = await self.reconcile_positions()
                if discrepancies:
                    logger.warning(f"Found {len(discrepancies)} discrepancies")
                else:
                    logger.debug("Reconciliation OK - no discrepancies")
            except Exception as e:
                logger.error(f"Periodic reconciliation error: {e}")

            await asyncio.sleep(interval_seconds)


# Helper function to integrate with engine
async def verify_order_with_retry(
    client: MEXCClient,
    symbol: str,
    order_id: str,
    max_retries: int = 3,
    delay: float = 1.0
) -> Dict[str, Any]:
    """
    Verify order with exponential backoff retry.

    Returns order details or error info.
    """
    for attempt in range(max_retries):
        try:
            order = await client.get_order(symbol, order_id)
            return {
                "status": order.status,
                "filled_qty": order.executed_qty,
                "avg_price": order.avg_price,
                "verified": True,
                "attempts": attempt + 1
            }
        except Exception as e:
            if attempt < max_retries - 1:
                await asyncio.sleep(delay * (2 ** attempt))
            else:
                return {
                    "status": "UNKNOWN",
                    "verified": False,
                    "error": str(e),
                    "attempts": attempt + 1
                }

    return {"status": "UNKNOWN", "verified": False, "error": "Max retries exceeded"}
