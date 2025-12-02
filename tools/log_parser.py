"""
Log Parser for ScalperBot
Extracts signals, orders, filter results, and metrics from bot.log
"""
import re
from datetime import datetime
from typing import Dict, List, Any, Optional
from dataclasses import dataclass, field
from collections import defaultdict


@dataclass
class FilterResult:
    """Result of a GREEN filter check"""
    timestamp: datetime
    symbol: str
    filter_name: str  # GREEN1, GREEN2, GREEN3, GREEN4
    passed: bool
    price: Optional[float] = None
    threshold: Optional[float] = None
    value: Optional[float] = None
    raw_message: str = ""


@dataclass
class SignalEvent:
    """A trading signal event"""
    timestamp: datetime
    symbol: str
    action: str  # BUY, SELL
    price: float
    reason: str = ""


@dataclass
class OrderEvent:
    """An order execution event"""
    timestamp: datetime
    symbol: str
    side: str
    price: float
    quantity: float
    notional: float
    order_id: Optional[str] = None
    dry_run: bool = False
    status: str = "EXECUTED"


@dataclass
class CycleData:
    """Data from one strategy cycle"""
    timestamp: datetime
    cycle_number: int
    symbols_checked: List[str] = field(default_factory=list)
    filter_results: List[FilterResult] = field(default_factory=list)
    signals: List[SignalEvent] = field(default_factory=list)
    orders: List[OrderEvent] = field(default_factory=list)


@dataclass
class SymbolMetrics:
    """Aggregated metrics for a symbol"""
    symbol: str
    total_cycles: int = 0
    signal_count: int = 0
    order_count: int = 0

    # Filter statistics
    green1_pass: int = 0
    green1_fail: int = 0
    green2_pass: int = 0
    green2_fail: int = 0
    green3_pass: int = 0
    green3_fail: int = 0
    green4_pass: int = 0
    green4_fail: int = 0

    # Near-miss tracking (3/4 filters passed)
    near_miss_count: int = 0
    near_miss_blocking_filter: Dict[str, int] = field(default_factory=lambda: defaultdict(int))

    # Breakout gap tracking
    breakout_gaps: List[float] = field(default_factory=list)  # % gap when GREEN4 failed

    # Expansion tracking (GREEN2)
    expansion_values: List[float] = field(default_factory=list)


class LogParser:
    """Parse bot.log and extract structured data"""

    # Regex patterns for parsing log lines
    PATTERNS = {
        'timestamp': r'^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3})',
        'cycle': r'Strategy Cycle #(\d+)',
        'symbol_check': r'📊 (\w+/USDT) Strategy Check',

        # GREEN filter patterns
        'green1': r'GREEN 1: Price=([\d.]+), 2bars_ago=([\d.]+), trending=(UP|DOWN)',
        'green2_dynamic': r'GREEN 2 \[DYNAMIC\]: BB_pctl=(\d+)%, threshold=(\d+)%.*expanding=(True|False), passed=([✅❌])',
        'green2_simple': r'GREEN 2: BB_width=([\d.]+), prev=([\d.]+), expanding=(True|False)',
        'green3': r'GREEN 3: (DISABLED|Volume_Z=([\d.-]+), threshold=([\d.]+), surge=(YES|NO))',
        'green4': r'GREEN 4: Price=([\d.]+), breakout_level=([\d.]+), breakout=(YES|NO)',

        # Structured analyzer line: [ANALYZER] [SYMBOL][G1:P/F][G2:P/F][G3:P/F][G4:P/F][BL:xxx][GAP:xxx%]
        'analyzer_line': r'\[ANALYZER\] \[(\w+/USDT)\]\[G1:(P|F)\]\[G2:(P|F)\]\[G3:(P|F)\]\[G4:(P|F)\]\[BL:([\d.]+)\]\[GAP:([\d.-]+)%\]',

        # Signal and order patterns
        'signal': r'🟢 SIGNAL GENERATED: (\w+/USDT) (BUY|SELL) @ ([\d.]+)',
        'order_executed': r'(🔶 \[DRY_RUN\]|✅) .*Order.*: (\w+/USDT).*side=(\w+).*price=([\d.]+)',
        'no_signal': r'❌ No signal - filters not all passed',

        # Error patterns
        'error': r'ERROR|Exception|Failed|error',
        'telegram_error': r'Telegram.*error|send.*failed',
    }

    def __init__(self, max_lines: int = 200000):
        self.max_lines = max_lines
        self.cycles: List[CycleData] = []
        self.symbol_metrics: Dict[str, SymbolMetrics] = defaultdict(lambda: SymbolMetrics(symbol=""))
        self.errors: List[str] = []
        self.parse_errors: List[str] = []

    def parse_file(self, log_path: str, days: int = 7) -> Dict[str, Any]:
        """Parse log file and return structured analysis data"""

        lines_processed = 0
        current_cycle: Optional[CycleData] = None
        current_symbol: Optional[str] = None
        current_filters: Dict[str, bool] = {}

        cutoff_date = datetime.now() - timedelta(days=days) if days else None

        try:
            with open(log_path, 'r', encoding='utf-8', errors='ignore') as f:
                # Read last N lines efficiently
                lines = self._tail_file(f, self.max_lines)
        except Exception as e:
            self.parse_errors.append(f"Failed to read log file: {e}")
            return self._build_result()

        for line in lines:
            lines_processed += 1

            try:
                # Extract timestamp
                ts_match = re.match(self.PATTERNS['timestamp'], line)
                if not ts_match:
                    continue

                timestamp = datetime.strptime(ts_match.group(1), '%Y-%m-%d %H:%M:%S,%f')

                # Skip if before cutoff
                if cutoff_date and timestamp < cutoff_date:
                    continue

                # Check for new cycle
                cycle_match = re.search(self.PATTERNS['cycle'], line)
                if cycle_match:
                    if current_cycle:
                        self._finalize_cycle(current_cycle)
                    current_cycle = CycleData(
                        timestamp=timestamp,
                        cycle_number=int(cycle_match.group(1))
                    )
                    current_symbol = None
                    current_filters = {}
                    continue

                # Check for symbol check start
                symbol_match = re.search(self.PATTERNS['symbol_check'], line)
                if symbol_match:
                    # Finalize previous symbol's filters
                    if current_symbol and current_filters and current_cycle:
                        self._process_filter_results(current_symbol, current_filters, current_cycle)

                    current_symbol = symbol_match.group(1)
                    current_filters = {}
                    if current_cycle:
                        current_cycle.symbols_checked.append(current_symbol)
                    continue

                # Parse GREEN filters
                if current_symbol:
                    self._parse_green_filters(line, timestamp, current_symbol, current_filters, current_cycle)

                # Check for signal
                signal_match = re.search(self.PATTERNS['signal'], line)
                if signal_match and current_cycle:
                    signal = SignalEvent(
                        timestamp=timestamp,
                        symbol=signal_match.group(1),
                        action=signal_match.group(2),
                        price=float(signal_match.group(3))
                    )
                    current_cycle.signals.append(signal)
                    self.symbol_metrics[signal.symbol].signal_count += 1

                # Check for "no signal" - finalize filters for current symbol
                if re.search(self.PATTERNS['no_signal'], line):
                    if current_symbol and current_filters and current_cycle:
                        self._process_filter_results(current_symbol, current_filters, current_cycle)
                        current_symbol = None
                        current_filters = {}

                # Parse structured analyzer line (preferred over individual filter parsing)
                analyzer_match = re.search(self.PATTERNS['analyzer_line'], line)
                if analyzer_match:
                    symbol = analyzer_match.group(1)
                    g1_pass = analyzer_match.group(2) == 'P'
                    g2_pass = analyzer_match.group(3) == 'P'
                    g3_pass = analyzer_match.group(4) == 'P'
                    g4_pass = analyzer_match.group(5) == 'P'
                    breakout_level = float(analyzer_match.group(6))
                    gap_pct = float(analyzer_match.group(7))

                    # Update metrics directly from structured line
                    metrics = self.symbol_metrics[symbol]
                    metrics.symbol = symbol
                    metrics.total_cycles += 1

                    # Track filter results
                    if g1_pass:
                        metrics.green1_pass += 1
                    else:
                        metrics.green1_fail += 1
                    if g2_pass:
                        metrics.green2_pass += 1
                    else:
                        metrics.green2_fail += 1
                    if g3_pass:
                        metrics.green3_pass += 1
                    else:
                        metrics.green3_fail += 1
                    if g4_pass:
                        metrics.green4_pass += 1
                    else:
                        metrics.green4_fail += 1
                        # Track gap for failed breakouts
                        if gap_pct > 0:
                            metrics.breakout_gaps.append(gap_pct)

                    # Near-miss detection (3 of 4 passed)
                    passed_count = sum([g1_pass, g2_pass, g3_pass, g4_pass])
                    if passed_count == 3:
                        metrics.near_miss_count += 1
                        if not g1_pass:
                            metrics.near_miss_blocking_filter['GREEN1'] += 1
                        if not g2_pass:
                            metrics.near_miss_blocking_filter['GREEN2'] += 1
                        if not g3_pass:
                            metrics.near_miss_blocking_filter['GREEN3'] += 1
                        if not g4_pass:
                            metrics.near_miss_blocking_filter['GREEN4'] += 1

                # Check for errors
                if re.search(self.PATTERNS['error'], line, re.IGNORECASE):
                    self.errors.append(line.strip()[:200])

            except Exception as e:
                self.parse_errors.append(f"Line {lines_processed}: {str(e)[:100]}")

        # Finalize last cycle
        if current_cycle:
            if current_symbol and current_filters:
                self._process_filter_results(current_symbol, current_filters, current_cycle)
            self._finalize_cycle(current_cycle)

        return self._build_result(lines_processed)

    def _parse_green_filters(
        self,
        line: str,
        timestamp: datetime,
        symbol: str,
        filters: Dict[str, bool],
        cycle: Optional[CycleData]
    ):
        """Parse GREEN filter results from a log line"""

        # GREEN 1
        g1_match = re.search(self.PATTERNS['green1'], line)
        if g1_match:
            passed = g1_match.group(3) == 'UP'
            filters['GREEN1'] = passed
            price = float(g1_match.group(1))

            if cycle:
                cycle.filter_results.append(FilterResult(
                    timestamp=timestamp,
                    symbol=symbol,
                    filter_name='GREEN1',
                    passed=passed,
                    price=price,
                    raw_message=line.strip()[:200]
                ))
            return

        # GREEN 2 (dynamic)
        g2d_match = re.search(self.PATTERNS['green2_dynamic'], line)
        if g2d_match:
            passed = g2d_match.group(4) == '✅'
            filters['GREEN2'] = passed

            # Track expansion percentile
            bb_pctl = int(g2d_match.group(1))
            threshold = int(g2d_match.group(2))
            metrics = self.symbol_metrics[symbol]
            metrics.expansion_values.append(bb_pctl)

            if cycle:
                cycle.filter_results.append(FilterResult(
                    timestamp=timestamp,
                    symbol=symbol,
                    filter_name='GREEN2',
                    passed=passed,
                    value=bb_pctl,
                    threshold=threshold,
                    raw_message=line.strip()[:200]
                ))
            return

        # GREEN 2 (simple)
        g2s_match = re.search(self.PATTERNS['green2_simple'], line)
        if g2s_match:
            passed = g2s_match.group(3) == 'True'
            filters['GREEN2'] = passed

            if cycle:
                cycle.filter_results.append(FilterResult(
                    timestamp=timestamp,
                    symbol=symbol,
                    filter_name='GREEN2',
                    passed=passed,
                    raw_message=line.strip()[:200]
                ))
            return

        # GREEN 3
        g3_match = re.search(self.PATTERNS['green3'], line)
        if g3_match:
            if 'DISABLED' in g3_match.group(1):
                filters['GREEN3'] = True  # Disabled = auto-pass
            else:
                passed = g3_match.group(4) == 'YES'
                filters['GREEN3'] = passed

            if cycle:
                cycle.filter_results.append(FilterResult(
                    timestamp=timestamp,
                    symbol=symbol,
                    filter_name='GREEN3',
                    passed=filters['GREEN3'],
                    raw_message=line.strip()[:200]
                ))
            return

        # GREEN 4
        g4_match = re.search(self.PATTERNS['green4'], line)
        if g4_match:
            passed = g4_match.group(3) == 'YES'
            filters['GREEN4'] = passed
            price = float(g4_match.group(1))
            breakout_level = float(g4_match.group(2))

            # Track breakout gap when failed
            if not passed and breakout_level > 0:
                gap_pct = abs(breakout_level - price) / breakout_level * 100
                self.symbol_metrics[symbol].breakout_gaps.append(gap_pct)

            if cycle:
                cycle.filter_results.append(FilterResult(
                    timestamp=timestamp,
                    symbol=symbol,
                    filter_name='GREEN4',
                    passed=passed,
                    price=price,
                    threshold=breakout_level,
                    raw_message=line.strip()[:200]
                ))
            return

    def _process_filter_results(
        self,
        symbol: str,
        filters: Dict[str, bool],
        cycle: CycleData
    ):
        """Process completed filter results for a symbol"""

        metrics = self.symbol_metrics[symbol]
        metrics.symbol = symbol
        metrics.total_cycles += 1

        # Count passes/fails
        if 'GREEN1' in filters:
            if filters['GREEN1']:
                metrics.green1_pass += 1
            else:
                metrics.green1_fail += 1

        if 'GREEN2' in filters:
            if filters['GREEN2']:
                metrics.green2_pass += 1
            else:
                metrics.green2_fail += 1

        if 'GREEN3' in filters:
            if filters['GREEN3']:
                metrics.green3_pass += 1
            else:
                metrics.green3_fail += 1

        if 'GREEN4' in filters:
            if filters['GREEN4']:
                metrics.green4_pass += 1
            else:
                metrics.green4_fail += 1

        # Check for near-miss (exactly 3 of 4 passed)
        passed_count = sum(1 for v in filters.values() if v)
        if passed_count == 3 and len(filters) == 4:
            metrics.near_miss_count += 1
            # Find which filter blocked
            for fname, passed in filters.items():
                if not passed:
                    metrics.near_miss_blocking_filter[fname] += 1

    def _finalize_cycle(self, cycle: CycleData):
        """Finalize and store a completed cycle"""
        self.cycles.append(cycle)

    def _tail_file(self, f, n: int) -> List[str]:
        """Efficiently read last N lines of file"""
        # Simple approach for now - read all and take last N
        lines = f.readlines()
        return lines[-n:] if len(lines) > n else lines

    def _build_result(self, lines_processed: int = 0) -> Dict[str, Any]:
        """Build the final result dictionary"""

        # Convert symbol metrics to dict
        symbols_data = {}
        for symbol, metrics in self.symbol_metrics.items():
            if metrics.total_cycles == 0:
                continue

            # Calculate derived metrics
            total_filter_checks = metrics.total_cycles
            near_miss_rate = (metrics.near_miss_count / total_filter_checks * 100) if total_filter_checks > 0 else 0

            # Breakout gap statistics
            gap_stats = {}
            if metrics.breakout_gaps:
                sorted_gaps = sorted(metrics.breakout_gaps)
                gap_stats = {
                    'min': min(sorted_gaps),
                    'max': max(sorted_gaps),
                    'mean': sum(sorted_gaps) / len(sorted_gaps),
                    'median': sorted_gaps[len(sorted_gaps)//2],
                    'p10': sorted_gaps[int(len(sorted_gaps)*0.1)] if len(sorted_gaps) > 10 else sorted_gaps[0],
                    'p90': sorted_gaps[int(len(sorted_gaps)*0.9)] if len(sorted_gaps) > 10 else sorted_gaps[-1],
                }

            # Expansion statistics
            expansion_stats = {}
            if metrics.expansion_values:
                sorted_exp = sorted(metrics.expansion_values)
                expansion_stats = {
                    'min': min(sorted_exp),
                    'max': max(sorted_exp),
                    'mean': sum(sorted_exp) / len(sorted_exp),
                    'median': sorted_exp[len(sorted_exp)//2],
                }

            symbols_data[symbol] = {
                'total_cycles': metrics.total_cycles,
                'signal_count': metrics.signal_count,
                'order_count': metrics.order_count,
                'filters': {
                    'GREEN1': {'pass': metrics.green1_pass, 'fail': metrics.green1_fail},
                    'GREEN2': {'pass': metrics.green2_pass, 'fail': metrics.green2_fail},
                    'GREEN3': {'pass': metrics.green3_pass, 'fail': metrics.green3_fail},
                    'GREEN4': {'pass': metrics.green4_pass, 'fail': metrics.green4_fail},
                },
                'near_miss': {
                    'count': metrics.near_miss_count,
                    'rate_pct': round(near_miss_rate, 2),
                    'blocking_filter': dict(metrics.near_miss_blocking_filter),
                },
                'breakout_gap_stats': gap_stats,
                'expansion_stats': expansion_stats,
            }

        # Time window
        time_window = {}
        if self.cycles:
            time_window = {
                'start': self.cycles[0].timestamp.isoformat(),
                'end': self.cycles[-1].timestamp.isoformat(),
                'total_cycles': len(self.cycles),
            }

        return {
            'time_window': time_window,
            'lines_processed': lines_processed,
            'total_cycles': len(self.cycles),
            'total_signals': sum(m.signal_count for m in self.symbol_metrics.values()),
            'total_orders': sum(m.order_count for m in self.symbol_metrics.values()),
            'symbols': symbols_data,
            'errors_count': len(self.errors),
            'parse_errors_count': len(self.parse_errors),
            'recent_errors': self.errors[-10:] if self.errors else [],
        }


# Import timedelta at module level
from datetime import timedelta
