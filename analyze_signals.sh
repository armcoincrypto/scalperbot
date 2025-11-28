#!/bin/bash
# Signal Analysis Script
# Analyzes first N signals from logs and provides quality assessment

set -e

LOG_FILE="/home/user/scalperbot/bot.log"
DB_FILE="/home/user/scalperbot/scalperbot/trades.db"

echo "=================================================="
echo "📊 SIGNAL ANALYSIS REPORT"
echo "=================================================="
echo ""

# Check if log file exists
if [ ! -f "$LOG_FILE" ]; then
    echo "❌ Error: Log file not found at $LOG_FILE"
    exit 1
fi

# Count total signals
TOTAL_SIGNALS=$(grep "🟢 SIGNAL GENERATED" "$LOG_FILE" | wc -l)
echo "Total signals generated: $TOTAL_SIGNALS"
echo ""

if [ "$TOTAL_SIGNALS" -eq 0 ]; then
    echo "No signals found yet. Bot is waiting for market conditions."
    echo ""
    echo "Current filter status (last check):"
    tail -100 "$LOG_FILE" | grep "GREEN 2" | tail -4
    exit 0
fi

echo "=================================================="
echo "📈 SIGNAL BREAKDOWN BY PAIR"
echo "=================================================="
echo ""

for PAIR in "BTC/USDT" "ETH/USDT" "SOL/USDT" "XRP/USDT"; do
    COUNT=$(grep "🟢 SIGNAL GENERATED: $PAIR" "$LOG_FILE" | wc -l)
    echo "$PAIR: $COUNT signals"
done

echo ""
echo "=================================================="
echo "🔗 CORRELATION ANALYSIS"
echo "=================================================="
echo ""

CORRELATION_COUNT=$(grep "🔗 CORRELATION" "$LOG_FILE" | wc -l)
echo "Correlated signal events: $CORRELATION_COUNT"

if [ "$CORRELATION_COUNT" -gt 0 ]; then
    echo ""
    echo "Correlation details:"
    grep "🔗 CORRELATION" "$LOG_FILE" | tail -5
fi

echo ""
echo "=================================================="
echo "📊 FILTER DETAILS (First 10 Signals)"
echo "=================================================="
echo ""

# Extract first 10 signals with context
SIGNAL_COUNT=0
grep -n "🟢 SIGNAL GENERATED" "$LOG_FILE" | head -10 | while IFS=: read -r LINE_NUM SIGNAL_LINE; do
    SIGNAL_COUNT=$((SIGNAL_COUNT + 1))

    echo "--- Signal #$SIGNAL_COUNT ---"
    echo "$SIGNAL_LINE"

    # Get the 10 lines before signal (filter checks)
    START_LINE=$((LINE_NUM - 10))
    sed -n "${START_LINE},${LINE_NUM}p" "$LOG_FILE" | grep "GREEN"

    echo ""
done

echo ""
echo "=================================================="
echo "⏱️ SIGNAL TIMING ANALYSIS"
echo "=================================================="
echo ""

echo "Signal timestamps (all):"
grep "🟢 SIGNAL GENERATED" "$LOG_FILE" | awk '{print $1, $2, $NF}'

echo ""
echo "Time between signals:"
grep "🟢 SIGNAL GENERATED" "$LOG_FILE" | awk '{print $1, $2}' | \
    awk 'NR>1 {print "  ", $0, " (", NR-1, " signals)"}'

echo ""
echo "=================================================="
echo "💰 EXECUTION QUALITY (if trades executed)"
echo "=================================================="
echo ""

if [ -f "$DB_FILE" ]; then
    echo "Trades from database:"
    sqlite3 "$DB_FILE" "SELECT
        entry_time,
        symbol,
        side,
        price,
        quantity,
        notional,
        status
    FROM trades
    ORDER BY entry_time DESC
    LIMIT 10;" -header -column
else
    echo "⚠️ Database file not found"
fi

echo ""
echo "=================================================="
echo "🎯 QUALITY ASSESSMENT"
echo "=================================================="
echo ""

# Calculate signal rate (signals per hour)
FIRST_SIGNAL_TIME=$(grep "🟢 SIGNAL GENERATED" "$LOG_FILE" | head -1 | awk '{print $1, $2}')
LAST_SIGNAL_TIME=$(grep "🟢 SIGNAL GENERATED" "$LOG_FILE" | tail -1 | awk '{print $1, $2}')

echo "First signal: $FIRST_SIGNAL_TIME"
echo "Last signal: $LAST_SIGNAL_TIME"
echo ""

# Calculate correlation ratio
if [ "$TOTAL_SIGNALS" -gt 0 ]; then
    CORRELATION_RATIO=$((CORRELATION_COUNT * 100 / TOTAL_SIGNALS))
    echo "Correlation ratio: $CORRELATION_RATIO% ($CORRELATION_COUNT/$TOTAL_SIGNALS)"
else
    CORRELATION_RATIO=0
fi

echo ""
echo "=================================================="
echo "✅ RECOMMENDATION"
echo "=================================================="
echo ""

# Provide recommendation based on signal count and correlation
if [ "$TOTAL_SIGNALS" -lt 3 ]; then
    echo "🟢 Status: TOO EARLY TO ASSESS"
    echo "   Recommendation: Continue monitoring, need more signals"
    echo "   Action: Wait for at least 5 signals before making decision"
elif [ "$TOTAL_SIGNALS" -gt 20 ]; then
    echo "🔴 Status: HIGH SIGNAL RATE"
    echo "   Recommendation: Filters may be too loose"
    echo "   Action: Consider rollback to STRICT mode"
    echo "   See: ROLLBACK_PLAN.md"
elif [ "$CORRELATION_RATIO" -gt 80 ]; then
    echo "🟡 Status: HIGH CORRELATION"
    echo "   Recommendation: Filters catching market-wide moves"
    echo "   Action: Consider reducing position size or adding pair-specific filters"
else
    echo "🟢 Status: SIGNAL QUALITY LOOKS REASONABLE"
    echo "   Recommendation: Continue experiment"
    echo "   Action: Monitor for another 12-24 hours"
fi

echo ""
echo "=================================================="
echo "📋 NEXT STEPS"
echo "=================================================="
echo ""

echo "1. Review filter details above - do expansion rates make sense?"
echo "2. Check if signals are distributed across pairs or concentrated"
echo "3. If correlation is high (>80%), consider if this is market-wide momentum"
echo "4. If signal rate is high (>10/day), consider tightening thresholds"
echo "5. Update DAILY_REPORT_TEMPLATE.md with findings"
echo ""

echo "For detailed rollback procedure, see: ROLLBACK_PLAN.md"
echo ""
echo "Report generated: $(date)"
echo "=================================================="
