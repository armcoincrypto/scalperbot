#!/usr/bin/env python3
"""
Position Sync Utility
Syncs actual MEXC positions with the bot's database

Usage:
    python tools/sync_positions.py --check     # Just show positions, no changes
    python tools/sync_positions.py --sync      # Import untracked positions to DB
"""

import os
import sys
import argparse
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from config import settings
from exchanges.adapter import MEXCAdapter
from db import TradeDB


def get_exchange_positions(exchange):
    """Fetch all non-zero positions from exchange"""
    balance = exchange.fetch_balance()
    positions = {}

    for currency, info in balance.items():
        if isinstance(info, dict):
            free = info.get('free', 0) or 0
            total = info.get('total', 0) or 0
            if total > 0 and currency not in ['USDT', 'USD']:
                # Get current price
                symbol = f"{currency}/USDT"
                try:
                    ticker = exchange.fetch_ticker(symbol)
                    price = ticker.get('last', 0)
                    value_usd = total * price if price else 0
                    if value_usd >= 1:  # Only show positions worth >= $1
                        positions[symbol] = {
                            'currency': currency,
                            'quantity': total,
                            'free': free,
                            'price': price,
                            'value_usd': value_usd
                        }
                except Exception as e:
                    pass  # Skip if can't fetch price

    return positions


def get_db_positions(db):
    """Get open positions from database"""
    positions = {}
    for trade in db.get_open_positions():
        positions[trade['symbol']] = {
            'trade_id': trade['id'],
            'quantity': trade['quantity'],
            'entry_price': trade['price'],
            'status': trade['status']
        }
    return positions


def import_position(db, symbol: str, quantity: float, current_price: float):
    """Import an untracked position into the database"""
    notional = quantity * current_price
    trade_id = db.log_trade(
        symbol=symbol,
        side='buy',
        price=current_price,
        quantity=quantity,
        notional=notional,
        signal_reason='IMPORTED_FROM_EXCHANGE',
        order_id=f'IMPORT_{symbol.replace("/", "_")}',
        status='OPEN'
    )
    return trade_id


def main():
    parser = argparse.ArgumentParser(description='Sync MEXC positions with bot database')
    parser.add_argument('--check', action='store_true', help='Just show positions, no changes')
    parser.add_argument('--sync', action='store_true', help='Import untracked positions to DB')
    parser.add_argument('--symbol', type=str, help='Specific symbol to sync (e.g., ADA/USDT)')
    args = parser.parse_args()

    if not args.check and not args.sync:
        args.check = True  # Default to check mode

    # Check API credentials
    if not settings.mexc_api_key or not settings.mexc_api_secret:
        print("❌ ERROR: No API credentials found in .env file")
        print("   Create a .env file with:")
        print("   MEXC_API_KEY=your_key")
        print("   MEXC_API_SECRET=your_secret")
        sys.exit(1)

    print("=" * 60)
    print("MEXC Position Sync Utility")
    print("=" * 60)

    # Initialize exchange
    print("\n🔄 Connecting to MEXC...")
    exchange = MEXCAdapter(
        api_key=settings.mexc_api_key,
        api_secret=settings.mexc_api_secret,
        dry_run=False  # We need real data
    )

    # Initialize database
    db = TradeDB(settings.database_path)

    # Fetch positions
    print("📊 Fetching exchange positions...")
    exchange_positions = get_exchange_positions(exchange)

    print("📁 Fetching database positions...")
    db_positions = get_db_positions(db)

    # Get USDT balance
    balance = exchange.fetch_balance()
    usdt_info = balance.get('USDT', {})
    usdt_free = usdt_info.get('free', 0) if isinstance(usdt_info, dict) else 0

    print("\n" + "=" * 60)
    print("ACCOUNT SUMMARY")
    print("=" * 60)
    print(f"💵 USDT Balance: ${usdt_free:.2f}")

    # Show exchange positions
    print("\n" + "-" * 60)
    print("EXCHANGE POSITIONS (on MEXC)")
    print("-" * 60)

    total_value = 0
    for symbol, pos in sorted(exchange_positions.items(), key=lambda x: -x[1]['value_usd']):
        value = pos['value_usd']
        total_value += value
        tracked = "✅" if symbol in db_positions else "❌ NOT TRACKED"
        print(f"  {symbol}: {pos['quantity']:.6f} @ ${pos['price']:.4f} = ${value:.2f} {tracked}")

    print(f"\n  Total Position Value: ${total_value:.2f}")
    print(f"  Total Account Value: ${total_value + usdt_free:.2f}")

    # Show database positions
    print("\n" + "-" * 60)
    print("DATABASE POSITIONS (tracked by bot)")
    print("-" * 60)

    if not db_positions:
        print("  (no positions in database)")
    else:
        for symbol, pos in db_positions.items():
            on_exchange = "✅" if symbol in exchange_positions else "⚠️ NOT ON EXCHANGE"
            print(f"  {symbol}: {pos['quantity']:.6f} @ ${pos['entry_price']:.4f} [ID:{pos['trade_id']}] {on_exchange}")

    # Find untracked positions
    untracked = []
    for symbol, pos in exchange_positions.items():
        if symbol not in db_positions:
            untracked.append((symbol, pos))

    # Find phantom positions (in DB but not on exchange)
    phantoms = []
    for symbol, pos in db_positions.items():
        if symbol not in exchange_positions:
            phantoms.append((symbol, pos))

    if untracked:
        print("\n" + "-" * 60)
        print(f"⚠️  UNTRACKED POSITIONS ({len(untracked)} found)")
        print("-" * 60)
        for symbol, pos in untracked:
            print(f"  {symbol}: {pos['quantity']:.6f} = ${pos['value_usd']:.2f}")

    if phantoms:
        print("\n" + "-" * 60)
        print(f"👻 PHANTOM POSITIONS ({len(phantoms)} in DB but NOT on exchange)")
        print("-" * 60)
        for symbol, pos in phantoms:
            print(f"  {symbol}: {pos['quantity']:.6f} [ID:{pos['trade_id']}]")

    # Sync mode - import untracked positions
    if args.sync and untracked:
        print("\n" + "=" * 60)
        print("IMPORTING UNTRACKED POSITIONS")
        print("=" * 60)

        for symbol, pos in untracked:
            if args.symbol and symbol != args.symbol:
                continue

            trade_id = import_position(db, symbol, pos['quantity'], pos['price'])
            print(f"  ✅ Imported {symbol}: {pos['quantity']:.6f} @ ${pos['price']:.4f} -> Trade ID: {trade_id}")

        print("\n✅ Sync complete! Positions are now tracked by the bot.")
        print("   Start the bot with: python main.py")

    elif args.sync and not untracked:
        print("\n✅ All exchange positions are already tracked in database.")

    elif args.check and untracked:
        print("\n" + "-" * 60)
        print("💡 TO IMPORT UNTRACKED POSITIONS, RUN:")
        print(f"   python tools/sync_positions.py --sync")
        print("-" * 60)

    db.close()


if __name__ == "__main__":
    main()
