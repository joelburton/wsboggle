"""Tests for club CRUD and the ``GET /api/me`` clubs listing."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

from fastapi.testclient import TestClient


# --- Helpers ---------------------------------------------------------------


def _seed_invite(db: sqlite3.Connection, code: str = "friends-2026") -> str:
    db.execute(
        "INSERT INTO invite_codes (code, label, created_at) VALUES (?, ?, ?)",
        (code, "test", datetime.now(UTC).isoformat()),
    )
    return code


def _register(client: TestClient, handle: str, password: str = "hunter2!") -> dict:
    """Register and log a user in. Leaves them as the current session."""
    resp = client.post(
        "/api/auth/register",
        json={"handle": handle, "password": password, "invite_code": "friends-2026"},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def _logout(client: TestClient) -> None:
    client.post("/api/auth/logout")
    client.cookies.clear()


def _seed_users(db: sqlite3.Connection, client: TestClient, handles: list[str]) -> None:
    """Register a batch of users; final session is the last handle."""
    _seed_invite(db)
    for h in handles:
        _register(client, h)
        _logout(client)


# --- Create club -----------------------------------------------------------


def test_create_club_happy_path(client: TestClient, db: sqlite3.Connection) -> None:
    """Creating a club returns its summary and stores members."""
    _seed_users(db, client, ["moth", "leah"])
    _register(client, "joel")  # joel stays logged in

    resp = client.post(
        "/api/clubs",
        json={"name": "moth + joel", "member_handles": ["moth"]},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["name"] == "moth + joel"
    assert sorted(body["member_handles"]) == ["joel", "moth"]
    assert body["game_count"] == 0
    assert body["last_played_at"] is None


def test_create_club_three_members(client: TestClient, db: sqlite3.Connection) -> None:
    """3-person clubs include all three in member_handles, sorted by lower."""
    _seed_users(db, client, ["moth", "leah"])
    _register(client, "joel")

    resp = client.post(
        "/api/clubs",
        json={"name": "puplandia", "member_handles": ["moth", "leah"]},
    )
    assert resp.status_code == 200
    assert resp.json()["member_handles"] == ["joel", "leah", "moth"]


def test_create_club_unauth(client: TestClient) -> None:
    """Unauthenticated POST = 401."""
    resp = client.post(
        "/api/clubs",
        json={"name": "x", "member_handles": ["moth"]},
    )
    assert resp.status_code == 401


def test_create_club_empty_members(client: TestClient, db: sqlite3.Connection) -> None:
    """A club needs at least one other member."""
    _seed_invite(db)
    _register(client, "joel")

    resp = client.post(
        "/api/clubs",
        json={"name": "lonely", "member_handles": []},
    )
    assert resp.status_code == 400


def test_create_club_unknown_handle(client: TestClient, db: sqlite3.Connection) -> None:
    """Adding a handle that doesn't exist = 400 naming which one."""
    _seed_invite(db)
    _register(client, "joel")

    resp = client.post(
        "/api/clubs",
        json={"name": "x", "member_handles": ["nosuchuser"]},
    )
    assert resp.status_code == 400
    assert "nosuchuser" in resp.json()["detail"]


def test_create_club_invalid_handle(client: TestClient, db: sqlite3.Connection) -> None:
    """Handle failing HANDLE_RE = 400."""
    _seed_invite(db)
    _register(client, "joel")

    resp = client.post(
        "/api/clubs",
        json={"name": "x", "member_handles": ["NO BAD CHARS"]},
    )
    assert resp.status_code == 400


def test_create_club_self_in_members(client: TestClient, db: sqlite3.Connection) -> None:
    """The creator is added implicitly; listing yourself is a 400."""
    _seed_users(db, client, ["moth"])
    _register(client, "joel")

    resp = client.post(
        "/api/clubs",
        json={"name": "x", "member_handles": ["moth", "joel"]},
    )
    assert resp.status_code == 400
    assert "yourself" in resp.json()["detail"].lower()


def test_create_club_duplicate_member(client: TestClient, db: sqlite3.Connection) -> None:
    """Duplicate handles in the list = 400."""
    _seed_users(db, client, ["moth"])
    _register(client, "joel")

    resp = client.post(
        "/api/clubs",
        json={"name": "x", "member_handles": ["moth", "moth"]},
    )
    assert resp.status_code == 400
    assert "duplicate" in resp.json()["detail"].lower()


def test_create_club_duplicate_member_case_insensitive(
    client: TestClient, db: sqlite3.Connection
) -> None:
    """Case differences in handles still count as duplicates."""
    _seed_users(db, client, ["moth"])
    _register(client, "joel")

    resp = client.post(
        "/api/clubs",
        json={"name": "x", "member_handles": ["moth", "Moth"]},
    )
    assert resp.status_code == 400


def test_create_club_name_strip(client: TestClient, db: sqlite3.Connection) -> None:
    """Surrounding whitespace on the name is stripped before validation."""
    _seed_users(db, client, ["moth"])
    _register(client, "joel")

    resp = client.post(
        "/api/clubs",
        json={"name": "   moth + joel  ", "member_handles": ["moth"]},
    )
    assert resp.status_code == 200
    assert resp.json()["name"] == "moth + joel"


def test_create_club_empty_name(client: TestClient, db: sqlite3.Connection) -> None:
    """Empty / whitespace-only name = 400."""
    _seed_users(db, client, ["moth"])
    _register(client, "joel")

    resp = client.post(
        "/api/clubs",
        json={"name": "   ", "member_handles": ["moth"]},
    )
    assert resp.status_code == 400


# --- /api/me reflects clubs -----------------------------------------------


def test_me_includes_clubs(client: TestClient, db: sqlite3.Connection) -> None:
    """After creating a club, /api/me lists it."""
    _seed_users(db, client, ["moth"])
    _register(client, "joel")

    client.post("/api/clubs", json={"name": "moth + joel", "member_handles": ["moth"]})

    resp = client.get("/api/me")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["clubs"]) == 1
    assert body["clubs"][0]["name"] == "moth + joel"


def test_me_does_not_include_others_clubs(
    client: TestClient, db: sqlite3.Connection
) -> None:
    """Clubs you're not in don't show up."""
    _seed_users(db, client, ["leah", "moth"])  # moth is the current session
    # moth makes a private club with leah; joel won't be in it.
    client.post("/api/clubs", json={"name": "moth + leah", "member_handles": ["leah"]})
    _logout(client)

    _register(client, "joel")  # fresh user, not in any club
    resp = client.get("/api/me")
    assert resp.status_code == 200
    assert resp.json()["clubs"] == []


def test_get_club_open_to_any_authed_user(
    client: TestClient, db: sqlite3.Connection
) -> None:
    """``GET /api/clubs/:id`` returns the summary for any authed
    user (member or not). The "not in this club" page on the
    client depends on the non-member case working."""
    _seed_users(db, client, ["leah"])
    _register(client, "moth")  # moth logged in
    club_id = client.post(
        "/api/clubs", json={"name": "moth + leah", "member_handles": ["leah"]}
    ).json()["id"]
    _logout(client)

    # joel is authed but not in the club.
    _register(client, "joel")
    resp = client.get(f"/api/clubs/{club_id}")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["name"] == "moth + leah"
    assert sorted(body["member_handles"]) == ["leah", "moth"]


def test_get_club_404_for_missing(
    client: TestClient, db: sqlite3.Connection
) -> None:
    """Unknown club id → 404."""
    _seed_invite(db)
    _register(client, "joel")
    resp = client.get("/api/clubs/99999")
    assert resp.status_code == 404


def test_get_club_401_for_anon(client: TestClient) -> None:
    """Anonymous request → 401, even though the endpoint is open
    to non-members. Auth still required."""
    resp = client.get("/api/clubs/1")
    assert resp.status_code == 401


def test_me_sort_order(client: TestClient, db: sqlite3.Connection) -> None:
    """Clubs sort by last_played_at desc nulls last, then newest id first."""
    _seed_users(db, client, ["moth", "leah", "kate"])
    _register(client, "joel")

    # Create three clubs in order.
    r1 = client.post(
        "/api/clubs", json={"name": "club-1", "member_handles": ["moth"]}
    ).json()
    r2 = client.post(
        "/api/clubs", json={"name": "club-2", "member_handles": ["leah"]}
    ).json()
    r3 = client.post(
        "/api/clubs", json={"name": "club-3", "member_handles": ["kate"]}
    ).json()

    # Simulate a game in club-2 (oldest by id but most-recently played).
    db.execute(
        """
        INSERT INTO games (club_id, config, board, legal_words, started_at)
        VALUES (?, '{}', 'AAAAAAAAAAAAAAAA', '[]', ?)
        """,
        (r2["id"], "2026-06-01T00:00:00+00:00"),
    )

    resp = client.get("/api/me")
    names = [c["name"] for c in resp.json()["clubs"]]
    # club-2 (played) first; then unplayed by id desc: club-3, club-1.
    assert names == ["club-2", "club-3", "club-1"]
    # Sanity: club-2's last_played_at populated, others None.
    by_name = {c["name"]: c for c in resp.json()["clubs"]}
    assert by_name["club-2"]["last_played_at"] is not None
    assert by_name["club-1"]["last_played_at"] is None
    # And game_count tracks.
    assert by_name["club-2"]["game_count"] == 1
    assert by_name["club-1"]["game_count"] == 0
    # Silence unused-warnings:
    assert r1["name"] == "club-1"
    assert r3["name"] == "club-3"
