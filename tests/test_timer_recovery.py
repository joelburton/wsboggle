"""Tests for the startup timer-recovery sweep.

These exercise :func:`wsboggle.club_ws.recover_active_games` directly
rather than restarting the FastAPI app between cases. The function
runs from the lifespan startup hook on every boot; we just call it
again with the DB pre-staged to whatever scenario we're checking.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from wsboggle import club_ws
from wsboggle.app import app
from wsboggle.shared import GameConfig


# --- Helpers -------------------------------------------------------------


def _seed_club(db: sqlite3.Connection) -> tuple[int, int]:
    """Insert two users + a club and return ``(club_id, joel_id)``.

    No invite code or auth path needed — we bypass the route layer
    and write rows directly. The autoincrement keeps growing
    across tests (DELETE doesn't reset it), so we capture the
    user id rather than hard-coding ``1``.
    """
    now = datetime.now(UTC).isoformat()
    cur = db.execute(
        "INSERT INTO users (handle, handle_lower, password_hash, created_at) "
        "VALUES ('joel', 'joel', 'x', ?)",
        (now,),
    )
    joel_id = cur.lastrowid
    assert joel_id is not None
    cur = db.execute(
        "INSERT INTO users (handle, handle_lower, password_hash, created_at) "
        "VALUES ('moth', 'moth', 'x', ?)",
        (now,),
    )
    moth_id = cur.lastrowid
    assert moth_id is not None
    cur = db.execute(
        "INSERT INTO clubs (name, created_by, created_at) "
        "VALUES ('joel + moth', ?, ?)",
        (joel_id, now),
    )
    club_id = cur.lastrowid
    assert club_id is not None
    db.execute(
        "INSERT INTO clubs_users (club_id, user_id, joined_at) VALUES (?, ?, ?)",
        (club_id, joel_id, now),
    )
    db.execute(
        "INSERT INTO clubs_users (club_id, user_id, joined_at) VALUES (?, ?, ?)",
        (club_id, moth_id, now),
    )
    return club_id, joel_id


def _insert_game(
    db: sqlite3.Connection,
    *,
    club_id: int | None,
    created_by: int,
    ends_at: datetime | None,
    ended_at: datetime | None = None,
) -> int:
    """Insert a games row directly so the test can pick its
    ``ends_at`` precisely. Board / legal_words are minimal valid
    JSON; the recovery sweep only cares about the timer columns."""
    started = datetime.now(UTC) - timedelta(hours=1)
    cur = db.execute(
        """
        INSERT INTO games
            (club_id, created_by, config, board, legal_words,
             started_at, ends_at, ended_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            club_id,
            created_by,
            GameConfig().model_dump_json(),
            "A" * 16,
            json.dumps([]),
            started.isoformat(),
            None if ends_at is None else ends_at.isoformat(),
            None if ended_at is None else ended_at.isoformat(),
        ),
    )
    assert cur.lastrowid is not None
    return cur.lastrowid


@pytest.fixture(autouse=True)
def _cleanup_timers() -> None:
    """Make sure no scheduled tasks survive across recovery tests.

    The recovery sweep populates ``_timers`` with real asyncio tasks
    that would otherwise outlive the test and (eventually) reference
    deleted DB rows."""
    yield
    for task in list(club_ws._timers.values()):
        task.cancel()
    club_ws._timers.clear()


# --- Tests ---------------------------------------------------------------


def test_past_due_game_is_swept_ended(
    db: sqlite3.Connection,
) -> None:
    """A game whose ``ends_at`` is in the past gets marked ended
    in the DB. No timer task is scheduled (nothing to wait for)."""
    club_id, joel_id = _seed_club(db)
    past = datetime.now(UTC) - timedelta(minutes=10)
    game_id = _insert_game(
        db, club_id=club_id, created_by=joel_id, ends_at=past
    )

    club_ws.recover_active_games(app)

    row = db.execute(
        "SELECT ended_at FROM games WHERE id = ?", (game_id,)
    ).fetchone()
    assert row["ended_at"] is not None
    assert club_id not in club_ws._timers


def test_future_timer_is_rescheduled(
    db: sqlite3.Connection,
) -> None:
    """A game whose ``ends_at`` is in the future gets a fresh
    asyncio task scheduled; the DB row stays unended.

    Assertions live *inside* the loop because ``asyncio.run``
    cancels any still-pending task as it tears down, which would
    fire ``_run_timer``'s finally block and pop the entry from
    ``_timers`` before the test could observe it.
    """
    club_id, joel_id = _seed_club(db)
    future = datetime.now(UTC) + timedelta(hours=1)
    game_id = _insert_game(
        db, club_id=club_id, created_by=joel_id, ends_at=future
    )

    async def go() -> None:
        club_ws.recover_active_games(app)
        assert club_id in club_ws._timers
        task = club_ws._timers[club_id]
        assert not task.done()
        # Cancel + await so this test doesn't leave a pending task
        # tracked by the loop's shutdown finalizers.
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(go())

    row = db.execute(
        "SELECT ended_at FROM games WHERE id = ?", (game_id,)
    ).fetchone()
    assert row["ended_at"] is None


def test_untimed_game_is_left_alone(db: sqlite3.Connection) -> None:
    """An untimed game (``ends_at IS NULL``) stays open through a
    recovery sweep. Manual endGame is the only end path."""
    club_id, joel_id = _seed_club(db)
    game_id = _insert_game(
        db, club_id=club_id, created_by=joel_id, ends_at=None
    )

    club_ws.recover_active_games(app)

    row = db.execute(
        "SELECT ended_at FROM games WHERE id = ?", (game_id,)
    ).fetchone()
    assert row["ended_at"] is None
    assert club_id not in club_ws._timers


def test_solo_past_due_game_is_swept(db: sqlite3.Connection) -> None:
    """Solo games (``club_id IS NULL``) past their timer also get
    ended in the DB — leaving them stuck has no upside — but no
    timer task is scheduled, since there's no broadcast surface."""
    _club_id, joel_id = _seed_club(db)
    past = datetime.now(UTC) - timedelta(minutes=10)
    game_id = _insert_game(
        db, club_id=None, created_by=joel_id, ends_at=past
    )

    club_ws.recover_active_games(app)

    row = db.execute(
        "SELECT ended_at FROM games WHERE id = ?", (game_id,)
    ).fetchone()
    assert row["ended_at"] is not None
    assert None not in club_ws._timers
