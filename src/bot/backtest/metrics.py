from __future__ import annotations

import numpy as np
import pandas as pd


def equity_curve(returns: pd.Series, start_equity: float = 10_000.0) -> pd.Series:
    return start_equity * (1.0 + returns.fillna(0.0)).cumprod()


def sharpe_ratio(returns: pd.Series, periods_per_year: int = 365) -> float:
    r = returns.fillna(0.0)
    if r.std() == 0:
        return 0.0
    return float(np.sqrt(periods_per_year) * r.mean() / r.std())


def sortino_ratio(returns: pd.Series, periods_per_year: int = 365) -> float:
    r = returns.fillna(0.0)
    downside = r[r < 0]
    dd = downside.std()
    if dd == 0:
        return 0.0
    return float(np.sqrt(periods_per_year) * r.mean() / dd)


def max_drawdown(equity: pd.Series) -> float:
    roll_max = equity.cummax()
    drawdown = equity / roll_max - 1.0
    return float(drawdown.min())


def summarize(returns: pd.Series, start_equity: float = 10_000.0) -> dict:
    eq = equity_curve(returns, start_equity)
    return {
        "start_equity": start_equity,
        "end_equity": float(eq.iloc[-1]),
        "total_return": float(eq.iloc[-1] / start_equity - 1.0),
        "sharpe": sharpe_ratio(returns),
        "sortino": sortino_ratio(returns),
        "max_drawdown": max_drawdown(eq),
        "n_trades": int((returns != 0).sum()),
    }

