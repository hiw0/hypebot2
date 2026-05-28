from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import AsyncIterator, Dict, Optional

import pandas as pd
import websockets
from loguru import logger

from ..config import get_settings
from ..exchange.hyperliquid_client import HyperliquidClient


@dataclass
class Candle:
    ts: pd.Timestamp
    open: float
    high: float
    low: float
    close: float
    volume: float


class CandleBuffer:
    def __init__(self):
        self.df = pd.DataFrame(columns=["open", "high", "low", "close", "volume"]).astype(float)

    def upsert(self, c: Candle):
        if c.ts in self.df.index:
            self.df.loc[c.ts, ["open", "high", "low", "close", "volume"]] = [
                c.open,
                c.high,
                c.low,
                c.close,
                c.volume,
            ]
        else:
            self.df.loc[c.ts, ["open", "high", "low", "close", "volume"]] = [
                c.open,
                c.high,
                c.low,
                c.close,
                c.volume,
            ]
        self.df.sort_index(inplace=True)


class CandleStream:
    def __init__(self, client: HyperliquidClient, coin: str, interval: str):
        self.client = client
        self.coin = coin
        self.interval = interval
        self.url = client.ws_url

    async def _subscribe(self, ws) -> None:
        sub = {
            "method": "subscribe",
            "subscription": {"type": "candle", "coin": self.coin, "interval": self.interval},
        }
        await ws.send(json.dumps(sub))

    def _parse_candle(self, msg: Dict) -> Optional[Candle]:
        # Hyperliquid WS pushes candle updates; handle various shapes defensively
        data = msg.get("data") if isinstance(msg, dict) else None
        if isinstance(data, dict):
            # Common fields: time or timestamp, and o/h/l/c/v
            ts = data.get("time") or data.get("timestamp") or data.get("t")
            if ts is not None:
                try:
                    # assume ms if too large
                    ts = pd.to_datetime(int(ts), unit="ms" if int(ts) > 10_000_000_000 else "s", utc=True)
                except Exception:
                    ts = pd.Timestamp.utcnow()
                # extract prices
                o = float(data.get("open") or data.get("o") or data.get("limitPx") or data.get("close"))
                h = float(data.get("high") or data.get("h") or o)
                l = float(data.get("low") or data.get("l") or o)
                c = float(data.get("close") or data.get("c") or o)
                v = float(data.get("volume") or data.get("v") or 0.0)
                return Candle(ts=ts, open=o, high=h, low=l, close=c, volume=v)
        return None

    async def stream(self) -> AsyncIterator[Candle]:
        backoff = 1
        while True:
            try:
                async with websockets.connect(self.url, ping_interval=20, ping_timeout=20) as ws:
                    await self._subscribe(ws)
                    backoff = 1
                    async for raw in ws:
                        try:
                            msg = json.loads(raw)
                        except Exception:
                            continue
                        # Ignore subscription acks
                        ch = msg.get("channel")
                        if ch == "subscriptionResponse":
                            continue
                        # Attempt to parse candle
                        candle = self._parse_candle(msg)
                        if candle:
                            yield candle
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning("WS error: {} — reconnecting in {}s", e, backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30)

