from __future__ import annotations

import os
from typing import Literal, Optional

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    # dotenv is optional; env can be provided by shell or Compose
    pass

from pydantic import BaseModel, Field, ValidationError, ConfigDict


class Settings(BaseModel):
    # Hyperliquid
    hl_private_key: Optional[str] = Field(default=None, alias="HL_PRIVATE_KEY")
    hl_wallet_address: Optional[str] = Field(default=None, alias="HL_WALLET_ADDRESS")
    hl_env: Literal["mainnet", "testnet"] = Field(default="testnet", alias="HL_ENV")

    # Redis
    redis_url: str = Field(default="redis://localhost:6379/0", alias="REDIS_URL")

    # Bot
    bot_mode: Literal["paper", "live"] = Field(default="paper", alias="BOT_MODE")
    base_coin: str = Field(default="BTC", alias="BASE_COIN")  # Legacy single coin support
    base_coins: str = Field(default="BTC,ETH,SOL,HYPE", alias="BASE_COINS")  # Multi-coin support
    quote: str = Field(default="USDC", alias="QUOTE")
    max_leverage: float = Field(default=2.0, alias="MAX_LEVERAGE")
    max_position_usd: float = Field(default=200.0, alias="MAX_POSITION_USD")
    risk_per_trade: float = Field(default=0.01, alias="RISK_PER_TRADE")
    paper_equity_usd: float = Field(default=1000.0, alias="PAPER_EQUITY_USD")
    fee_rate: float = Field(default=0.0005, alias="FEE_RATE")
    slippage_bps: float = Field(default=2, alias="SLIPPAGE_BPS")

    # Logging
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")

    # Backtesting
    backtest_data_path: str = Field(default="data/BTCUSDC_1h.csv", alias="BACKTEST_DATA_PATH")

    # Dashboard
    dashboard_host: str = Field(default="0.0.0.0", alias="DASHBOARD_HOST")
    dashboard_port: int = Field(default=8000, alias="DASHBOARD_PORT")

    # Circuit breaker
    daily_loss_limit_usd: float = Field(default=50.0, alias="DAILY_LOSS_LIMIT_USD")
    daily_loss_limit_pct: float = Field(default=0.0, alias="DAILY_LOSS_LIMIT_PCT")

    # Clean startup (close all orders/positions on start)
    clean_startup: bool = Field(default=True, alias="CLEAN_STARTUP")

    # Dead man's switch
    deadman_seconds: int = Field(default=60, alias="DEADMAN_SECONDS")

    # Market data
    marketdata_source: Literal["poll", "ws"] = Field(default="ws", alias="MARKETDATA_SOURCE")
    candle_interval: str = Field(default="1m", alias="CANDLE_INTERVAL")

    # Strategy selection and params
    strategy: Literal["mean_reversion", "momentum", "test_aggressive"] = Field(default="mean_reversion", alias="STRATEGY")
    mr_fast: int = Field(default=10, alias="MR_FAST")
    mr_slow: int = Field(default=50, alias="MR_SLOW")
    mr_threshold: float = Field(default=0.0075, alias="MR_THRESHOLD")
    fee_maker_bps: float = Field(default=1.5, alias="FEE_MAKER_BPS")
    mr_dyn_mult: float = Field(default=3.0, alias="MR_DYN_MULT")
    mom_lookback: int = Field(default=20, alias="MOM_LOOKBACK")
    atr_period: int = Field(default=14, alias="ATR_PERIOD")
    min_atr_pct: float = Field(default=0.002, alias="MIN_ATR_PCT")

    # Optional order execution features
    twap_minutes: int | None = Field(default=None, alias="TWAP_MINUTES")
    twap_randomize: bool = Field(default=False, alias="TWAP_RANDOMIZE")
    reduce_only: bool = Field(default=False, alias="REDUCE_ONLY")
    aggressive_bps: int = Field(default=0, alias="AGGRESSIVE_BPS")
    maker_only: bool = Field(default=True, alias="MAKER_ONLY")
    min_cooldown_sec: int = Field(default=120, alias="MIN_COOLDOWN_SEC")
    max_open_orders_per_coin: int = Field(default=1, alias="MAX_OPEN_ORDERS_PER_COIN")
    allow_same_side_add: bool = Field(default=False, alias="ALLOW_SAME_SIDE_ADD")
    
    # Cadence / batching
    bbo_refresh_sec: float = Field(default=1.2, alias="BBO_REFRESH_SEC")
    max_actions_per_batch: int = Field(default=36, alias="MAX_ACTIONS_PER_BATCH")
    batch_interval_ms: int = Field(default=100, alias="BATCH_INTERVAL_MS")
    expires_after_sec: int = Field(default=2, alias="EXPIRES_AFTER_SEC")
    
    # WS discipline
    ws_max_inflight: int = Field(default=80, alias="WS_MAX_INFLIGHT")
    ws_max_msgs_per_min: int = Field(default=1500, alias="WS_MAX_MSGS_PER_MIN")
    
    # Info polling
    info_max_calls_per_sec: int = Field(default=1, alias="INFO_MAX_CALLS_PER_SEC")

    model_config = ConfigDict(
        populate_by_name=True,
        env_file='.env',
        env_file_encoding='utf-8',
        case_sensitive=False,
        extra='ignore'
    )
    
    def get_trading_coins(self) -> list[str]:
        """Get list of coins to trade. Uses BASE_COINS if set, otherwise falls back to BASE_COIN."""
        if self.base_coins and self.base_coins.strip():
            return [coin.strip().upper() for coin in self.base_coins.split(',') if coin.strip()]
        return [self.base_coin.upper()]


def get_settings() -> Settings:
    try:
        # Helper function to convert empty strings to None for optional fields
        def env_or_none(key: str, default: str | None = None) -> str | None:
            val = os.environ.get(key, default)
            return None if val == "" else val
        
        # Explicitly read environment variables for Pydantic v2 compatibility
        env_data = {
            "HL_PRIVATE_KEY": env_or_none("HL_PRIVATE_KEY"),
            "HL_WALLET_ADDRESS": env_or_none("HL_WALLET_ADDRESS"),
            "HL_ENV": os.environ.get("HL_ENV", "testnet"),
            "REDIS_URL": os.environ.get("REDIS_URL", "redis://localhost:6379/0"),
            "BOT_MODE": os.environ.get("BOT_MODE", "paper"),
            "BASE_COIN": os.environ.get("BASE_COIN", "BTC"),
            "BASE_COINS": os.environ.get("BASE_COINS", "BTC,ETH,SOL,HYPE"),
            "QUOTE": os.environ.get("QUOTE", "USDC"),
            "MAX_LEVERAGE": os.environ.get("MAX_LEVERAGE", "2.0"),
            "MAX_POSITION_USD": os.environ.get("MAX_POSITION_USD", "200.0"),
            "RISK_PER_TRADE": os.environ.get("RISK_PER_TRADE", "0.01"),
            "PAPER_EQUITY_USD": os.environ.get("PAPER_EQUITY_USD", "1000.0"),
            "FEE_RATE": os.environ.get("FEE_RATE", "0.0005"),
            "SLIPPAGE_BPS": os.environ.get("SLIPPAGE_BPS", "2"),
            "LOG_LEVEL": os.environ.get("LOG_LEVEL", "INFO"),
            "BACKTEST_DATA_PATH": os.environ.get("BACKTEST_DATA_PATH", "data/BTCUSDC_1h.csv"),
            "DASHBOARD_HOST": os.environ.get("DASHBOARD_HOST", "0.0.0.0"),
            "DASHBOARD_PORT": os.environ.get("DASHBOARD_PORT", "8000"),
            "DAILY_LOSS_LIMIT_USD": os.environ.get("DAILY_LOSS_LIMIT_USD", "50.0"),
            "DAILY_LOSS_LIMIT_PCT": os.environ.get("DAILY_LOSS_LIMIT_PCT", "0.0"),
            "DEADMAN_SECONDS": os.environ.get("DEADMAN_SECONDS", "60"),
            "MARKETDATA_SOURCE": os.environ.get("MARKETDATA_SOURCE", "ws"),
            "CANDLE_INTERVAL": os.environ.get("CANDLE_INTERVAL", "1m"),
            "STRATEGY": os.environ.get("STRATEGY", "mean_reversion"),
            "MR_FAST": os.environ.get("MR_FAST", "10"),
            "MR_SLOW": os.environ.get("MR_SLOW", "50"),
            "MR_THRESHOLD": os.environ.get("MR_THRESHOLD", "0.0075"),
            "FEE_MAKER_BPS": os.environ.get("FEE_MAKER_BPS", "1.5"),
            "MR_DYN_MULT": os.environ.get("MR_DYN_MULT", "3.0"),
            "MOM_LOOKBACK": os.environ.get("MOM_LOOKBACK", "20"),
            "ATR_PERIOD": os.environ.get("ATR_PERIOD", "14"),
            "MIN_ATR_PCT": os.environ.get("MIN_ATR_PCT", "0.002"),
            "TWAP_MINUTES": env_or_none("TWAP_MINUTES"),
            "TWAP_RANDOMIZE": os.environ.get("TWAP_RANDOMIZE", "False"),
            "REDUCE_ONLY": os.environ.get("REDUCE_ONLY", "False"),
            "AGGRESSIVE_BPS": os.environ.get("AGGRESSIVE_BPS", "0"),
            "MAKER_ONLY": os.environ.get("MAKER_ONLY", "True"),
            "MIN_COOLDOWN_SEC": os.environ.get("MIN_COOLDOWN_SEC", "120"),
            "MAX_OPEN_ORDERS_PER_COIN": os.environ.get("MAX_OPEN_ORDERS_PER_COIN", "1"),
            "ALLOW_SAME_SIDE_ADD": os.environ.get("ALLOW_SAME_SIDE_ADD", "False"),
            "BBO_REFRESH_SEC": os.environ.get("BBO_REFRESH_SEC", "1.2"),
            "MAX_ACTIONS_PER_BATCH": os.environ.get("MAX_ACTIONS_PER_BATCH", "36"),
            "BATCH_INTERVAL_MS": os.environ.get("BATCH_INTERVAL_MS", "100"),
            "EXPIRES_AFTER_SEC": os.environ.get("EXPIRES_AFTER_SEC", "2"),
            "WS_MAX_INFLIGHT": os.environ.get("WS_MAX_INFLIGHT", "80"),
            "WS_MAX_MSGS_PER_MIN": os.environ.get("WS_MAX_MSGS_PER_MIN", "1500"),
            "INFO_MAX_CALLS_PER_SEC": os.environ.get("INFO_MAX_CALLS_PER_SEC", "1"),
        }
        return Settings.model_validate(env_data)
    except ValidationError as e:
        raise RuntimeError(f"Invalid configuration: {e}")
