from __future__ import annotations

import asyncio
import json
from typing import AsyncIterator, Dict, Optional

import websockets
from loguru import logger

from ..config import get_settings
from ..exchange.hyperliquid_client import HyperliquidClient


class NotificationStream:
    def __init__(self, client: HyperliquidClient, user_address: str):
        self.client = client
        self.user = user_address
        self.url = client.ws_url

    async def _subscribe(self, ws) -> None:
        sub = {"method": "subscribe", "subscription": {"type": "notification", "user": self.user}}
        await ws.send(json.dumps(sub))

    async def stream(self) -> AsyncIterator[Dict]:
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
                        ch = msg.get("channel")
                        if ch == "subscriptionResponse":
                            continue
                        yield msg
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning("WS notif error: {} — reconnecting in {}s", e, backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30)

