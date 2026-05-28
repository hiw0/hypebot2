from __future__ import annotations

import pandas as pd

from .base import Strategy, Signal


class MomentumStrategy(Strategy):
    name = "momentum"

    def __init__(self, lookback: int = 20, atr_period: int = 14, min_atr_pct: float = 0.002):
        self.lb = lookback
        self.atr_period = atr_period
        self.min_atr_pct = min_atr_pct

    def on_bar(self, df: pd.DataFrame) -> Signal:
        if len(df) < max(self.lb + 1, self.atr_period + 2):
            return Signal("hold", 0.0)
        close = df["close"]
        high = df["high"]
        low = df["low"]
        prev_close = close.shift(1)
        tr = pd.concat([
            (high - low),
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ], axis=1).max(axis=1)
        atr = tr.rolling(self.atr_period).mean()
        # Use previous bar ATR to avoid lookahead
        atr_pct = float((atr.iloc[-2] / close.iloc[-2]) if close.iloc[-2] else 0.0)
        if atr_pct < self.min_atr_pct:
            return Signal("hold", meta={"atr_pct": atr_pct, "min_atr_pct": self.min_atr_pct})
        high_n = df["high"].rolling(self.lb).max().iloc[-2]  # use previous bar to avoid lookahead
        low_n = df["low"].rolling(self.lb).min().iloc[-2]
        last_close = close.iloc[-1]
        if last_close > high_n:
            return Signal("buy", meta={"atr_pct": atr_pct, "min_atr_pct": self.min_atr_pct})
        if last_close < low_n:
            return Signal("sell", meta={"atr_pct": atr_pct, "min_atr_pct": self.min_atr_pct})
        return Signal("hold", meta={"atr_pct": atr_pct, "min_atr_pct": self.min_atr_pct})
