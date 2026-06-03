"""Shared pytest setup.

Two things happen here that need to occur **before** any
``wsboggle`` module is imported:

1. ``WSBOGGLE_DEV=1`` is set so session cookies don't get ``Secure``,
   which would otherwise stop ``TestClient`` from echoing them back
   over plain HTTP.
2. ``DB_PATH`` is pointed at a fresh tempfile per test session so
   tests can't see each other's data and don't touch the dev DB.

Fixtures below give individual tests a clean :class:`TestClient` and
direct access to the underlying sqlite connection.
"""

from __future__ import annotations

import os
import tempfile
from collections.abc import Iterator

# These two env vars must be set before importing wsboggle.config.
os.environ.setdefault("WSBOGGLE_DEV", "1")
_tmp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmp_db.close()
os.environ["DB_PATH"] = _tmp_db.name

import sqlite3  # noqa: E402

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from wsboggle.app import app  # noqa: E402


@pytest.fixture
def client() -> Iterator[TestClient]:
    """A FastAPI ``TestClient`` with the app's lifespan run.

    The lifespan opens the DB once and stashes the connection on
    ``app.state.db``; the connection lives for the duration of the
    test.
    """
    with TestClient(app) as c:
        yield c


@pytest.fixture
def db(client: TestClient) -> sqlite3.Connection:
    """The live DB connection the app is using. Hand-managed inserts
    (e.g. seeding an invite code before a register call) go through
    this."""
    conn: sqlite3.Connection = app.state.db
    return conn


@pytest.fixture(autouse=True)
def _clean_tables(db: sqlite3.Connection) -> None:
    """Wipe game-state tables before each test so they're isolated.

    We keep the schema; just clear the rows. Ordered by FK
    dependency so cascades aren't needed.
    """
    for table in (
        "guesses",
        "chat_messages",
        "games",
        "clubs_users",
        "clubs",
        "sessions",
        "users",
        "invite_codes",
    ):
        db.execute(f"DELETE FROM {table}")
