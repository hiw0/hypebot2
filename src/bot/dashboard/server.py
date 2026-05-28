from __future__ import annotations

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse

from ..config import get_settings
from ..datastore.redis_store import RedisStore
from ..metrics.pnl import PnLTracker


def create_app() -> FastAPI:
    settings = get_settings()
    store = RedisStore(settings.redis_url)
    app = FastAPI(title="HypeBot Dashboard")
    pnl = PnLTracker(store)

    @app.get("/health")
    def health():
        ok = store.ping()
        return {"status": "ok" if ok else "down"}

    @app.get("/metrics")
    def metrics():
        return store.get_metrics()

    @app.get("/")
    def index() -> HTMLResponse:
        m = store.get_metrics()
        trades = store.get_trades(50)
        p = store.get_pnl()
        html = f"""
        <html>
        <head><title>HypeBot</title></head>
        <body>
            <h2>HypeBot Dashboard</h2>
            <p>Status: {'OK' if store.ping() else 'DOWN'}</p>
            <h3>PNL</h3>
            <pre>{p}</pre>
            <h3>Metrics</h3>
            <pre>{m}</pre>
            <h3>Recent Trades</h3>
            <table border="1" cellpadding="4" cellspacing="0">
                <tr><th>Mode</th><th>Side</th><th>Px</th><th>Qty</th><th>Fee</th></tr>
                {''.join([f"<tr><td>{t.get('mode')}</td><td>{t.get('side')}</td><td>{t.get('px')}</td><td>{t.get('qty')}</td><td>{t.get('fee')}</td></tr>" for t in trades])}
            </table>
        </body>
        </html>
        """
        return HTMLResponse(html)

    @app.get("/pnl")
    def pnl_endpoint():
        return store.get_pnl()

    @app.get("/trades")
    def trades_endpoint(limit: int = 100):
        return store.get_trades(limit)

    return app
