from __future__ import annotations

import uuid
from typing import Optional, Dict

from loguru import logger

from .config import get_settings
from .exchange.hyperliquid_client import HyperliquidClient
from .risk import RiskConfig, RiskManager
from .strategy.base import Strategy, Signal
from .datastore.redis_store import RedisStore
from .metrics.pnl import PnLTracker


class Executor:
    def __init__(self, client: HyperliquidClient, strategy: Strategy, twap_minutes: int | None = None, twap_randomize: bool = False, reduce_only: bool = False):
        self.settings = get_settings()
        self.client = client
        self.strategy = strategy
        self.twap_minutes = twap_minutes
        self.twap_randomize = twap_randomize
        self.reduce_only = reduce_only
        self._last_action_ts: Dict[str, float] = {}
        cfg = RiskConfig(
            max_leverage=self.settings.max_leverage,
            max_position_usd=self.settings.max_position_usd,
            risk_per_trade=self.settings.risk_per_trade,
            min_order_value=10.0,
        )
        # Get real account equity instead of defaulting to $10k
        account_equity = self._get_account_equity()
        self.risk = RiskManager(cfg, equity_usd=account_equity)
        self.store = RedisStore()
        self.pnl = PnLTracker(self.store)

    def _get_account_equity(self) -> float:
        """Get real account equity from exchange"""
        try:
            user_state = self.client.info().user_state(self.settings.hl_wallet_address)
            margin_summary = user_state.get('marginSummary', {})
            account_value = float(margin_summary.get('accountValue', self.settings.paper_equity_usd))
            logger.info(f"Real account equity: ${account_value}")
            return account_value
        except Exception as e:
            logger.warning(f"Failed to get account equity, using fallback: {e}")
            return self.settings.paper_equity_usd

    def _gen_cloid(self) -> str:
        return uuid.uuid4().hex

    def execute_signal(
        self,
        signal: Signal,
        asset_id: int,
        coin: str,
        price: float,
        stop_px: Optional[float] = None,
    ) -> None:
        if signal.action not in {"buy", "sell", "hold", "flat"}:
            logger.info("No actionable signal: {}", signal.action)
            return

        if signal.action in {"hold", "flat"}:
            logger.info("Signal {} — no order placed", signal.action)
            return

        if stop_px is None:
            # simple default stop: 2% adverse
            stop_px = price * (0.98 if signal.action == "buy" else 1.02)

        # Cooldown per coin to avoid overtrading
        import time as _time
        now = _time.time()
        last = self._last_action_ts.get(coin, 0.0)
        if now - last < max(0, int(self.settings.min_cooldown_sec)):
            logger.info("Cooldown active for {}. Skipping.", coin)
            return

        # Check current position
        pos = self.client.get_position_for_coin(coin)
        current_sz = float(pos.get("szi", 0) if pos else 0)
        if current_sz > 0 and signal.action == "buy" and not self.settings.allow_same_side_add:
            logger.info("Already long {}. Skipping add.", coin)
            return
        if current_sz < 0 and signal.action == "sell" and not self.settings.allow_same_side_add:
            logger.info("Already short {}. Skipping add.", coin)
            return

        qty = self.risk.position_size(price, stop_px)
        if qty <= 0:
            logger.warning("Position size computed as 0. Skipping order.")
            return

        _, sz_decimals = self.client.get_decimals(asset_id)
        qty = round(qty, sz_decimals)

        # Log the calculated order details
        notional_value = qty * price
        logger.info(f"Order details: qty={qty}, price={price}, notional=${notional_value:.2f}")

        # Default to maker-only unless user explicitly opts into aggressing
        tif = "Alo" if bool(getattr(self.settings, "maker_only", True)) else "Gtc"
        is_buy = signal.action == "buy"
        cloid = self._gen_cloid()

        if self.settings.bot_mode == "paper":
            fee = qty * price * self.settings.fee_rate
            self.store.push_trade({
                "mode": "paper",
                "side": signal.action,
                "px": price,
                "qty": qty,
                "fee": fee,
                "time": str(uuid.uuid4())[:8],
            })
            self.pnl.process_trade("buy" if is_buy else "sell", price, qty, fee)
            logger.info("[PAPER] Executed {} px={} qty={} fee={}", signal.action, price, qty, fee)
            return

        # Before placing, avoid stacking: cancel any resting orders for this coin
        try:
            self.client.cancel_all_for_coin(coin)
        except Exception as e:
            logger.debug("Cancel existing orders for {} failed: {}", coin, e)

        # Live placement: choose TWAP vs limit order
        if self.twap_minutes and self.twap_minutes > 0:
            resp = self.client.place_twap_order(
                asset_id=asset_id,
                is_buy=is_buy,
                size=f"{qty}",
                minutes=self.twap_minutes,
                reduce_only=self.reduce_only,
                randomize=self.twap_randomize,
            )
        else:
            # Optionally make the limit price slightly aggressive to improve fill odds
            px = price
            try:
                bps = int(getattr(self.settings, "aggressive_bps", 0) or 0)
                if bps:
                    adj = 1 + (bps / 10_000.0) if is_buy else 1 - (bps / 10_000.0)
                    px = price * adj
                    # If we aggress, switch to IOC to avoid lingering taker orders
                    tif = "Ioc" if tif == "Alo" else tif
            except Exception:
                px = price
            resp = self.client.place_limit_order(
                asset_id=asset_id,
                is_buy=is_buy,
                price=f"{px}",
                size=f"{qty}",
                tif=tif,
                reduce_only=self.reduce_only,
                cloid=cloid,
            )
        logger.info("Order response: {}", resp)
        try:
            statuses = resp.get("response", {}).get("data", {}).get("statuses", [])
            if statuses:
                st = statuses[0]
                if "filled" in st:
                    filled = st["filled"]
                    fqty = float(filled.get("totalSz", qty))
                    fpx = float(filled.get("avgPx", price))
                    fee = fqty * fpx * self.settings.fee_rate
                    self.store.push_trade({"mode": "live", "side": signal.action, "px": fpx, "qty": fqty, "fee": fee})
                    self.pnl.process_trade("buy" if is_buy else "sell", fpx, fqty, fee)
        except Exception as e:
            logger.warning("Could not parse fill from response: {}", e)
        # Update last action timestamp only after attempting
        self._last_action_ts[coin] = now
