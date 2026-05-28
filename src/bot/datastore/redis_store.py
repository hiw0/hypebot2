from __future__ import annotations

from typing import Any, Dict, Optional, List
import os

import redis
from redis.exceptions import RedisError
from loguru import logger

from ..config import get_settings


class RedisStore:
    def __init__(self, url: Optional[str] = None):
        self.settings = get_settings()
        # Prefer explicit env var if set (avoids surprises if defaults differ)
        env_url = os.getenv("REDIS_URL")
        chosen = url or (env_url.strip() if env_url else None) or self.settings.redis_url
        # Remember the original URL to support a helpful fallback in ping()
        self._url = (chosen or "redis://localhost:6379/0").strip()
        self.client = redis.Redis.from_url(self._url, decode_responses=True)

    def ping(self) -> bool:
        try:
            return bool(self.client.ping())
        except Exception as e:
            logger.error("Redis ping failed: {}", e)
            # Common misconfigs and environment mismatches:
            # 1) Using hostname 'redis' when running locally
            # 2) Using 'localhost' inside a container (should be 'redis' with Compose)
            # 3) IPv6 localhost (::1) vs IPv4; prefer 127.0.0.1 to avoid IPv6 issues
            try:
                from urllib.parse import urlparse
                u = urlparse(self._url)
                host = (u.hostname or "").lower()
                port = u.port or 6379
                db_path = (u.path or "/0")

                # Case A: 'redis' -> try 127.0.0.1
                if host == "redis":
                    fallback_url = f"redis://127.0.0.1:{port}{db_path}"
                    logger.info("Retrying Redis on {} due to hostname 'redis'", fallback_url)
                    self.client = redis.Redis.from_url(fallback_url, decode_responses=True)
                    return bool(self.client.ping())

                # Case B: localhost/127.0.0.1 -> try service hostname 'redis'
                if host in {"localhost", "127.0.0.1", "::1"}:
                    fallback_url = f"redis://redis:{port}{db_path}"
                    logger.info("Retrying Redis on {} due to localhost inside container", fallback_url)
                    self.client = redis.Redis.from_url(fallback_url, decode_responses=True)
                    return bool(self.client.ping())
            except Exception as e2:
                logger.debug("Redis fallback attempt failed: {}", e2)
            return False

    def set_json(self, key: str, value: Dict[str, Any]) -> None:
        import orjson
        try:
            self.client.set(key, orjson.dumps(value))
        except RedisError as e:
            logger.warning("Redis set_json failed for {}: {}", key, e)

    def get_json(self, key: str) -> Optional[Dict[str, Any]]:
        import orjson
        try:
            val = self.client.get(key)
            return None if val is None else orjson.loads(val)
        except RedisError as e:
            logger.warning("Redis get_json failed for {}: {}", key, e)
            return None

    def incr_metric(self, key: str, amount: float) -> None:
        try:
            self.client.hincrbyfloat("metrics", key, amount)
        except RedisError as e:
            logger.warning("Redis incr_metric failed for {}: {}", key, e)

    def get_metrics(self) -> Dict[str, Any]:
        try:
            return self.client.hgetall("metrics")
        except RedisError as e:
            logger.warning("Redis get_metrics failed: {}", e)
            return {}

    # Trades list helpers (most recent first)
    def push_trade(self, trade: Dict[str, Any]) -> None:
        import orjson
        try:
            self.client.lpush("trades", orjson.dumps(trade))
            # Trim to last 500
            self.client.ltrim("trades", 0, 499)
        except RedisError as e:
            logger.warning("Redis push_trade failed: {}", e)

    def get_trades(self, limit: int = 100) -> list[Dict[str, Any]]:
        import orjson
        try:
            items = self.client.lrange("trades", 0, max(0, limit - 1))
            return [orjson.loads(x) for x in items]
        except RedisError as e:
            logger.warning("Redis get_trades failed: {}", e)
            return []

    # PnL hash helpers
    def get_pnl(self) -> Dict[str, Any]:
        try:
            return self.client.hgetall("pnl")
        except RedisError as e:
            logger.warning("Redis get_pnl failed: {}", e)
            return {}

    def set_pnl_field(self, key: str, value: Any) -> None:
        try:
            self.client.hset("pnl", key, value)
        except RedisError as e:
            logger.warning("Redis set_pnl_field failed ({}): {}", key, e)

    def incr_pnl_field(self, key: str, amount: float) -> None:
        try:
            self.client.hincrbyfloat("pnl", key, amount)
        except RedisError as e:
            logger.warning("Redis incr_pnl_field failed ({}): {}", key, e)


class MemoryStore:
    """
    In-memory fallback when Redis is unavailable. Implements the subset of
    RedisStore API that the bot uses so the bot can run without persistence.
    """
    def __init__(self):
        self._kvs: Dict[str, Any] = {}
        self._metrics: Dict[str, float] = {}
        self._trades: List[bytes] = []  # store bytes to mirror Redis serialization
        self._pnl: Dict[str, Any] = {}

    # Compatibility
    def ping(self) -> bool:
        return True

    # JSON KV
    def set_json(self, key: str, value: Dict[str, Any]) -> None:
        self._kvs[key] = value

    def get_json(self, key: str) -> Optional[Dict[str, Any]]:
        return self._kvs.get(key)

    # Metrics hash
    def incr_metric(self, key: str, amount: float) -> None:
        self._metrics[key] = float(self._metrics.get(key, 0.0)) + float(amount)

    def get_metrics(self) -> Dict[str, Any]:
        # Return as str->str like Redis hgetall
        return {k: str(v) for k, v in self._metrics.items()}

    # Trades list
    def push_trade(self, trade: Dict[str, Any]) -> None:
        import orjson
        self._trades.insert(0, orjson.dumps(trade))
        self._trades = self._trades[:500]

    def get_trades(self, limit: int = 100) -> list[Dict[str, Any]]:
        import orjson
        items = self._trades[: max(0, limit)]
        return [orjson.loads(x) for x in items]

    # PnL hash helpers
    def get_pnl(self) -> Dict[str, Any]:
        # Return as str->str to mirror Redis behavior
        return {k: str(v) if isinstance(v, (int, float)) else v for k, v in self._pnl.items()}

    def set_pnl_field(self, key: str, value: Any) -> None:
        self._pnl[key] = value

    def incr_pnl_field(self, key: str, amount: float) -> None:
        self._pnl[key] = float(self._pnl.get(key, 0.0)) + float(amount)
