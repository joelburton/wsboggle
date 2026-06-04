"""FastAPI application object.

The minimum viable app: a lifespan that opens (and warms) the SQLite
DB on startup, plus a ``/api/health`` endpoint. Real routes
(``/api/me``, ``/api/clubs``, ``/api/auth/*``, ``/ws/clubs/:id``) get
mounted here as they're built.
"""

from __future__ import annotations

import sqlite3
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI

from wsboggle import (
    auth_routes,
    club_routes,
    club_ws,
    db,
    dict_routes,
    game_routes,
    solo_routes,
)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Startup / shutdown hook.

    Opens the SQLite DB once on startup (which also runs the schema
    bootstrap if the DB file is new) and stashes the connection on
    ``app.state.db`` for handlers to use.
    """
    conn: sqlite3.Connection = db.connect()
    app.state.db = conn
    try:
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
