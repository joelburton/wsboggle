"""Auth route + session-resolution tests.

Covers the happy-path register → me → logout flow, plus the boring
but load-bearing rejections (bad invite, bad handle, duplicate
handle, wrong password). Doesn't unit-test the scrypt primitives —
hashlib is already tested upstream.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from wsboggle import auth


# --- Helpers ---------------------------------------------------------------


def _seed_invite(db: sqlite3.Connection, code: str = "friends-2026") -> str:
    """Insert an invite code and return it."""
    db.execute(
        "INSERT INTO invite_codes (code, label, created_at) VALUES (?, ?, ?)",
        (code, "test", datetime.now(UTC).isoformat()),
    )
    return code


def _register(client: TestClient, handle: str, password: str, code: str) -> dict[str, object]:
    """POST /api/auth/register and assert success."""
    resp = client.post(
        "/api/auth/register",
        json={"handle": handle, "password": password, "invite_code": code},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


# --- Register --------------------------------------------------------------


def test_register_happy_path(client: TestClient, db: sqlite3.Connection) -> None:
    """A valid registration creates a user, returns their info, and
    sets a session cookie."""
    _seed_invite(db)
    resp = client.post(
        "/api/auth/register",
        json={"handle": "Moth", "password": "hunter2!", "invite_code": "friends-2026"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["user"]["handle"] == "Moth"
    assert "wsboggle_session" in resp.cookies

    # And the DB has the user with case-preserved display + lowered lookup.
    row = db.execute("SELECT handle, handle_lower FROM users WHERE id = ?", (body["user"]["id"],)).fetchone()
    assert row["handle"] == "Moth"
    assert row["handle_lower"] == "moth"


def test_register_invalid_handle(client: TestClient, db: sqlite3.Connection) -> None:
    """Handles failing :data:`HANDLE_RE` are rejected before invite-code
    lookup happens."""
    _seed_invite(db)
    resp = client.post(
        "/api/auth/register",
        json={"handle": "x", "password": "hunter2!", "invite_code": "friends-2026"},
    )
    assert resp.status_code == 400
    assert "handle" in resp.json()["detail"]


def test_register_short_password(client: TestClient, db: sqlite3.Connection) -> None:
    """Passwords below MIN_PASSWORD_LENGTH are rejected."""
    _seed_invite(db)
    resp = client.post(
        "/api/auth/register",
        json={"handle": "moth", "password": "ab", "invite_code": "friends-2026"},
    )
    assert resp.status_code == 400
    assert "password" in resp.json()["detail"].lower()


def test_register_bad_invite(client: TestClient) -> None:
    """No matching invite-code row = 400."""
    resp = client.post(
        "/api/auth/register",
        json={"handle": "moth", "password": "hunter2!", "invite_code": "nope"},
    )
    assert resp.status_code == 400
    assert "invite" in resp.json()["detail"].lower()


def test_register_invite_is_case_insensitive(client: TestClient, db: sqlite3.Connection) -> None:
    """Codes are matched via NOCASE collation."""
    _seed_invite(db, code="FRIENDS-2026")
    resp = client.post(
        "/api/auth/register",
        json={"handle": "moth", "password": "hunter2!", "invite_code": "friends-2026"},
    )
    assert resp.status_code == 200


def test_register_duplicate_handle(client: TestClient, db: sqlite3.Connection) -> None:
    """Second registration of the same lowered handle = 409."""
    _seed_invite(db)
    _register(client, "moth", "hunter2!", "friends-2026")
    resp = client.post(
        "/api/auth/register",
        json={"handle": "MOTH", "password": "another!", "invite_code": "friends-2026"},
    )
    assert resp.status_code == 409


# --- Login -----------------------------------------------------------------


def test_login_happy_path(client: TestClient, db: sqlite3.Connection) -> None:
    """Login with the right password starts a session."""
    _seed_invite(db)
    _register(client, "moth", "hunter2!", "friends-2026")
    client.cookies.clear()

    resp = client.post("/api/auth/login", json={"handle": "moth", "password": "hunter2!"})
    assert resp.status_code == 200
    assert resp.json()["user"]["handle"] == "moth"
    assert "wsboggle_session" in resp.cookies


def test_login_case_insensitive_handle(client: TestClient, db: sqlite3.Connection) -> None:
    """Login by capitalised handle hits the same user."""
    _seed_invite(db)
    _register(client, "moth", "hunter2!", "friends-2026")
    client.cookies.clear()

    resp = client.post("/api/auth/login", json={"handle": "MOTH", "password": "hunter2!"})
    assert resp.status_code == 200


def test_login_wrong_password(client: TestClient, db: sqlite3.Connection) -> None:
    """Wrong password = 401 with the same message as unknown-handle."""
    _seed_invite(db)
    _register(client, "moth", "hunter2!", "friends-2026")
    client.cookies.clear()

    resp = client.post("/api/auth/login", json={"handle": "moth", "password": "wrong!"})
    assert resp.status_code == 401


def test_login_unknown_handle(client: TestClient) -> None:
    """Unknown handle returns the same 401 as wrong-password — no
    enumeration oracle."""
    resp = client.post("/api/auth/login", json={"handle": "ghost", "password": "anything"})
    assert resp.status_code == 401


# --- /api/me ---------------------------------------------------------------


def test_me_unauthenticated_401(client: TestClient) -> None:
    """No session = 401."""
    resp = client.get("/api/me")
    assert resp.status_code == 401


def test_me_after_register(client: TestClient, db: sqlite3.Connection) -> None:
    """Registering also logs you in; /api/me reflects that."""
    _seed_invite(db)
    body = _register(client, "moth", "hunter2!", "friends-2026")

    resp = client.get("/api/me")
    assert resp.status_code == 200
    assert resp.json()["user"]["id"] == body["user"]["id"]


def test_me_after_login(client: TestClient, db: sqlite3.Connection) -> None:
    """Same after explicit login."""
    _seed_invite(db)
    _register(client, "moth", "hunter2!", "friends-2026")
    client.cookies.clear()
    client.post("/api/auth/login", json={"handle": "moth", "password": "hunter2!"})

    resp = client.get("/api/me")
    assert resp.status_code == 200


# --- Logout ----------------------------------------------------------------


def test_logout_clears_session(client: TestClient, db: sqlite3.Connection) -> None:
    """After logout, /api/me 401s and the session row is gone."""
    _seed_invite(db)
    _register(client, "moth", "hunter2!", "friends-2026")

    # Session is in the DB right after registration.
    n = db.execute("SELECT COUNT(*) AS n FROM sessions").fetchone()["n"]
    assert n == 1

    resp = client.post("/api/auth/logout")
    assert resp.status_code == 200

    # Row is gone.
    n = db.execute("SELECT COUNT(*) AS n FROM sessions").fetchone()["n"]
    assert n == 0

    # And /api/me 401s.
    client.cookies.clear()
    resp = client.get("/api/me")
    assert resp.status_code == 401


def test_logout_when_not_logged_in(client: TestClient) -> None:
    """Logout is idempotent — calling it without a cookie is fine."""
    resp = client.post("/api/auth/logout")
    assert resp.status_code == 200


# --- Session sliding window ------------------------------------------------


def test_expired_session_is_rejected(client: TestClient, db: sqlite3.Connection) -> None:
    """A session whose expires_at is in the past is treated as logged
    out — even though the row still exists."""
    _seed_invite(db)
    _register(client, "moth", "hunter2!", "friends-2026")

    # Backdate the only session row.
    past = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    db.execute("UPDATE sessions SET expires_at = ?", (past,))

    resp = client.get("/api/me")
    assert resp.status_code == 401


def test_me_advances_session_expiry(client: TestClient, db: sqlite3.Connection) -> None:
    """Hitting an authed route through the ``current_user`` dependency
    bumps the session row's ``expires_at`` — proves the sliding window
    is wired through the route layer, not just available as a primitive."""
    _seed_invite(db)
    _register(client, "moth", "hunter2!", "friends-2026")

    # Force the session row to a stale expiry that's still in the future
    # (so the request isn't 401'd) but is older than what `touch_session`
    # would set it to.
    stale = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    db.execute("UPDATE sessions SET expires_at = ?", (stale,))
    before = db.execute("SELECT expires_at FROM sessions").fetchone()["expires_at"]

    resp = client.get("/api/me")
    assert resp.status_code == 200

    after = db.execute("SELECT expires_at FROM sessions").fetchone()["expires_at"]
    assert after > before, "expected /api/me to advance the session's expires_at"


def test_touch_advances_expiry(db: sqlite3.Connection) -> None:
    """Direct unit test on :func:`auth.touch_session`: expiry moves
    forward."""
    # Seed a user + session by hand to skip the route layer.
    db.execute(
        "INSERT INTO users (handle, handle_lower, password_hash, created_at) "
        "VALUES (?, ?, ?, ?)",
        ("moth", "moth", "scrypt$1$1$1$00$00", datetime.now(UTC).isoformat()),
    )
    user_id = db.execute("SELECT id FROM users WHERE handle_lower = 'moth'").fetchone()["id"]
    token = auth.create_session(db, user_id)

    initial_session = auth.find_session(db, token)
    assert initial_session is not None
    initial_expiry = initial_session.expires_at

    # Backdate so the touch produces a visible move forward.
    db.execute(
        "UPDATE sessions SET expires_at = ? WHERE id = ?",
        ((datetime.now(UTC) - timedelta(days=1)).isoformat(), token),
    )

    auth.touch_session(db, token)

    after = auth.find_session(db, token)
    assert after is not None
    assert after.expires_at > initial_expiry - timedelta(seconds=1)  # advanced
