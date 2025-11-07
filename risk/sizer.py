def calculate_size(equity, price, sl_price, risk_pct):
    risk_per_unit = abs(price - sl_price)
    if risk_per_unit == 0:
        return 0
    notional = equity * risk_pct / risk_per_unit
    return notional / price
