from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from loguru import logger

from ..config import get_settings


class HyperliquidClient:
    """Thin wrapper around Hyperliquid Python SDK for Info + Exchange.

    This class centralizes SDK usage, adds retries, and provides typed helpers.
    """

    def __init__(self, private_key: Optional[str] = None, wallet_address: Optional[str] = None, env: str = "testnet"):
        self.settings = get_settings()
        self.env = env or self.settings.hl_env
        # Align URLs with docs
        self.api_url = "https://api.hyperliquid.xyz" if self.env == "mainnet" else "https://api.hyperliquid-testnet.xyz"
        # Use env-specific WS endpoint
        self.ws_url = "wss://api.hyperliquid.xyz/ws" if self.env == "mainnet" else "wss://api.hyperliquid-testnet.xyz/ws"
        self.private_key = private_key or self.settings.hl_private_key
        self.wallet_address = wallet_address or self.settings.hl_wallet_address

        # Lazily import SDK to allow unit tests without dependency
        try:
            from hyperliquid.info import Info  # type: ignore
            from hyperliquid.utils import constants  # type: ignore
            self.InfoCls = Info
            self.constants = constants
        except Exception as e:
            logger.warning("hyperliquid-python-sdk not available. Install to use live APIs. Error: {}", e)
            self.InfoCls = None
            self.constants = None

        # Exchange/trade client from SDK, if available
        self._trade = None
        try:
            from hyperliquid.exchange import Exchange  # type: ignore
            self.ExchangeCls = Exchange
        except Exception:
            self.ExchangeCls = None

        self._info = None

    def info(self):
        if self._info is None:
            if not self.InfoCls:
                raise RuntimeError("hyperliquid-python-sdk is required for info() usage.")
            # constants.<ENV>_API_URL are defined in SDK
            api_url = self.constants.MAINNET_API_URL if self.env == "mainnet" else self.constants.TESTNET_API_URL
            self._info = self.InfoCls(api_url, skip_ws=True)
        return self._info

    def trade(self):
        if self._trade is None:
            if not self.ExchangeCls:
                raise RuntimeError("hyperliquid-python-sdk is required for trade() usage.")
            if not self.private_key or not self.wallet_address:
                raise RuntimeError("HL_PRIVATE_KEY and HL_WALLET_ADDRESS are required for trading.")
            
            # Create LocalAccount wallet from private key
            try:
                from eth_account import Account
                wallet = Account.from_key(self.private_key)
            except Exception as e:
                raise RuntimeError(f"Failed to create wallet from private key: {e}")
            
            # Use base_url instead of is_mainnet parameter for recent SDKs
            base_url = "https://api.hyperliquid.xyz" if self.env == "mainnet" else "https://api.hyperliquid-testnet.xyz"
            self._trade = self.ExchangeCls(wallet, base_url=base_url)
        return self._trade

    # -------- Info helpers --------
    def get_all_mids(self) -> Dict[str, str]:
        info = self.info()
        res = info.all_mids()  # SDK convenience method; falls back to /info allMids
        return res

    def get_user_open_orders(self, address: Optional[str] = None) -> List[Dict[str, Any]]:
        info = self.info()
        addr = address or self.wallet_address
        if not addr:
            raise RuntimeError("wallet address required")
        return info.open_orders(addr)

    def get_asset_id(self, coin: str) -> Optional[int]:
        """Return perp asset id for a given coin symbol using meta.universe index."""
        info = self.info()
        meta = info.meta()
        universe = meta.get("universe", [])
        for idx, c in enumerate(universe):
            if c.get("name") == coin:
                return idx
        return None

    def get_coin_name(self, asset_id: int) -> str:
        info = self.info()
        meta = info.meta()
        universe = meta.get("universe", [])
        if asset_id < 0 or asset_id >= len(universe):
            raise RuntimeError(f"Invalid asset_id: {asset_id}")
        return universe[asset_id].get("name")

    def get_decimals(self, asset_id: int) -> tuple[int, int]:
        """Return (px_decimals, sz_decimals) for coin, with safe fallbacks.
        Keys vary by SDK; try common ones.
        """
        info = self.info()
        meta = info.meta()
        universe = meta.get("universe", [])
        if asset_id < 0 or asset_id >= len(universe):
            return (2, 4)
        c = universe[asset_id]
        # Try common field names; fall back conservatively
        px_dec = c.get("pxDecimals") or c.get("priceDecimals") or 2
        sz_dec = c.get("szDecimals") or c.get("sizeDecimals") or 4
        try:
            return (int(px_dec), int(sz_dec))
        except Exception:
            return (2, 4)

    # -------- Trading helpers --------
    def place_limit_order(
        self,
        asset_id: int,
        is_buy: bool,
        price: str,
        size: str,
        tif: str = "Gtc",
        reduce_only: bool = False,
        cloid: Optional[str] = None,
        expires_ms: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Place a single limit order. Returns raw response.

        Uses the new v0.18.0 order method.
        """
        trade = self.trade()
        
        # Resolve coin + quantize to instrument decimals
        coin_name = self.get_coin_name(asset_id)
        px_dec, sz_dec = self.get_decimals(asset_id)
        try:
            q_px = round(float(price), px_dec)
            q_sz = round(float(size), sz_dec)
        except Exception:
            q_px = float(price)
            q_sz = float(size)

        # Skip cloid if incompatible
        cloid_obj = cloid

        # Use the new order method with dictionary format for order_type
        response = trade.order(
            coin_name,
            is_buy,
            float(q_sz),
            float(q_px),
            {"limit": {"tif": tif}},
            reduce_only=reduce_only,
            cloid=cloid_obj
        )
        return response

    def cancel_order(self, asset_id: int, oid: int) -> Dict[str, Any]:
        trade = self.trade()
        
        # Get coin name from asset_id for the new API
        coin_name = self.get_coin_name(asset_id)
        
        return trade.cancel(coin_name, oid)

    def cancel_by_cloid(self, asset_id: int, cloid: str) -> Dict[str, Any]:
        trade = self.trade()
        
        # Get coin name from asset_id for the new API
        coin_name = self.get_coin_name(asset_id)
        
        return trade.cancel_by_cloid(coin_name, cloid)

    def schedule_cancel(self, when_ms: Optional[int]) -> Dict[str, Any]:
        trade = self.trade()
        return trade.schedule_cancel(when_ms)

    def place_twap_order(
        self,
        asset_id: int,
        is_buy: bool,
        size: str,
        minutes: int,
        reduce_only: bool = False,
        randomize: bool = False,
    ) -> Dict[str, Any]:
        trade = self.trade()
        coin_name = self.get_coin_name(asset_id)
        # Try SDK native TWAP methods first
        try:
            if hasattr(trade, "twap") and callable(getattr(trade, "twap")):
                return trade.twap(coin_name, is_buy, str(size), int(minutes), reduce_only=reduce_only, randomize=randomize)
            if hasattr(trade, "twap_order") and callable(getattr(trade, "twap_order")):
                return trade.twap_order(coin_name, is_buy, str(size), int(minutes), reduce_only=reduce_only, randomize=randomize)
        except Exception as e:
            logger.warning("SDK TWAP call failed, falling back to limit: {}", e)
        # Fallback: place a limit order at current mid (acts like market-ish if combined with Ioc upstream)
        info = self.info()
        mids = info.all_mids()
        current_price = float(mids.get(coin_name, "0"))
        if current_price <= 0:
            raise RuntimeError(f"No market price for {coin_name}")
        return self.place_limit_order(asset_id, is_buy, str(current_price), size, reduce_only=reduce_only)

    def cancel_twap(self, twap_id: int) -> Dict[str, Any]:
        trade = self.trade()
        try:
            # Prefer SDK if available
            if hasattr(trade, "cancel_twap") and callable(getattr(trade, "cancel_twap")):
                return trade.cancel_twap(twap_id)
            if hasattr(trade, "twap_cancel") and callable(getattr(trade, "twap_cancel")):
                return trade.twap_cancel(twap_id)
        except Exception as e:
            logger.warning("SDK TWAP cancel failed: {}", e)
        return {"status": "error", "message": "TWAP cancel not available in SDK"}

    def update_leverage(self, asset_id: int, is_cross: bool, leverage: int) -> Dict[str, Any]:
        trade = self.trade()
        # Get coin name from asset_id for the new API
        coin_name = self.get_coin_name(asset_id)
        return trade.update_leverage(leverage, coin_name, is_cross)

    def update_isolated_margin(self, asset_id: int, is_buy: bool, ntli: int) -> Dict[str, Any]:
        trade = self.trade()
        
        # Get coin name from asset_id for the new API  
        coin_name = self.get_coin_name(asset_id)
        
        return trade.update_isolated_margin(coin_name, is_buy, ntli)

    def batch_cancel_oids(self, cancels: list[dict]) -> Dict[str, Any]:
        trade = self.trade()
        
        # Convert old format to new bulk_cancel format
        # Old: [{"a": asset_id, "o": oid}]
        # New: bulk_cancel expects coin names and oids
        info = self.info()
        meta = info.meta()
        universe = meta.get("universe", [])
        
        cancel_requests = []
        for cancel in cancels:
            asset_id = cancel.get("a")
            oid = cancel.get("o")
            if asset_id is not None and oid is not None:
                if asset_id >= len(universe):
                    continue
                coin_name = universe[asset_id].get("name")
                cancel_requests.append({"name": coin_name, "oid": oid})
        
        if not cancel_requests:
            return {"status": "ok", "response": {"type": "cancel", "data": {"statuses": []}}}
            
        return trade.bulk_cancel(cancel_requests)

    def batch_modify(self, modifies: list[dict]) -> Dict[str, Any]:
        # Batch modify may have different signature in v0.18.0, 
        # for now return error to avoid breaking
        logger.warning("Batch modify not implemented for SDK v0.18.0")
        return {"status": "error", "message": "Batch modify not implemented"}

    def order_status(self, user: str, oid: Optional[int] = None, cloid: Optional[str] = None):
        info = self.info()
        try:
            # hyperliquid sdk may expose order_status
            if hasattr(info, "order_status"):
                return info.order_status(user, oid if oid is not None else cloid)
        except Exception:
            pass
        # Fallback to raw info request if available
        try:
            body = {"type": "orderStatus", "user": user}
            if oid is not None:
                body["oid"] = oid
            elif cloid is not None:
                body["oid"] = cloid
            return info._post_info(body)  # type: ignore[attr-defined]
        except Exception as e:
            logger.warning("order_status not available: {}", e)
            return None

    def cancel_all_for_coin(self, coin: str) -> Dict[str, Any]:
        addr = self.wallet_address
        if not addr:
            raise RuntimeError("wallet address required")
        asset_id = self.get_asset_id(coin)
        if asset_id is None:
            raise RuntimeError(f"Unknown asset for {coin}")
        oo = self.get_user_open_orders(addr)
        cancels = [{"a": asset_id, "o": x["oid"]} for x in oo if x.get("coin") == coin]
        if not cancels:
            return {"status": "ok", "response": {"type": "cancel", "data": {"statuses": []}}}
        return self.batch_cancel_oids(cancels)

    def get_position_for_coin(self, coin: str) -> Optional[Dict[str, Any]]:
        """Return position object for a coin, if any (None if flat)."""
        if not self.wallet_address:
            return None
        try:
            user_state = self.info().user_state(self.wallet_address)
            for ap in user_state.get("assetPositions", []):
                pos = ap.get("position", {})
                if pos.get("coin") == coin:
                    return pos
        except Exception:
            return None
        return None
    
    def cancel_all_orders(self) -> Dict[str, Any]:
        """Cancel all open orders across all coins."""
        addr = self.wallet_address
        if not addr:
            raise RuntimeError("wallet address required")
        
        try:
            oo = self.get_user_open_orders(addr)
            if not oo:
                logger.info("No open orders to cancel")
                return {"status": "ok", "response": {"type": "cancel", "data": {"statuses": []}}}
            
            # Group by asset_id for batch cancellation
            cancels = []
            for order in oo:
                coin = order.get("coin")
                oid = order.get("oid")
                if coin and oid is not None:
                    asset_id = self.get_asset_id(coin)
                    if asset_id is not None:
                        cancels.append({"a": asset_id, "o": oid})
            
            if not cancels:
                logger.info("No valid orders found to cancel")
                return {"status": "ok", "response": {"type": "cancel", "data": {"statuses": []}}}
            
            logger.info(f"Cancelling {len(cancels)} open orders")
            return self.batch_cancel_oids(cancels)
            
        except Exception as e:
            logger.error(f"Failed to cancel all orders: {e}")
            return {"status": "error", "error": str(e)}
    
    def close_all_positions(self) -> List[Dict[str, Any]]:
        """Close all open positions with market orders."""
        if not self.wallet_address:
            raise RuntimeError("wallet address required")
        
        try:
            user_state = self.info().user_state(self.wallet_address)
            positions = user_state.get('assetPositions', [])
            
            if not positions:
                logger.info("No open positions to close")
                return []
            
            close_results = []
            for position in positions:
                pos_info = position.get('position', {})
                coin = pos_info.get('coin')
                size = pos_info.get('szi', '0')
                
                if not coin or float(size) == 0:
                    continue
                
                asset_id = self.get_asset_id(coin)
                if asset_id is None:
                    logger.warning(f"Could not find asset_id for {coin}")
                    continue
                
                # Determine if we need to buy or sell to close
                position_size = float(size)
                is_buy = position_size < 0  # If short, buy to close
                close_size = abs(position_size)
                
                logger.info(f"Closing {coin} position: {size} (market {'buy' if is_buy else 'sell'})")
                
                # Place market order to close position
                try:
                    # Get current market price for the order
                    all_mids = self.get_all_mids()
                    market_px = float(all_mids.get(coin, '0'))
                    
                    if market_px <= 0:
                        logger.warning(f"Could not get market price for {coin}, skipping")
                        continue
                    
                    # Place market order (use IOC tif for immediate execution)
                    result = self.place_limit_order(
                        asset_id=asset_id,
                        is_buy=is_buy,
                        price=str(market_px * (1.01 if is_buy else 0.99)),  # Slight price cushion for fills
                        size=str(close_size),
                        tif="Ioc",  # Immediate or Cancel for market-like execution
                        reduce_only=True
                    )
                    
                    close_results.append({
                        "coin": coin,
                        "size": size,
                        "action": "buy" if is_buy else "sell",
                        "result": result
                    })
                    
                except Exception as e:
                    logger.error(f"Failed to close {coin} position: {e}")
                    close_results.append({
                        "coin": coin,
                        "size": size,
                        "action": "buy" if is_buy else "sell",
                        "error": str(e)
                    })
            
            logger.info(f"Attempted to close {len(close_results)} positions")
            return close_results
            
        except Exception as e:
            logger.error(f"Failed to close all positions: {e}")
            return [{"error": str(e)}]
