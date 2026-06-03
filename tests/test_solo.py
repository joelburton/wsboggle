"""Solo HTTP routes — start, guess, end.

End-to-end coverage of ``/api/solo/games`` against a real libwords
solver. Skipped when ``data/libwords.so`` / ``words.dat`` aren't
present (same pattern as test_libwords.py / test_games.py).
"""

from __future__ import annotations

import os
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("WSBOGGLE_DEV", "1")

from wsboggle.config import settings  # noqa: E402


_DATA = Path(settings.data_dir)
pytestmark = pytest.mark.skipif(
    not ((_DATA / "libwords.so").exists() and (_DATA / "words.dat").exists()),
    reason=f"libwords.so / words.dat missing under {_DATA}",
)


# --- Helpers ---------------------------------------------------------------


def _seed_invite(db: sqlite3.Connection, code: str = "friends-2026") -> None:
    db.execute(
        "INSERT INTO invite_codes (code, label, created_at) VALUES (?, ?, ?)",
        (code, "test", datetime.now(UTC).isoformat()),
    )


def _register(client: TestClient, handle: str = "joel") -> dict:
    """Register and log a user in. Caller has already seeded the invite."""
    resp = client.post(
        "/api/auth/register",
        json={"handle": handle, "password": "hunter2!", "invite_code": "friends-2026"},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def _start_solo(client: TestClient, **config_overrides: object) -> dict:
    """Start a solo game; returns the GameSnapshot dict."""
    config = {"dice_set": "4", "timer_seconds": 180}
    config.update(config_overrides)
    resp = client.post("/api/solo/games", json={"config": config})
    assert resp.status_code == 200, resp.text
    return resp.json()


# --- Start ------------------------------------------------------------------


def test_start_solo_returns_snapshot(client: TestClient, db: sqlite3.Connection) -> None:
    """A successful start returns a board + ends_at + empty guesses."""
    _seed_invite(db)
    _register(client)
    snap = _start_solo(client)

    assert isinstance(snap["game_id"], int)
    assert len(snap["board"]) == 4
    assert len(snap["board"][0]) == 4
    assert snap["ends_at"] is not None
    assert snap["ended_at"] is None
    assert snap["your_guesses"] == []


def test_start_solo_requires_auth(client: TestClient) -> None:
    """No session = 401."""
    resp = client.post(
        "/api/solo/games", json={"config": {"dice_set": "4", "timer_seconds": 60}}
    )
    assert resp.status_code == 401


def test_start_solo_rejects_unknown_dice_set(
    client: TestClient, db: sqlite3.Connection
) -> None:
    """Unknown dice_set gets a 400 with a descriptive message."""
    _seed_invite(db)
    _register(client)
    resp = client.post(
        "/api/solo/games",
        json={"config": {"dice_set": "nonexistent", "timer_seconds": 60}},
    )
    assert resp.status_code == 400
    assert "dice_set" in resp.json()["detail"]


def test_start_solo_supports_untimed(
    client: TestClient, db: sqlite3.Connection
) -> None:
    """timer_seconds=null means ends_at=null."""
    _seed_invite(db)
    _register(client)
    snap = _start_solo(client, timer_seconds=None)
    assert snap["ends_at"] is None


# --- Guess -----------------------------------------------------------------


def test_guess_legal_word(client: TestClient, db: sqlite3.Connection) -> None:
    """Picking a real word from the board's legal list comes back accepted."""
    _seed_invite(db)
    _register(client)
    snap = _start_solo(client)

    # Read the game's legal_words back from the DB to pick a real one.
    import json
    row = db.execute(
        "SELECT legal_words FROM games WHERE id = ?", (snap["game_id"],)
    ).fetchone()
    legal = json.loads(row["legal_words"])
    word = legal[0]

    resp = client.post(
        f"/api/solo/games/{snap['game_id']}/guess", json={"word": word}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["result"] == "accepted"
    assert body["points"] > 0
    assert body["word"] == word.lower()


def test_guess_not_in_dictionary(client: TestClient, db: sqlite3.Connection) -> None:
    """A nonsense word comes back 'not_in_dictionary' with 0 points."""
    _seed_invite(db)
    _register(client)
    snap = _start_solo(client)

    resp = client.post(
        f"/api/solo/games/{snap['game_id']}/guess", json={"word": "zzzqqq"}
    )
    assert resp.status_code == 200
    assert resp.json()["result"] == "not_in_dictionary"
    assert resp.json()["points"] == 0


def test_guess_duplicate(client: TestClient, db: sqlite3.Connection) -> None:
    """The same word twice comes back 'already_submitted' on the second try."""
    _seed_invite(db)
    _register(client)
    snap = _start_solo(client)

    import json
    legal = json.loads(
        db.execute(
            "SELECT legal_words FROM games WHERE id = ?", (snap["game_id"],)
        ).fetchone()["legal_words"]
    )
    word = legal[0]

    r1 = client.post(f"/api/solo/games/{snap['game_id']}/guess", json={"word": word})
    assert r1.json()["result"] == "accepted"
    r2 = client.post(f"/api/solo/games/{snap['game_id']}/guess", json={"word": word})
    assert r2.json()["result"] == "already_submitted"
    assert r2.json()["points"] == 0


def test_guess_non_letters_rejected(
    client: TestClient, db: sqlite3.Connection
) -> None:
    """Words with digits or punctuation are 400."""
    _seed_invite(db)
    _register(client)
    snap = _start_solo(client)

    resp = client.post(
        f"/api/solo/games/{snap['game_id']}/guess", json={"word": "abc123"}
    )
    assert resp.status_code == 400


def test_guess_too_short_rejected(client: TestClient, db: sqlite3.Connection) -> None:
    """Words shorter than min_legal_length are 400."""
    _seed_invite(db)
    _register(client)
    snap = _start_solo(client)

    resp = client.post(
        f"/api/solo/games/{snap['game_id']}/guess", json={"word": "ab"}
    )
    assert resp.status_code == 400


def test_guess_on_others_game_404(client: TestClient, db: sqlite3.Connection) -> None:
    """Trying to guess on someone else's solo game = 404 (no oracle)."""
    _seed_invite(db)
    _register(client, handle="moth")
    snap = _start_solo(client)
    # Log out and switch to a different user.
    client.post("/api/auth/logout")
    client.cookies.clear()
    _register(client, handle="joel")

    resp = client.post(
        f"/api/solo/games/{snap['game_id']}/guess", json={"word": "anything"}
    )
    assert resp.status_code == 404


def test_guess_after_timer_expired_410(
    client: TestClient, db: sqlite3.Connection
) -> None:
    """A guess after ends_at returns 410 and auto-ends the game."""
    _seed_invite(db)
    _register(client)
    snap = _start_solo(client)

    # Backdate ends_at to the past.
    past = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
    db.execute("UPDATE games SET ends_at = ? WHERE id = ?", (past, snap["game_id"]))

    resp = client.post(
        f"/api/solo/games/{snap['game_id']}/guess", json={"word": "anything"}
    )
    assert resp.status_code == 410

    # Auto-end stamped ended_at.
    row = db.execute(
        "SELECT ended_at FROM games WHERE id = ?", (snap["game_id"],)
    ).fetchone()
    assert row["ended_at"] is not None


# --- End -------------------------------------------------------------------


def test_end_returns_full_result(client: TestClient, db: sqlite3.Connection) -> None:
    """Ending returns a GameResult with the player + missed_words."""
    _seed_invite(db)
    _register(client)
    snap = _start_solo(client)

    # Submit one real guess so the player has a non-empty list.
    import json
    legal = json.loads(
        db.execute(
            "SELECT legal_words FROM games WHERE id = ?", (snap["game_id"],)
        ).fetchone()["legal_words"]
    )
    word = legal[0]
    client.post(f"/api/solo/games/{snap['game_id']}/guess", json={"word": word})

    resp = client.post(f"/api/solo/games/{snap['game_id']}/end")
    assert resp.status_code == 200
    body = resp.json()
    assert body["game_id"] == snap["game_id"]
    assert len(body["players"]) == 1
    assert body["players"][0]["words"][0]["word"] == word.lower()
    # Missed = legal - found.
    found_words = {e["word"] for e in body["players"][0]["words"]}
    missed_words = {m["word"] for m in body["missed_words"]}
    assert found_words.isdisjoint(missed_words)
    assert found_words | missed_words == set(legal)


def test_end_is_idempotent(client: TestClient, db: sqlite3.Connection) -> None:
    """End twice — second call returns the same result without re-stamping."""
    _seed_invite(db)
    _register(client)
    snap = _start_solo(client)

    r1 = client.post(f"/api/solo/games/{snap['game_id']}/end")
    assert r1.status_code == 200
    first_ended = r1.json()["ended_at"]

    r2 = client.post(f"/api/solo/games/{snap['game_id']}/end")
    assert r2.status_code == 200
    assert r2.json()["ended_at"] == first_ended


def test_end_on_others_game_404(client: TestClient, db: sqlite3.Connection) -> None:
    """Cross-user end = 404, same as guess."""
    _seed_invite(db)
    _register(client, handle="moth")
    snap = _start_solo(client)
    client.post("/api/auth/logout")
    client.cookies.clear()
    _register(client, handle="joel")

    resp = client.post(f"/api/solo/games/{snap['game_id']}/end")
    assert resp.status_code == 404
