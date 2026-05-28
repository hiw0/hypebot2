from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple

import numpy as np
import pandas as pd

from ..strategy.base import Strategy


@dataclass
class BacktestConfig:
    fee_rate: float = 0.0005  # 5 bps per trade side
    slippage_bps: float = 2.0  # 2 bps per fill
    start_equity: float = 10_000.0


class Backtester:
    def __init__(self, df: pd.DataFrame, strategy: Strategy, cfg: BacktestConfig):
        self.df = df.copy()
        self.strategy = strategy
        self.cfg = cfg
        self._validate()

    def _validate(self) -> None:
        for col in ("open", "high", "low", "close"):
            if col not in self.df.columns:
                raise ValueError(f"Missing column {col}")

    def _apply_slippage(self, px: float, side: str) -> float:
        bps = self.cfg.slippage_bps / 10_000.0
        if side == "buy":
            return px * (1 + bps)
        else:
            return px * (1 - bps)

    def _fees(self, notional: float) -> float:
        return notional * self.cfg.fee_rate

    def run(self) -> Tuple[pd.Series, pd.DataFrame]:
        df = self.df
        n = len(df)
        position = 0.0  # qty in coin
        cash = self.cfg.start_equity
        equity_series = []
        returns = []
        trades = []

        # We use bar-close signal, transact on next open
        for i in range(1, n):
            hist = df.iloc[: i + 1]
            signal = self.strategy.on_bar(hist)
            next_open = df["open"].iloc[i]
            close_px = df["close"].iloc[i]
            # mark-to-market equity before acting
            equity_before = cash + position * close_px

            if signal.action in ("buy", "sell"):
                side = signal.action
                px = self._apply_slippage(next_open, side)
                # Target position: 1x notional equal to equity unless strategy specifies target_qty
                target_qty = signal.target_qty if signal.target_qty > 0 else (equity_before / px)
                if side == "sell":
                    target_qty = -target_qty

                delta_qty = target_qty - position
                if abs(delta_qty) > 1e-9:
                    notional = abs(delta_qty) * px
                    fee = self._fees(notional)
                    # execute
                    cash -= delta_qty * px  # buy reduces cash; sell increases cash
                    cash -= fee
                    position += delta_qty
                    trades.append({
                        "time": df.index[i],
                        "side": "buy" if delta_qty > 0 else "sell",
                        "px": float(px),
                        "qty": float(abs(delta_qty)),
                        "fee": float(fee),
                    })

            equity_after = cash + position * close_px
            retn = 0.0 if equity_before == 0 else (equity_after / equity_before - 1.0)
            equity_series.append(equity_after)
            returns.append(retn)

        returns_s = pd.Series(returns, index=df.index[1:])
        trades_df = pd.DataFrame(trades)
        return returns_s, trades_df

