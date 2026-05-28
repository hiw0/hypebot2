from __future__ import annotations

import pandas as pd

from .base import Strategy, Signal


class MeanReversionStrategy(Strategy):
    name = "mean_reversion"

    def __init__(self, fast_window: int = 10, slow_window: int = 50, threshold: float = 0.005, 
                 fee_maker_bps: float = 1.5, slippage_bps: float = 2.0, mr_dyn_mult: float = 2.2):
        self.fast = fast_window
        self.slow = slow_window
        self.threshold = threshold
        self.fee_maker_bps = fee_maker_bps
        self.slippage_bps = slippage_bps
        self.mr_dyn_mult = mr_dyn_mult

    def on_bar(self, df: pd.DataFrame) -> Signal:
        if len(df) < max(self.fast, self.slow):
            return Signal("hold", 0.0)
        
        close = df["close"]
        fast = close.rolling(self.fast).mean()
        slow = close.rolling(self.slow).mean()
        
        last_fast = fast.iloc[-1]
        last_slow = slow.iloc[-1]
        mid = close.iloc[-1]
        
        pct = (last_fast - last_slow) / last_slow if last_slow else 0.0
        
        # Cost-based edge filter to prevent unprofitable trades
        # costs in bps
        cost_floor_bps = 2 * self.fee_maker_bps + self.slippage_bps + 1.0  # +1bp tick/adverse cushion
        required_edge_bps = max(11.0, self.mr_dyn_mult * cost_floor_bps)
        
        edge_bps = abs(last_fast - last_slow) / mid * 1e4 if mid > 0 else 0
        if edge_bps < required_edge_bps:
            return Signal("hold", meta={"pct": pct, "edge_bps": edge_bps, "required_edge_bps": required_edge_bps})
        
        if pct < -self.threshold:
            return Signal("buy", meta={"pct": pct, "edge_bps": edge_bps, "required_edge_bps": required_edge_bps})
        if pct > self.threshold:
            return Signal("sell", meta={"pct": pct, "edge_bps": edge_bps, "required_edge_bps": required_edge_bps})
        return Signal("hold", meta={"pct": pct, "edge_bps": edge_bps, "required_edge_bps": required_edge_bps})

