# hypebot2

Hyperliquid trading bot. Python, async, Redis-backed state. Includes a backtest engine and a small FastAPI dashboard.

Supports paper and live modes. Mean-reversion and momentum (Donchian breakout) strategies are bundled.

## Setup

```bash
cp .env.example .env
# fill in HL_PRIVATE_KEY, HL_WALLET_ADDRESS, HL_ENV
pip install -r requirements.txt
```

## Run

Paper mode, WS candles:

```bash
python -m src.cli run --mode paper --source ws
```

Live (start on testnet):

```bash
python -m src.cli run --mode live --source ws --strategy momentum
```

Backtest:

```bash
python -m src.cli backtest --strategy momentum
```

Dashboard at `http://localhost:8000`:

```bash
python -m src.cli dashboard
```

Or run the full stack via Docker:

```bash
docker compose up --build
```

## Notes

- Start on testnet, paper mode.
- Use a Hyperliquid API wallet, never your main private key.
- Redis is used for state; the bot falls back to an in-memory store if Redis is unreachable (a one-time warning is logged).
- Daily-loss circuit breaker (`DAILY_LOSS_LIMIT_USD`) and dead-man's switch (`DEADMAN_SECONDS`) are configurable in `.env`.
- All configuration lives in `.env` — see `.env.example` for the full set.

## Layout

```
src/
  bot/
    config.py                  pydantic settings (loads .env)
    exchange/hyperliquid_client.py
    strategy/
      base.py                  strategy protocol
      mean_reversion.py
      momentum.py
    risk.py                    sizing and risk checks
    executor.py                order execution
    datastore/redis_store.py
    backtest/                  engine, loader, metrics
    dashboard/server.py        FastAPI dashboard
  cli.py                       entrypoint: run / backtest / dashboard
docker/                        Dockerfile + healthcheck
```
