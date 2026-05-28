from __future__ import annotations

from dataclasses import dataclass


@dataclass
class RiskConfig:
    max_leverage: float
    max_position_usd: float
    risk_per_trade: float  # fraction of equity, e.g., 0.01
    min_order_value: float = 10.0  # minimum order value in USD


class RiskManager:
    def __init__(self, cfg: RiskConfig, equity_usd: float = 10000.0):
        self.cfg = cfg
        self.equity = equity_usd

    def update_equity(self, equity_usd: float) -> None:
        self.equity = equity_usd

    def position_size(self, entry_px: float, stop_px: float) -> float:
        # Fixed-fraction risk: size such that loss to stop is risk_per_trade * equity
        risk_usd = self.equity * self.cfg.risk_per_trade
        per_unit_risk = abs(entry_px - stop_px)
        if per_unit_risk <= 0:
            return 0.0
        raw_qty = risk_usd / per_unit_risk
        notional = raw_qty * entry_px
        
        # Enforce minimum order value
        if notional < self.cfg.min_order_value:
            raw_qty = self.cfg.min_order_value / entry_px
            notional = raw_qty * entry_px
        
        # Check against max position limit
        if notional > self.cfg.max_position_usd:
            raw_qty = self.cfg.max_position_usd / entry_px
        
        return max(0.0, raw_qty)

    def can_open(self, current_leverage: float, new_notional: float, price: float) -> bool:
        if current_leverage > self.cfg.max_leverage:
            return False
        if new_notional > self.cfg.max_position_usd:
            return False
        return True

