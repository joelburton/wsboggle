"""Runtime configuration sourced from environment variables.

Kept deliberately small — a single frozen :class:`Settings` dataclass
that's populated once at import time. No layered config files, no
hot-reload; restart the process to change anything.

Env vars:

- ``HOST`` / ``PORT`` — Uvicorn bind. Default ``127.0.0.1:3001``.
- ``DB_PATH`` — SQLite path. Default ``data/wsboggle.db``.
- ``DATA_DIR`` — directory holding ``words.dat`` / ``all.sqlite3`` /
  ``libwords.so``. Default ``data``.
- ``CLIENT_DIST`` — production-only path to the built client. When
  unset, the server only serves ``/api`` and ``/ws`` (dev mode; Vite
  serves the client).
- ``WSBOGGLE_DEV`` — set to ``1`` to enable Uvicorn auto-reload.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    """All runtime knobs."""

    host: str
    port: int
    db_path: str
    data_dir: str
    client_dist: str | None
    dev: bool


def _load() -> Settings:
    """Read the environment once and produce a :class:`Settings`."""
    return Settings(
        host=os.environ.get("HOST", "127.0.0.1"),
        port=int(os.environ.get("PORT", "3001")),
        db_path=os.environ.get("DB_PATH", "data/wsboggle.db"),
        data_dir=os.environ.get("DATA_DIR", "data"),
        client_dist=os.environ.get("CLIENT_DIST"),
        dev=os.environ.get("WSBOGGLE_DEV") == "1",
    )


settings = _load()
