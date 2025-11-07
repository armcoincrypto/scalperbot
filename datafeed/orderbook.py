class OrderBook:
    def __init__(self):
        self.bids = {}
        self.asks = {}
        self.ts = 0

    def apply_snapshot(self, data: dict):
        self.bids = {float(p): float(q) for p, q in data['bids']}
        self.asks = {float(p): float(q) for p, q in data['asks']}
        self.ts = data['lastUpdateId']

    def apply_delta(self, data: dict):
        for side, updates in [('bids', data['b']), ('asks', data['a'])]:
            book = self.bids if side == 'bids' else self.asks
            for p, q in updates:
                p, q = float(p), float(q)
                if q == 0:
                    book.pop(p, None)
                else:
                    book[p] = q

    def mid(self) -> float:
        best_bid = max(self.bids.keys()) if self.bids else 0
        best_ask = min(self.asks.keys()) if self.asks else 0
        return (best_bid + best_ask) / 2 if best_bid and best_ask else 0

    def spread_bps(self) -> float:
        if not self.bids or not self.asks:
            return 999
        return (min(self.asks) - max(self.bids)) / self.mid() * 10000

    def depth_usd(self, bps: int = 10) -> float:
        mid = self.mid()
        total = 0.0
        for p, q in self.bids.items():
            if (mid - p) / mid * 10000 <= bps:
                total += p * q
        for p, q in self.asks.items():
            if (p - mid) / mid * 10000 <= bps:
                total += p * q
        return total
