from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Dict, Optional

import pandas as pd


class Signal:
    def __init__(self, action: str, target_qty: float = 0.0, meta: Optional[Dict] = None):
        self.action = action  # 'buy' | 'sell' | 'flat' | 'hold'
        self.target_qty = target_qty
        self.meta = meta or {}


class Strategy(ABC):
    name: str = "base"

    @abstractmethod
    def on_bar(self, df: pd.DataFrame) -> Signal:
        """Called with the full DataFrame; implement your logic using the last row.
        Return a Signal with action and optional target size.
        """
        raise NotImplementedError

