from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import pandas as pd

from ..datastore.redis_store import RedisStore


Side = Literal["buy", "sell"]


@dataclass
class PnLState:
    pos_qty: float = 0.0
    avg_cost: float = 0.0
    realized_pnl: float = 0.0
    fees: float = 0.0
    day: str = ""  # YYYY-MM-DD UTC
    realized_pnl_today: float = 0.0


class PnLTracker:
    def __init__(self, store: RedisStore):
        self.store = store

    def _load(self) -> PnLState:
        h = self.store.get_pnl()
        day = pd.Timestamp.utcnow().strftime("%Y-%m-%d")
        state = PnLState(
            pos_qty=float(h.get("pos_qty", 0.0) or 0.0),
            avg_cost=float(h.get("avg_cost", 0.0) or 0.0),
            realized_pnl=float(h.get("realized_pnl", 0.0) or 0.0),
            fees=float(h.get("fees", 0.0) or 0.0),
            day=str(h.get("day", day) or day),
            realized_pnl_today=float(h.get("realized_pnl_today", 0.0) or 0.0),
        )
        # Reset daily counters if day changed
        if state.day != day:
            state.day = day
            state.realized_pnl_today = 0.0
            self._persist(state)
        return state

    def _persist(self, s: PnLState) -> None:
        self.store.set_pnl_field("pos_qty", s.pos_qty)
        self.store.set_pnl_field("avg_cost", s.avg_cost)
        self.store.set_pnl_field("realized_pnl", s.realized_pnl)
        self.store.set_pnl_field("fees", s.fees)
        self.store.set_pnl_field("day", s.day)
        self.store.set_pnl_field("realized_pnl_today", s.realized_pnl_today)

    def process_trade(self, side: Side, px: float, qty: float, fee: float) -> None:
        s = self._load()
        if qty <= 0:
            return
        if side == "buy":
            # New average cost
            new_qty = s.pos_qty + qty
            if new_qty <= 0:  # closing/inverting position via buy on net short (unlikely here)
                # Treat as closing short: realized PnL on qty
                realized = (s.avg_cost - px) * qty  # short close: avg - px
                s.realized_pnl += realized
                s.realized_pnl_today += realized
                s.pos_qty = new_qty
            else:
                s.avg_cost = (s.avg_cost * s.pos_qty + px * qty) / new_qty if new_qty else 0.0
                s.pos_qty = new_qty
        else:  # sell
            new_qty = s.pos_qty - qty
            # Realized PnL for the closed part
            closed_qty = min(qty, max(0.0, s.pos_qty)) if s.pos_qty > 0 else qty
            realized = (px - s.avg_cost) * closed_qty
            s.realized_pnl += realized
            s.realized_pnl_today += realized
            s.pos_qty = new_qty
            if s.pos_qty < 0:
                # Went net short: set avg_cost to this trade px for the short leg
                s.avg_cost = px
            elif s.pos_qty == 0:
                s.avg_cost = 0.0
        s.fees += fee
        self._persist(s)

    def equity_estimate(self, last_px: float, start_equity: float = 10_000.0) -> float:
        s = self._load()
        return start_equity + s.realized_pnl + s.pos_qty * last_px - s.fees

    def realized_pnl_today(self) -> float:
        return self._load().realized_pnl_today

