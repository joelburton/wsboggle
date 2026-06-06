"""FastAPI application object.

The minimum viable app: a lifespan that opens (and warms) the SQLite
DB on startup, plus a ``/api/health`` endpoint. Real routes
(``/api/me``, ``/api/clubs``, ``/api/auth/*``, ``/ws/clubs/:id``) get
mounted here as they're built.

When ``CLIENT_DIST`` is set, the built Vite bundle is served from this
process: ``/assets/*`` from disk, and any non-API / non-WS path falls
back to ``index.html`` so client-side routes like ``/c/:id`` deep-link.
"""

from __future__ import annotations

import sqlite3
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from wsboggle import (
    auth_routes,
    club_routes,
    club_ws,
    db,
    dict_routes,
    game_routes,
    solo_routes,
)
from wsboggle.config import settings


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Startup / shutdown hook.

    Opens the SQLite DB once on startup (which also runs the schema
    bootstrap if the DB file is new) and stashes the connection on
    ``app.state.db`` for handlers to use. Then sweeps any
    crash-orphaned games via :func:`club_ws.recover_active_games`:
    timers that already expired during downtime get marked ended;
    timers still in the future get a fresh asyncio task scheduled
    so ``gameEnded`` still fires for whoever is connected.
    """
    conn: sqlite3.Connection = db.connect()
    app.state.db = conn
    try:
        club_ws.recover_active_games(app)
        yield
    finally:
        conn.close()


app = FastAPI(title="wsboggle", lifespan=lifespan)
app.include_router(auth_routes.router)
app.include_router(club_routes.router)
app.include_router(solo_routes.router)
app.include_router(game_routes.router)
app.include_router(dict_routes.router)
app.include_router(club_ws.router)


@app.get("/api/health")
async def health() -> dict[str, str]:
    """Liveness check — used by the client to confirm the proxy is up."""
    return {"status": "ok"}


if settings.client_dist:
    _client_dist = Path(settings.client_dist).resolve()
    app.mount(
        "/assets",
        StaticFiles(directory=_client_dist / "assets"),
        name="assets",
    )

    @app.get("/{full_path:path}")
    async def spa_fallback(full_path: str) -> FileResponse:
        """Serve a file from CLIENT_DIST if it exists, else index.html.

        Registered after the API routers, so ``/api/*`` and ``/ws/*``
        match first. The path-traversal guard rejects anything that
        resolves outside ``CLIENT_DIST``.
        """
        if full_path:
            candidate = (_client_dist / full_path).resolve()
            try:
                candidate.relative_to(_client_dist)
            except ValueError:
                raise HTTPException(status_code=404)
            if candidate.is_file():
                return FileResponse(candidate)
        return FileResponse(_client_dist / "index.html")
