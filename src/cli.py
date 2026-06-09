from __future__ import annotations

import argparse
import sys
import time
from typing import Optional
import asyncio

import pandas as pd
from loguru import logger
import uvicorn

from .bot.config import get_settings
from .bot.exchange.hyperliquid_client import HyperliquidClient
from .bot.strategy.mean_reversion import MeanReversionStrategy
from .bot.strategy.momentum import MomentumStrategy
from .bot.executor import Executor
from .bot.backtest.data_loader import load_ohlcv_csv
from .bot.backtest.engine import Backtester, BacktestConfig
from .bot.backtest.metrics import summarize
from .bot.dashboard.server import create_app
from .bot.datastore.redis_store import RedisStore
from .bot.metrics.pnl import PnLTracker


def _make_strategy(settings):
    # Decide strategy from settings.STRATEGY and env params
    name = getattr(settings, "strategy", "mean_reversion")
    if name == "momentum":
        lb = int(getattr(settings, "mom_lookback", 20))
        return MomentumStrategy(lookback=lb)
    elif name == "test_aggressive":
        from .bot.strategy.test_aggressive import TestAggressiveStrategy
        return TestAggressiveStrategy()
    # mean reversion default
    fast = int(getattr(settings, "mr_fast", 10))
    slow = int(getattr(settings, "mr_slow", 50))
    thr = float(getattr(settings, "mr_threshold", 0.005))
    fee_maker_bps = float(getattr(settings, "fee_maker_bps", 1.5))
    slippage_bps = float(getattr(settings, "slippage_bps", 2.0))
    mr_dyn_mult = float(getattr(settings, "mr_dyn_mult", 2.2))
    return MeanReversionStrategy(fast_window=fast, slow_window=slow, threshold=thr,
                               fee_maker_bps=fee_maker_bps, slippage_bps=slippage_bps, mr_dyn_mult=mr_dyn_mult)


def cmd_run(mode: Optional[str] = None, source: Optional[str] = None, strategy: Optional[str] = None, twap_minutes: Optional[int] = None, twap_randomize: bool = False, reduce_only: bool = False) -> int:
    settings = get_settings()
    if mode:
        settings.bot_mode = mode  # type: ignore[attr-defined]
    if source:
        settings.marketdata_source = source  # type: ignore[attr-defined]
    if strategy:
        settings.strategy = strategy  # type: ignore[attr-defined]

    # Startup context log (no secrets)
    try:
        from urllib.parse import urlparse
        r = urlparse(settings.redis_url)
        rh = (r.hostname or "?") + (f":{r.port}" if r.port else "")
    except Exception:
        rh = "?"
    # Get list of coins to trade
    trading_coins = settings.get_trading_coins()
    logger.info(
        "Starting bot: mode={} env={} source={} coins={} strategy={} redis={}",
        settings.bot_mode,
        settings.hl_env,
        settings.marketdata_source,
        ','.join(trading_coins),
        getattr(settings, "strategy", "mean_reversion"),
        rh,
    )
    client = HyperliquidClient(env=settings.hl_env)
    
    # Clean startup: cancel all orders and close all positions if enabled
    if settings.clean_startup and settings.bot_mode == "live":
        logger.info("Clean startup enabled - cancelling all orders and closing all positions")
        try:
            # Cancel all open orders first
            cancel_result = client.cancel_all_orders()
            if cancel_result.get("status") == "ok":
                cancelled_count = len(cancel_result.get("response", {}).get("data", {}).get("statuses", []))
                if cancelled_count > 0:
                    logger.info(f"Cancelled {cancelled_count} open orders")
                else:
                    logger.info("No open orders to cancel")
            else:
                logger.warning(f"Order cancellation failed: {cancel_result}")
            
            # Close all positions
            close_results = client.close_all_positions()
            if close_results:
                successful_closes = [r for r in close_results if "error" not in r]
                failed_closes = [r for r in close_results if "error" in r]
                
                if successful_closes:
                    logger.info(f"Attempted to close {len(successful_closes)} positions:")
                    for result in successful_closes:
                        logger.info(f"  {result['coin']}: {result['action']} {result['size']}")
                
                if failed_closes:
                    logger.warning(f"Failed to close {len(failed_closes)} positions:")
                    for result in failed_closes:
                        logger.warning(f"  {result.get('coin', 'unknown')}: {result['error']}")
            else:
                logger.info("No open positions to close")
                
            # Brief pause to allow orders to settle
            import time
            time.sleep(2)
            logger.info("Clean startup completed")
            
        except Exception as e:
            logger.error(f"Clean startup failed: {e}")
            # Continue anyway - this shouldn't stop the bot from starting
    elif settings.clean_startup:
        logger.info("Clean startup enabled but skipped (not in live mode)")
    
    # Validate all coins and get their asset IDs
    coin_asset_map = {}
    for coin in trading_coins:
        asset_id = client.get_asset_id(coin)
        if asset_id is None:
            logger.error("Could not resolve asset id for coin {}", coin)
            return 1
        coin_asset_map[coin] = asset_id
        logger.info("Trading {} (asset_id: {})", coin, asset_id)

    # Ensure Redis is reachable before starting; fallback to in-memory store if not
    import os as _os
    store = RedisStore()
    wait_secs = int(_os.getenv("REDIS_WAIT_SECONDS", "120") or "120")
    if not _wait_for_redis(store, timeout_seconds=wait_secs):
        from .bot.datastore.redis_store import MemoryStore
        store = MemoryStore()
        logger.warning("Redis unreachable — running with in-memory store (no persistence)")
    pnl = PnLTracker(store)

    strat = _make_strategy(settings)
    executor = Executor(client, strat, twap_minutes=twap_minutes, twap_randomize=twap_randomize, reduce_only=reduce_only)
    # store and pnl already created above

    # Dead-man's switch: schedule cancel N seconds from now and refresh periodically
    def refresh_deadman():
        if settings.bot_mode == "live" and settings.hl_private_key and settings.hl_wallet_address:
            try:
                when = int((pd.Timestamp.utcnow().timestamp() + settings.deadman_seconds) * 1000)
                client.schedule_cancel(when)
                logger.debug("Scheduled cancel at {}", when)
            except Exception as e:
                logger.warning("Deadman schedule failed: {}", e)
        else:
            logger.debug("Skipping deadman refresh (mode={} keys={})", settings.bot_mode, bool(settings.hl_private_key and settings.hl_wallet_address))

    # Simple polling loop: fetch mids and build price series for each coin
    coin_data = {}
    for coin in trading_coins:
        coin_data[coin] = {
            'prices': [],
            'index': []
        }
    
    try:
        last_deadman = pd.Timestamp(0)
        while settings.marketdata_source != "ws":
            mids = client.get_all_mids()
            now = pd.Timestamp.utcnow()
            
            # Process each coin
            for coin in trading_coins:
                asset_id = coin_asset_map[coin]
                px_str = mids.get(coin)
                if px_str is None:
                    logger.warning("No mid for {}", coin)
                    continue
                
                px = float(px_str)
                coin_data[coin]['prices'].append(px)
                coin_data[coin]['index'].append(now)

                # Form a minimal OHLCV from mids as close; open=close
                prices = coin_data[coin]['prices']
                index = coin_data[coin]['index']
                df = pd.DataFrame({
                    "open": prices,
                    "high": prices,
                    "low": prices,
                    "close": prices,
                    "volume": [0.0] * len(prices),
                }, index=pd.DatetimeIndex(index))

                # Generate trading signals for this coin
                signal = strat.on_bar(df)
                if settings.bot_mode == "live":
                    executor.execute_signal(signal, asset_id=asset_id, coin=coin, price=px)
                else:
                    logger.info("[PAPER] {} Signal: {} meta={} px={}", coin, signal.action, signal.meta, px)
                    # In paper mode, also run through executor to simulate trade + update PnL/Redis
                    executor.execute_signal(signal, asset_id=asset_id, coin=coin, price=px)

            # Refresh deadman's switch periodically (once per cycle)
            if (now - last_deadman).total_seconds() >= max(10, settings.deadman_seconds // 2):
                refresh_deadman()
                last_deadman = now

            # Circuit breaker: check daily realized PnL (once per cycle)
            daily_pnl = pnl.realized_pnl_today()
            if settings.daily_loss_limit_usd > 0 and daily_pnl <= -abs(settings.daily_loss_limit_usd):
                logger.error("Circuit breaker tripped: daily PnL {} <= -{} USD", daily_pnl, settings.daily_loss_limit_usd)
                break

            time.sleep(2.0)
    except KeyboardInterrupt:
        logger.info("Shutting down bot.")
    return 0


async def _run_ws_loop(settings) -> None:
    from .bot.marketdata.ws import CandleStream, CandleBuffer
    from .bot.marketdata.notifications import NotificationStream

    client = HyperliquidClient(env=settings.hl_env)
    
    # Clean startup: cancel all orders and close all positions if enabled
    if settings.clean_startup and settings.bot_mode == "live":
        logger.info("Clean startup enabled - cancelling all orders and closing all positions")
        try:
            # Cancel all open orders first
            cancel_result = client.cancel_all_orders()
            if cancel_result.get("status") == "ok":
                cancelled_count = len(cancel_result.get("response", {}).get("data", {}).get("statuses", []))
                if cancelled_count > 0:
                    logger.info(f"Cancelled {cancelled_count} open orders")
                else:
                    logger.info("No open orders to cancel")
            else:
                logger.warning(f"Order cancellation failed: {cancel_result}")
            
            # Close all positions
            close_results = client.close_all_positions()
            if close_results:
                successful_closes = [r for r in close_results if "error" not in r]
                failed_closes = [r for r in close_results if "error" in r]
                
                if successful_closes:
                    logger.info(f"Attempted to close {len(successful_closes)} positions:")
                    for result in successful_closes:
                        logger.info(f"  {result['coin']}: {result['action']} {result['size']}")
                
                if failed_closes:
                    logger.warning(f"Failed to close {len(failed_closes)} positions:")
                    for result in failed_closes:
                        logger.warning(f"  {result.get('coin', 'unknown')}: {result['error']}")
            else:
                logger.info("No open positions to close")
                
            # Brief pause to allow orders to settle
            import time
            time.sleep(2)
            logger.info("Clean startup completed")
            
        except Exception as e:
            logger.error(f"Clean startup failed: {e}")
            # Continue anyway - this shouldn't stop the bot from starting
    elif settings.clean_startup:
        logger.info("Clean startup enabled but skipped (not in live mode)")
    
    # Get list of coins to trade and validate them
    trading_coins = settings.get_trading_coins()
    coin_asset_map = {}
    for coin in trading_coins:
        asset_id = client.get_asset_id(coin)
        if asset_id is None:
            logger.error("Could not resolve asset id for coin {}", coin)
            return
        coin_asset_map[coin] = asset_id
        logger.info("WebSocket mode: Trading {} (asset_id: {})", coin, asset_id)
    
    # Ensure Redis is reachable before starting; fallback to in-memory store if not
    import os as _os
    store = RedisStore()
    wait_secs = int(_os.getenv("REDIS_WAIT_SECONDS", "120") or "120")
    if not _wait_for_redis(store, timeout_seconds=wait_secs):
        from .bot.datastore.redis_store import MemoryStore
        store = MemoryStore()
        logger.warning("Redis unreachable — running with in-memory store (no persistence)")
    pnl = PnLTracker(store)

    strat = _make_strategy(settings)
    executor = Executor(
        client,
        strat,
        twap_minutes=int(getattr(settings, "twap_minutes", 0) or 0) or None,
        twap_randomize=bool(getattr(settings, "twap_randomize", False)),
        reduce_only=bool(getattr(settings, "reduce_only", False)),
    )
    
    # Create streams and buffers for each coin
    streams_and_buffers = {}
    for coin in trading_coins:
        stream = CandleStream(client, coin, settings.candle_interval)
        buf = CandleBuffer()
        streams_and_buffers[coin] = {'stream': stream, 'buffer': buf}

    last_deadman = pd.Timestamp(0, tz="UTC")

    async def consume_notifications():
        if not settings.hl_wallet_address:
            return
        notif = NotificationStream(client, settings.hl_wallet_address)
        async for msg in notif.stream():
            try:
                data = msg.get("data", {})
                # Fill messages: parse typical structure into side/px/sz/fee
                if isinstance(data, dict) and data.get("type") == "notification":
                    payload = data.get("payload", {})
                    # This is heuristic; actual schema may vary by message
                    fills = payload.get("fills") or []
                    for f in fills:
                        side = f.get("side") or ("buy" if f.get("dir") == "Long" else "sell")
                        px = float(f.get("px") or f.get("price") or 0)
                        sz = float(f.get("sz") or f.get("size") or 0)
                        fee = float(f.get("fee") or 0)
                        if px > 0 and sz > 0:
                            pnl.process_trade("buy" if side.lower().startswith("b") else "sell", px, sz, fee)
            except Exception as e:
                logger.debug("Notif parse error: {}", e)

    async def consume_candles_for_coin(coin: str):
        """Consume candles for a specific coin"""
        nonlocal last_deadman
        stream_info = streams_and_buffers[coin]
        stream = stream_info['stream']
        buf = stream_info['buffer']
        asset_id = coin_asset_map[coin]
        
        async for candle in stream.stream():
            buf.upsert(candle)
            df = buf.df
            if df.empty:
                continue
            now = df.index[-1]

            # Deadman's switch refresh (only do this once, not for every coin)
            if coin == trading_coins[0] and (now - last_deadman).total_seconds() >= max(10, settings.deadman_seconds // 2):
                if settings.bot_mode == "live" and settings.hl_private_key and settings.hl_wallet_address:
                    try:
                        when = int((pd.Timestamp.utcnow().timestamp() + settings.deadman_seconds) * 1000)
                        client.schedule_cancel(when)
                        last_deadman = now
                    except Exception as e:
                        logger.warning("Deadman schedule failed: {}", e)
                else:
                    last_deadman = now

            # Circuit breaker (check once for all coins)
            if coin == trading_coins[0]:
                daily_pnl = pnl.realized_pnl_today()
                if settings.daily_loss_limit_usd > 0 and daily_pnl <= -abs(settings.daily_loss_limit_usd):
                    logger.error("Circuit breaker tripped: daily PnL {} <= -{} USD", daily_pnl, settings.daily_loss_limit_usd)
                    return

            # Generate signals for this specific coin
            signal = strat.on_bar(df)
            px = float(df["close"].iloc[-1])
            if settings.bot_mode == "live":
                logger.info("[LIVE][WS] {} Signal: {} px={} meta={} df_len={}", coin, signal.action, px, signal.meta, len(df))
                executor.execute_signal(signal, asset_id=asset_id, coin=coin, price=px)
            else:
                logger.info("[PAPER][WS] {} Signal: {} px={} meta={} df_len={}", coin, signal.action, px, signal.meta, len(df))
                executor.execute_signal(signal, asset_id=asset_id, coin=coin, price=px)

    # Create tasks for all coins plus notifications
    tasks = [consume_candles_for_coin(coin) for coin in trading_coins]
    tasks.append(consume_notifications())
    
    await asyncio.gather(*tasks)


def cmd_backtest(strategy: Optional[str] = None) -> int:
    settings = get_settings()
    df = load_ohlcv_csv(settings.backtest_data_path)
    if strategy:
        settings.strategy = strategy  # type: ignore[attr-defined]
    strat = _make_strategy(settings)
    cfg = BacktestConfig(fee_rate=settings.fee_rate, slippage_bps=settings.slippage_bps)
    bt = Backtester(df, strat, cfg)
    rets, trades = bt.run()
    report = summarize(rets, cfg.start_equity)
    logger.info("Backtest summary: {}", report)
    out_dir = "backtests"
    import os
    os.makedirs(out_dir, exist_ok=True)
    df_summary = pd.DataFrame([report])
    df_summary.to_json(f"{out_dir}/summary.json", orient="records", indent=2)
    trades.to_csv(f"{out_dir}/trades.csv", index=False)
    logger.info("Wrote backtest results to {}", out_dir)
    return 0


def cmd_dashboard() -> int:
    settings = get_settings()
    app = create_app()
    import os
    # Prefer provider-assigned PORT if present (common on PaaS)
    port = int(os.getenv("PORT", str(settings.dashboard_port)))
    uvicorn.run(app, host=settings.dashboard_host, port=port, log_level="info")
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    # Default to 'run' if no subcommand was provided (helps container runtimes)
    if not argv:
        argv = ["run"]
    parser = argparse.ArgumentParser("hypebot2")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_run = sub.add_parser("run", help="Run the trading bot")
    p_run.add_argument("--mode", choices=["paper", "live"], default=None)
    p_run.add_argument("--source", choices=["poll", "ws"], default=None, help="Market data source")
    p_run.add_argument("--strategy", choices=["mean_reversion", "momentum"], default=None)
    p_run.add_argument("--twap-minutes", type=int, default=None, help="Place TWAP orders instead of single limit")
    p_run.add_argument("--twap-randomize", action="store_true", help="Randomize TWAP schedule")
    p_run.add_argument("--reduce-only", action="store_true", help="Send reduce-only orders when supported")

    p_bt = sub.add_parser("backtest", help="Run backtests from CSV")
    p_bt.add_argument("--strategy", choices=["mean_reversion", "momentum"], default=None)
    sub.add_parser("dashboard", help="Run the monitoring dashboard")

    args = parser.parse_args(argv)
    if args.cmd == "run":
        # Use ws loop if requested via CLI or env
        src = args.source or get_settings().marketdata_source
        if src == "ws":
            try:
                asyncio.run(_run_ws_loop(get_settings()))
            except KeyboardInterrupt:
                logger.info("Shutting down WS bot.")
            return 0
        return cmd_run(args.mode, args.source, args.strategy, args.twap_minutes, args.twap_randomize, args.reduce_only)
    if args.cmd == "backtest":
        return cmd_backtest(args.strategy)
    if args.cmd == "dashboard":
        return cmd_dashboard()
    return 1


def _wait_for_redis(store: RedisStore, timeout_seconds: int = 120) -> bool:
    start = time.time()
    delay = 1.0
    while True:
        if store.ping():
            # Avoid logging secrets in REDIS_URL; show only host:port/db, prefer actual store URL
            try:
                from urllib.parse import urlparse
                url_to_log = getattr(store, "_url", None) or get_settings().redis_url
                u = urlparse(url_to_log)
                hostport = u.hostname or "?"
                if u.port:
                    hostport += f":{u.port}"
                db = (u.path or "/").lstrip("/") or "0"
                logger.info("Connected to Redis at {} / db {}", hostport, db)
            except Exception:
                logger.info("Connected to Redis")
            return True
        elapsed = time.time() - start
        if elapsed >= timeout_seconds:
            logger.error("Failed to connect to Redis within {}s; continuing without persistence", timeout_seconds)
            return False
        logger.warning("Waiting for Redis (retry in {:.0f}s)...", delay)
        time.sleep(delay)
        delay = min(delay * 2, 10.0)


if __name__ == "__main__":
    raise SystemExit(main())
