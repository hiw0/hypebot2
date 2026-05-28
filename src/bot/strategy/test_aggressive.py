from __future__ import annotations

import pandas as pd
import random

from .base import Strategy, Signal


class TestAggressiveStrategy(Strategy):
    """Test strategy that generates immediate buy/sell signals to verify trading execution"""
    name = "test_aggressive"

    def __init__(self):
        self.signal_count = 0

    def on_bar(self, df: pd.DataFrame) -> Signal:
        # Generate alternating buy/sell signals for testing
        self.signal_count += 1
        
        if self.signal_count % 3 == 1:
            return Signal("buy", meta={"test": True, "count": self.signal_count})
        elif self.signal_count % 3 == 2:
            return Signal("sell", meta={"test": True, "count": self.signal_count})
        else:
            return Signal("hold", meta={"test": True, "count": self.signal_count})