from __future__ import annotations

from typing import Optional

import pandas as pd


def load_ohlcv_csv(path: str, tz: Optional[str] = None) -> pd.DataFrame:
    """Load OHLCV CSV with columns: timestamp, open, high, low, close, volume.
    - timestamp is in ms or seconds; autodetect.
    - returns DataFrame indexed by UTC datetime with float columns.
    """
    df = pd.read_csv(path)
    cols = {c.lower(): c for c in df.columns}
    required = ["timestamp", "open", "high", "low", "close", "volume"]
    for r in required:
        if r not in cols:
            raise ValueError(f"CSV missing column: {r}")
    ts = df[cols["timestamp"]]
    if ts.max() > 10_000_000_000:  # ms
        dt = pd.to_datetime(ts, unit="ms", utc=True)
    else:
        dt = pd.to_datetime(ts, unit="s", utc=True)
    if tz:
        dt = dt.tz_convert(tz)
    out = pd.DataFrame(
        {
            "open": df[cols["open"]].astype(float),
            "high": df[cols["high"]].astype(float),
            "low": df[cols["low"]].astype(float),
            "close": df[cols["close"]].astype(float),
            "volume": df[cols["volume"]].astype(float),
        },
        index=dt,
    )
    return out.sort_index()

