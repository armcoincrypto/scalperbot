"""
Database Reader for ScalperBot Analysis
Reads trades.db and extracts trade metrics
"""
import sqlite3
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional
from dataclasses import dataclass, field
import statistics


@dataclass
class Trade:
    """A single trade record"""
    id: int
    symbol: str
    side: str
    entry_price: float
    exit_price: Optional[float]
    quantity: float
    notional: float
    pnl: Optional[float]
    fees: float
    status: str
    entry_timestamp: datetime
    exit_timestamp: Optional[datetime]
    signal_reason: str


@dataclass
class SymbolTradeMetrics:
    """Trade metrics for a single symbol"""
    symbol: str
    total_trades: int = 0
    buys: int = 0
    sells: int = 0
    wins: int = 0
    losses: int = 0
    win_rate: float = 0.0
    profit_factor: float = 0.0
    avg_pnl: float = 0.0
    median_pnl: float = 0.0
    total_pnl: float = 0.0
    avg_win: float = 0.0
    avg_loss: float = 0.0
    gross_profit: float = 0.0
    gross_loss: float = 0.0
    avg_time_in_trade_seconds: float = 0.0
    avg_slippage_pct: float = 0.0
    max_drawdown_pct: float = 0.0
    trades: List[Trade] = field(default_factory=list)


class DBReader:
    """Read and analyze trades from database"""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self.conn: Optional[sqlite3.Connection] = None

    def connect(self) -> bool:
        """Connect to database"""
        try:
            self.conn = sqlite3.connect(self.db_path)
            self.conn.row_factory = sqlite3.Row
            return True
        except Exception as e:
            print(f"Failed to connect to database: {e}")
            return False

    def close(self):
        """Close database connection"""
        if self.conn:
            self.conn.close()
            self.conn = None

    def read_trades(self, days: int = 30) -> Dict[str, Any]:
        """Read trades from database and compute metrics"""

        if not self.conn:
            if not self.connect():
                return {'error': 'Failed to connect to database'}

        try:
            cursor = self.conn.cursor()

            # Check if trades table exists
            cursor.execute("""
                SELECT name FROM sqlite_master
                WHERE type='table' AND name='trades'
            """)
            if not cursor.fetchone():
                return {'error': 'trades table not found', 'symbols': {}}

            # Get column names to handle schema differences
            cursor.execute("PRAGMA table_info(trades)")
            columns = {row['name'] for row in cursor.fetchall()}

            # Build query based on available columns
            cutoff_date = (datetime.now() - timedelta(days=days)).isoformat()

            # Flexible query based on schema
            base_cols = ['id', 'symbol', 'side', 'price', 'quantity', 'notional', 'status']
            optional_cols = {
                'entry_price': 'price',  # fallback
                'exit_price': None,
                'pnl': None,
                'fees': '0',
                'created_at': None,
                'entry_timestamp': 'created_at',
                'exit_timestamp': None,
                'signal_reason': "''",
            }

            # Query trades
            query = """
                SELECT * FROM trades
                WHERE created_at >= ? OR entry_timestamp >= ?
                ORDER BY id ASC
            """

            try:
                cursor.execute(query, (cutoff_date, cutoff_date))
            except sqlite3.OperationalError:
                # Fallback query without timestamp filter
                cursor.execute("SELECT * FROM trades ORDER BY id ASC")

            rows = cursor.fetchall()

            # Parse trades
            trades_by_symbol: Dict[str, List[Trade]] = {}

            for row in rows:
                row_dict = dict(row)

                symbol = row_dict.get('symbol', 'UNKNOWN')

                # Parse with fallbacks
                trade = Trade(
                    id=row_dict.get('id', 0),
                    symbol=symbol,
                    side=row_dict.get('side', 'unknown'),
                    entry_price=float(row_dict.get('entry_price', row_dict.get('price', 0))),
                    exit_price=float(row_dict['exit_price']) if row_dict.get('exit_price') else None,
                    quantity=float(row_dict.get('quantity', 0)),
                    notional=float(row_dict.get('notional', 0)),
                    pnl=float(row_dict['pnl']) if row_dict.get('pnl') is not None else None,
                    fees=float(row_dict.get('fees', 0)),
                    status=row_dict.get('status', 'UNKNOWN'),
                    entry_timestamp=self._parse_timestamp(
                        row_dict.get('entry_timestamp', row_dict.get('created_at'))
                    ),
                    exit_timestamp=self._parse_timestamp(row_dict.get('exit_timestamp')),
                    signal_reason=row_dict.get('signal_reason', ''),
                )

                if symbol not in trades_by_symbol:
                    trades_by_symbol[symbol] = []
                trades_by_symbol[symbol].append(trade)

            # Compute metrics per symbol
            symbols_metrics = {}
            for symbol, trades in trades_by_symbol.items():
                metrics = self._compute_symbol_metrics(symbol, trades)
                symbols_metrics[symbol] = metrics

            # Global summary
            all_trades = [t for trades in trades_by_symbol.values() for t in trades]

            return {
                'total_trades': len(all_trades),
                'symbols': {
                    sym: self._metrics_to_dict(m) for sym, m in symbols_metrics.items()
                },
                'global_summary': self._compute_global_summary(all_trades),
                'time_range': {
                    'days': days,
                    'cutoff': cutoff_date,
                },
            }

        except Exception as e:
            return {'error': str(e), 'symbols': {}}

    def _parse_timestamp(self, ts: Any) -> Optional[datetime]:
        """Parse timestamp from various formats"""
        if ts is None:
            return None
        if isinstance(ts, datetime):
            return ts
        try:
            # Try ISO format
            return datetime.fromisoformat(str(ts).replace('Z', '+00:00'))
        except:
            try:
                # Try common format
                return datetime.strptime(str(ts), '%Y-%m-%d %H:%M:%S')
            except:
                return None

    def _compute_symbol_metrics(self, symbol: str, trades: List[Trade]) -> SymbolTradeMetrics:
        """Compute metrics for a symbol's trades"""

        metrics = SymbolTradeMetrics(symbol=symbol, trades=trades)
        metrics.total_trades = len(trades)

        # Count buys/sells
        metrics.buys = sum(1 for t in trades if t.side.lower() == 'buy')
        metrics.sells = sum(1 for t in trades if t.side.lower() == 'sell')

        # Filter trades with PnL for performance metrics
        trades_with_pnl = [t for t in trades if t.pnl is not None]

        if not trades_with_pnl:
            return metrics

        # Win/loss analysis
        pnls = [t.pnl for t in trades_with_pnl]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p < 0]

        metrics.wins = len(wins)
        metrics.losses = len(losses)
        metrics.win_rate = (metrics.wins / len(pnls) * 100) if pnls else 0

        # PnL stats
        metrics.total_pnl = sum(pnls)
        metrics.avg_pnl = statistics.mean(pnls) if pnls else 0
        metrics.median_pnl = statistics.median(pnls) if pnls else 0

        # Gross profit/loss
        metrics.gross_profit = sum(wins) if wins else 0
        metrics.gross_loss = abs(sum(losses)) if losses else 0
        metrics.avg_win = statistics.mean(wins) if wins else 0
        metrics.avg_loss = statistics.mean([abs(l) for l in losses]) if losses else 0

        # Profit factor
        if metrics.gross_loss > 0:
            metrics.profit_factor = metrics.gross_profit / metrics.gross_loss
        else:
            metrics.profit_factor = float('inf') if metrics.gross_profit > 0 else 0

        # Time in trade
        durations = []
        for t in trades_with_pnl:
            if t.entry_timestamp and t.exit_timestamp:
                duration = (t.exit_timestamp - t.entry_timestamp).total_seconds()
                if duration > 0:
                    durations.append(duration)

        if durations:
            metrics.avg_time_in_trade_seconds = statistics.mean(durations)

        # Slippage (simplified - would need market data for accurate calculation)
        # For now, estimate from trades with both entry and exit
        slippages = []
        for t in trades_with_pnl:
            if t.entry_price and t.exit_price and t.entry_price > 0:
                # Rough slippage estimate based on expected vs actual
                expected_pnl = (t.exit_price - t.entry_price) * t.quantity
                if t.side.lower() == 'sell':
                    expected_pnl = -expected_pnl
                if t.pnl is not None and abs(expected_pnl) > 0:
                    slippage = abs(t.pnl - expected_pnl) / t.notional * 100 if t.notional > 0 else 0
                    slippages.append(slippage)

        if slippages:
            metrics.avg_slippage_pct = statistics.mean(slippages)

        return metrics

    def _compute_global_summary(self, trades: List[Trade]) -> Dict[str, Any]:
        """Compute global summary across all trades"""

        if not trades:
            return {
                'total_trades': 0,
                'total_pnl': 0,
                'win_rate': 0,
            }

        trades_with_pnl = [t for t in trades if t.pnl is not None]
        pnls = [t.pnl for t in trades_with_pnl]

        wins = len([p for p in pnls if p > 0])
        total_with_pnl = len(pnls)

        return {
            'total_trades': len(trades),
            'completed_trades': total_with_pnl,
            'pending_trades': len(trades) - total_with_pnl,
            'wins': wins,
            'losses': total_with_pnl - wins,
            'win_rate': (wins / total_with_pnl * 100) if total_with_pnl > 0 else 0,
            'total_pnl': sum(pnls) if pnls else 0,
            'avg_pnl': statistics.mean(pnls) if pnls else 0,
            'by_status': self._count_by_status(trades),
        }

    def _count_by_status(self, trades: List[Trade]) -> Dict[str, int]:
        """Count trades by status"""
        counts: Dict[str, int] = {}
        for t in trades:
            status = t.status or 'UNKNOWN'
            counts[status] = counts.get(status, 0) + 1
        return counts

    def _metrics_to_dict(self, m: SymbolTradeMetrics) -> Dict[str, Any]:
        """Convert metrics to dictionary (excluding trades list)"""
        return {
            'symbol': m.symbol,
            'total_trades': m.total_trades,
            'buys': m.buys,
            'sells': m.sells,
            'wins': m.wins,
            'losses': m.losses,
            'win_rate': round(m.win_rate, 2),
            'profit_factor': round(m.profit_factor, 2) if m.profit_factor != float('inf') else 'inf',
            'avg_pnl': round(m.avg_pnl, 4),
            'median_pnl': round(m.median_pnl, 4),
            'total_pnl': round(m.total_pnl, 4),
            'avg_win': round(m.avg_win, 4),
            'avg_loss': round(m.avg_loss, 4),
            'gross_profit': round(m.gross_profit, 4),
            'gross_loss': round(m.gross_loss, 4),
            'avg_time_in_trade_seconds': round(m.avg_time_in_trade_seconds, 1),
            'avg_slippage_pct': round(m.avg_slippage_pct, 3),
        }
