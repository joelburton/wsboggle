"""Tests for the four new read endpoints:

- ``GET /api/games/:id`` (game view — snapshot + optional result)
- ``GET /api/solo/games`` (solo history)
- ``GET /api/clubs/:id/games`` (club history)
- ``GET /api/define`` (word definitions)

The first three exercise libwords for board generation; the test
module is skipped when ``data/libwords.so`` / ``words.dat`` are
missing (same pattern as test_libwords.py).
"""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("WSBOGGLE_DEV", "1")

from wsboggle.config import settings  # noqa: E402


_DATA = Path(settings.data_dir)
needs_libwords = pytest.mark.skipif(
    not ((_DATA / "libwords.so").exists() and (_DATA / "words.dat").exists()),
    reason=f"libwords.so / words.dat missing under {_DATA}",
)
needs_dictionary = pytest.mark.skipif(
    not (_DATA / "all.sqlite3").exists(),
    reason=f"all.sqlite3 missing under {_DATA}",
)


# --- Shared helpers --------------------------------------------------------


def _seed_invite(db: sqlite3.Connection, code: str = "friends-2026") -> None:
    db.execute(
        "INSERT INTO invite_codes (code, label, created_at) VALUES (?, ?, ?)",
        (code, "test", datetime.now(UTC).isoformat()),
    )


def _register(client: TestClient, handle: str = "joel") -> dict:
    resp = client.post(
        "/api/auth/register",
        json={"handle": handle, "password": "hunter2!", "invite_code": "friends-2026"},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def _logout(client: TestClient) -> None:
    client.post("/api/auth/logout")
    client.cookies.clear()


def _start_solo(client: TestClient, timer_seconds: int | None = 60) -> dict:
    resp = client.post(
        "/api/solo/games",
        json={"config": {"dice_set": "4", "timer_seconds": timer_seconds}},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def _legal_word(db: sqlite3.Connection, game_id: int) -> str:
    """Pick any legal word for a game (for test convenience)."""
    row = db.execute(
        "SELECT legal_words FROM games WHERE id = ?", (game_id,)
    ).fetchone()
    return json.loads(row["legal_words"])[0]


# --- GET /api/games/:id ---------------------------------------------------


@needs_libwords
def test_game_view_active(client: TestClient, db: sqlite3.Connection) -> None:
    """An active solo game returns snapshot, result=null."""
    _seed_invite(db)
    _register(client)
    snap = _start_solo(client)

    resp = client.get(f"/api/games/{snap['game_id']}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["snapshot"]["game_id"] == snap["game_id"]
    assert body["snapshot"]["ended_at"] is None
    assert body["result"] is None


@needs_libwords
def test_game_view_after_end(client: TestClient, db: sqlite3.Connection) -> None:
    """An ended game returns both snapshot and result; snapshot's ended_at
    matches result's ended_at."""
    _seed_invite(db)
    _register(client)
    snap = _start_solo(client)
    client.post(f"/api/solo/games/{snap['game_id']}/end")

    resp = client.get(f"/api/games/{snap['game_id']}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["snapshot"]["ended_at"] is not None
    assert body["result"] is not None
    assert body["result"]["ended_at"] == body["snapshot"]["ended_at"]
    assert len(body["result"]["players"]) == 1


@needs_libwords
def test_game_view_cross_user_404(client: TestClient, db: sqlite3.Connection) -> None:
    """Trying to view someone else's solo game = 404."""
    _seed_invite(db)
    _register(client, handle="moth")
    snap = _start_solo(client)
    _logout(client)
    _register(client, handle="joel")

    resp = client.get(f"/api/games/{snap['game_id']}")
    assert resp.status_code == 404


def test_game_view_unauth(client: TestClient) -> None:
    """Unauthenticated GET = 401."""
    resp = client.get("/api/games/1")
    assert resp.status_code == 401


def test_game_view_missing_404(client: TestClient, db: sqlite3.Connection) -> None:
    """Nonexistent game id = 404."""
    _seed_invite(db)
    _register(client)
    resp = client.get("/api/games/9999")
    assert resp.status_code == 404


@needs_libwords
def test_game_view_your_guesses_personalised(
    client: TestClient, db: sqlite3.Connection
) -> None:
    """The snapshot's your_guesses reflects the *viewing* user. For a
    solo game with one guess, the creator sees their own list."""
    _seed_invite(db)
    _register(client)
    snap = _start_solo(client)
    word = _legal_word(db, snap["game_id"])
    client.post(f"/api/solo/games/{snap['game_id']}/guess", json={"word": word})

    resp = client.get(f"/api/games/{snap['game_id']}")
    your_guesses = resp.json()["snapshot"]["your_guesses"]
    assert len(your_guesses) == 1
    assert your_guesses[0]["word"] == word.lower()
    assert your_guesses[0]["is_legal"] is True


# --- GET /api/solo/games -------------------------------------------------


@needs_libwords
def test_solo_history_empty(client: TestClient, db: sqlite3.Connection) -> None:
    """A fresh user's solo history is an empty list."""
    _seed_invite(db)
    _register(client)
    resp = client.get("/api/solo/games")
    assert resp.status_code == 200
    assert resp.json() == []


@needs_libwords
def test_solo_history_lists_games(
    client: TestClient, db: sqlite3.Connection
) -> None:
    """Listing shows your games sorted by started_at desc, with score
    and word count reflecting your legal guesses."""
    _seed_invite(db)
    _register(client)
    s1 = _start_solo(client)
    s2 = _start_solo(client)
    # Add a legal guess to s1.
    word = _legal_word(db, s1["game_id"])
    client.post(f"/api/solo/games/{s1['game_id']}/guess", json={"word": word})

    resp = client.get("/api/solo/games")
    body = resp.json()
    assert [g["game_id"] for g in body] == [s2["game_id"], s1["game_id"]]
    by_id = {g["game_id"]: g for g in body}
    assert by_id[s1["game_id"]]["your_word_count"] == 1
    assert by_id[s1["game_id"]]["your_score"] > 0
    assert by_id[s2["game_id"]]["your_word_count"] == 0
    assert by_id[s2["game_id"]]["your_score"] == 0


@needs_libwords
def test_solo_history_isolated_per_user(
    client: TestClient, db: sqlite3.Connection
) -> None:
    """Each user only sees their own solo games."""
    _seed_invite(db)
    _register(client, handle="moth")
    _start_solo(client)
    _logout(client)
    _register(client, handle="joel")

    resp = client.get("/api/solo/games")
    assert resp.json() == []


def test_solo_history_unauth(client: TestClient) -> None:
    resp = client.get("/api/solo/games")
    assert resp.status_code == 401


# --- GET /api/clubs/:id/games --------------------------------------------


def _make_club(
    client: TestClient, db: sqlite3.Connection, other_handles: list[str]
) -> dict:
    """Register the others, register joel last (stays signed in), make a club."""
    _seed_invite(db)
    for h in other_handles:
        _register(client, h)
        _logout(client)
    _register(client, "joel")
    resp = client.post(
        "/api/clubs",
        json={"name": "test-club", "member_handles": other_handles},
    )
    assert resp.status_code == 200
    return resp.json()


def _insert_synth_game(
    db: sqlite3.Connection,
    *,
    club_id: int,
    creator_id: int,
    legal_words: list[str],
    ended: bool = True,
    dice_set: str = "4",
    started_at: str | None = None,
) -> int:
    """Insert a fake completed game row directly.

    ``started_at`` lets tests inject specific ISO timestamps so we
    can verify ordering across multiple games without relying on
    insertion order or sleeping.
    """
    config = {
        "dice_set": dice_set,
        "scoring_ladder": "basic",
        "min_legal_length": 3,
        "mode": "competitive",
        "dupes_cancel": True,
        "timer_seconds": 60,
        "timer_direction": "down",
        "min_words": None,
        "max_words": None,
        "min_score": None,
        "max_score": None,
        "min_longest": None,
        "max_longest": None,
    }
    ts = started_at or datetime.now(UTC).isoformat()
    cur = db.execute(
        """
        INSERT INTO games
            (club_id, created_by, config, board, legal_words, started_at, ended_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            club_id,
            creator_id,
            json.dumps(config),
            "A" * 16,
            json.dumps(legal_words),
            ts,
            ts if ended else None,
        ),
    )
    assert cur.lastrowid is not None
    return cur.lastrowid


def test_club_history_empty(client: TestClient, db: sqlite3.Connection) -> None:
    """A brand-new club has no game history."""
    club = _make_club(client, db, ["moth"])
    resp = client.get(f"/api/clubs/{club['id']}/games")
    assert resp.status_code == 200
    assert resp.json() == []


def test_club_history_member_only(
    client: TestClient, db: sqlite3.Connection
) -> None:
    """Non-members get 404."""
    club = _make_club(client, db, ["moth"])
    _logout(client)
    # Register a stranger; they're not in any club.
    _register(client, "stranger")
    resp = client.get(f"/api/clubs/{club['id']}/games")
    assert resp.status_code == 404


def test_club_history_excludes_active(
    client: TestClient, db: sqlite3.Connection
) -> None:
    """Listing excludes games whose ended_at is null."""
    club = _make_club(client, db, ["moth"])
    joel_id = client.get("/api/me").json()["user"]["id"]
    _insert_synth_game(db, club_id=club["id"], creator_id=joel_id, legal_words=["cat"], ended=False)
    _insert_synth_game(db, club_id=club["id"], creator_id=joel_id, legal_words=["cat"], ended=True)

    resp = client.get(f"/api/clubs/{club['id']}/games")
    assert resp.status_code == 200
    assert len(resp.json()) == 1


def test_club_history_includes_member_scores(
    client: TestClient, db: sqlite3.Connection
) -> None:
    """All club members appear in the players list, even zero-scorers."""
    club = _make_club(client, db, ["moth"])
    joel_id = client.get("/api/me").json()["user"]["id"]
    moth_id = db.execute("SELECT id FROM users WHERE handle_lower = 'moth'").fetchone()["id"]
    game_id = _insert_synth_game(
        db, club_id=club["id"], creator_id=joel_id, legal_words=["cat", "rat"]
    )
    # joel finds 'cat', moth finds nothing.
    db.execute(
        "INSERT INTO guesses (game_id, user_id, word, is_legal, ts) VALUES (?, ?, ?, 1, ?)",
        (game_id, joel_id, "cat", datetime.now(UTC).isoformat()),
    )

    resp = client.get(f"/api/clubs/{club['id']}/games")
    body = resp.json()
    assert len(body) == 1
    players = {p["handle"]: p["final_total"] for p in body[0]["players"]}
    assert players == {"joel": 1, "moth": 0}
    # Sorted by score desc.
    assert [p["handle"] for p in body[0]["players"]] == ["joel", "moth"]
    # Silence unused.
    assert moth_id  # noqa


def test_club_history_orders_most_recent_first(
    client: TestClient, db: sqlite3.Connection
) -> None:
    """Multiple ended games come back sorted by started_at desc."""
    club = _make_club(client, db, ["moth"])
    joel_id = client.get("/api/me").json()["user"]["id"]

    # Three games at distinct timestamps, intentionally inserted in
    # non-chronological order so the response order can't accidentally
    # be "insertion order."
    base = datetime(2026, 1, 1, tzinfo=UTC)
    middle = (base + timedelta(days=1)).isoformat()
    newest = (base + timedelta(days=2)).isoformat()
    oldest = base.isoformat()
    g_middle = _insert_synth_game(
        db, club_id=club["id"], creator_id=joel_id, legal_words=["cat"],
        started_at=middle,
    )
    g_oldest = _insert_synth_game(
        db, club_id=club["id"], creator_id=joel_id, legal_words=["cat"],
        started_at=oldest,
    )
    g_newest = _insert_synth_game(
        db, club_id=club["id"], creator_id=joel_id, legal_words=["cat"],
        started_at=newest,
    )

    resp = client.get(f"/api/clubs/{club['id']}/games")
    assert resp.status_code == 200
    ids = [g["game_id"] for g in resp.json()]
    assert ids == [g_newest, g_middle, g_oldest]


# --- GET /api/define ------------------------------------------------------


@needs_dictionary
def test_define_known_word(client: TestClient, db: sqlite3.Connection) -> None:
    """A common word returns a non-null definition string."""
    _seed_invite(db)
    _register(client)
    resp = client.get("/api/define", params={"word": "cat"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["word"] == "cat"
    assert body["definition"] is not None
    assert len(body["definition"]) > 0


def test_define_unknown_word(client: TestClient, db: sqlite3.Connection) -> None:
    """Bogus word = 200 with definition: null (no error)."""
    _seed_invite(db)
    _register(client)
    resp = client.get("/api/define", params={"word": "zzzqqqxx"})
    assert resp.status_code == 200
    assert resp.json()["definition"] is None


def test_define_non_alpha_input(
    client: TestClient, db: sqlite3.Connection
) -> None:
    """Digits / punctuation = definition: null, no error."""
    _seed_invite(db)
    _register(client)
    resp = client.get("/api/define", params={"word": "abc123"})
    assert resp.status_code == 200
    assert resp.json()["definition"] is None


def test_define_unauth(client: TestClient) -> None:
    resp = client.get("/api/define", params={"word": "cat"})
    assert resp.status_code == 401
