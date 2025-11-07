class Breakers:
    def __init__(self, config):
        self.config = config
        self.daily_pnl = 0.0
        self.weekly_high = 0.0
        self.current_dd = 0.0

    def check_daily_loss(self):
        return self.daily_pnl <= -self.config.daily_loss_pct * self.config.equity_usd_cap

    def check_weekly_dd(self):
        return self.current_dd >= self.config.weekly_dd_pct
