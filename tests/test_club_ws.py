"""Tests for the club WebSocket — auth, snapshot, chat, presence.

These cover the main club view surface only; game-flow messages over the WS
ship in the next milestone and will have their own test module.

``TestClient.websocket_connect`` carries the client's cookies into
the handshake, so logging in via ``POST /api/auth/register`` is
enough to authenticate the subsequent WS open.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect


# --- Helpers (parallel to test_clubs.py) ----------------------------------


def _seed_invite(db: sqlite3.Connection, code: str = "friends-2026") -> str:
    db.execute(
        "INSERT INTO invite_codes (code, label, created_at) VALUES (?, ?, ?)",
        (code, "test", datetime.now(UTC).isoformat()),
    )
    return code


def _register(client: TestClient, handle: str) -> dict:
    """Register a user and return the MeResponse. Leaves them logged in."""
    resp = client.post(
        "/api/auth/register",
        json={"handle": handle, "password": "hunter2!", "invite_code": "friends-2026"},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def _logout(client: TestClient) -> None:
    client.post("/api/auth/logout")
    client.cookies.clear()


def _make_club(client: TestClient, name: str, members: list[str]) -> int:
    """POST a club with the currently-logged-in user as creator."""
    resp = client.post("/api/clubs", json={"name": name, "member_handles": members})
    assert resp.status_code == 200, resp.text
    return resp.json()["id"]


# --- Auth / authorization ------------------------------------------------


def _expect_close(ws) -> int:
    """Server accepted the WS and then immediately closed it (auth /
    member-check rejection). Pull the close frame and return its code.

    We accept-then-close so browsers see a real 4xxx close code
    rather than an opaque 1006 — see ``club_ws`` module docstring.
    """
    with pytest.raises(WebSocketDisconnect) as exc:
        ws.receive_json()
    return exc.value.code


def test_ws_unauthenticated_closes(
    client: TestClient, db: sqlite3.Connection
) -> None:
    """No session cookie → WS is accepted then closed with 4401."""
    _seed_invite(db)
    _register(client, "moth")
    _logout(client)
    _register(client, "joel")
    club_id = _make_club(client, "joel + moth", ["moth"])

    _logout(client)  # drop the cookie
    with client.websocket_connect(f"/ws/clubs/{club_id}") as ws:
        assert _expect_close(ws) == 4401


def test_ws_non_member_closes(
    client: TestClient, db: sqlite3.Connection
) -> None:
    """An authed user who isn't in the club is closed with 4403."""
    _seed_invite(db)
    _register(client, "moth")
    _logout(client)
    _register(client, "joel")
    club_id = _make_club(client, "joel + moth", ["moth"])
    _logout(client)

    # leah exists but is not in the club.
    _register(client, "leah")
    with client.websocket_connect(f"/ws/clubs/{club_id}") as ws:
        assert _expect_close(ws) == 4403


def test_ws_missing_club_closes(
    client: TestClient, db: sqlite3.Connection
) -> None:
    """Authed user, club id that doesn't exist → 4404."""
    _seed_invite(db)
    _register(client, "joel")

    with client.websocket_connect("/ws/clubs/99999") as ws:
        assert _expect_close(ws) == 4404


# --- clubState snapshot ---------------------------------------------------


def test_ws_initial_snapshot(client: TestClient, db: sqlite3.Connection) -> None:
    """First frame on connect is a clubState with members + chat."""
    _seed_invite(db)
    _register(client, "moth")
    _logout(client)
    _register(client, "joel")
    club_id = _make_club(client, "joel + moth", ["moth"])

    with client.websocket_connect(f"/ws/clubs/{club_id}") as ws:
        msg = ws.receive_json()

    assert msg["type"] == "clubState"
    assert msg["club_id"] == club_id
    assert msg["name"] == "joel + moth"
    handles = sorted(m["handle"] for m in msg["members"])
    assert handles == ["joel", "moth"]
    # joel is online (the connecting socket); moth is not connected.
    by_handle = {m["handle"]: m for m in msg["members"]}
    assert by_handle["joel"]["online"] is True
    assert by_handle["moth"]["online"] is False
    assert msg["chat"] == []


def test_ws_snapshot_replays_chat(
    client: TestClient, db: sqlite3.Connection
) -> None:
    """Persisted chat lines come back in the snapshot."""
    _seed_invite(db)
    _register(client, "moth")
    _logout(client)
    _register(client, "joel")
    club_id = _make_club(client, "joel + moth", ["moth"])

    # Seed the table directly so we don't depend on the chat round-trip.
    db.execute(
        "INSERT INTO chat_messages (club_id, user_id, text, ts) "
        "SELECT ?, id, ?, ? FROM users WHERE handle = 'joel'",
        (club_id, "hi", "2026-06-01T00:00:00+00:00"),
    )

    with client.websocket_connect(f"/ws/clubs/{club_id}") as ws:
        msg = ws.receive_json()

    assert msg["type"] == "clubState"
    assert len(msg["chat"]) == 1
    line = msg["chat"][0]
    assert line["text"] == "hi"
    assert line["handle"] == "joel"


# --- Chat round-trip ------------------------------------------------------


def _setup_two_members(
    client: TestClient, db: sqlite3.Connection
) -> tuple[str, str, int]:
    """Register joel + moth, create a club between them, return the
    two users' session tokens and the club id.

    Tests swap ``client.cookies`` to ``joel_token`` / ``moth_token``
    around each WS connect so a single TestClient stands in for two
    browser tabs. Sessions are real DB rows; the cookie value is the
    session row's id."""
    _seed_invite(db)
    _register(client, "moth")
    moth_token = client.cookies.get("wsboggle_session")
    assert moth_token is not None
    client.cookies.clear()  # don't logout (that deletes the session row)
    _register(client, "joel")
    joel_token = client.cookies.get("wsboggle_session")
    assert joel_token is not None
    club_id = _make_club(client, "joel + moth", ["moth"])
    return joel_token, moth_token, club_id


def _ws_connect(client: TestClient, token: str, url: str):
    """Helper: clear cookies, set the requested session token, open
    the WS. Used to simulate two distinct browser sessions through
    one TestClient."""
    client.cookies.clear()
    client.cookies.set("wsboggle_session", token)
    return client.websocket_connect(url)


def test_ws_chat_broadcasts_to_self_and_others(
    client: TestClient, db: sqlite3.Connection
) -> None:
    """A sent chat is broadcast to every connected socket, including
    the sender. (Echo is how the client confirms acceptance.)"""
    joel_token, moth_token, club_id = _setup_two_members(client, db)

    with _ws_connect(client, joel_token, f"/ws/clubs/{club_id}") as joel_ws:
        joel_ws.receive_json()  # clubState
        with _ws_connect(client, moth_token, f"/ws/clubs/{club_id}") as moth_ws:
            moth_ws.receive_json()  # clubState
            joel_ws.receive_json()  # memberPresence: moth online

            joel_ws.send_json({"type": "chat", "text": "hi moth"})

            seen_self = joel_ws.receive_json()
            seen_other = moth_ws.receive_json()

    for seen in (seen_self, seen_other):
        assert seen["type"] == "chatMessage"
        assert seen["message"]["text"] == "hi moth"
        assert seen["message"]["handle"] == "joel"


def test_ws_chat_persists(client: TestClient, db: sqlite3.Connection) -> None:
    """A chat sent over WS is in the DB after the socket closes."""
    _seed_invite(db)
    _register(client, "moth")
    _logout(client)
    _register(client, "joel")
    club_id = _make_club(client, "joel + moth", ["moth"])

    with client.websocket_connect(f"/ws/clubs/{club_id}") as ws:
        ws.receive_json()  # clubState
        ws.send_json({"type": "chat", "text": "persisted"})
        ws.receive_json()  # chatMessage

    row = db.execute(
        "SELECT text FROM chat_messages WHERE club_id = ?", (club_id,)
    ).fetchone()
    assert row is not None
    assert row["text"] == "persisted"


def test_ws_empty_chat_gets_feedback(
    client: TestClient, db: sqlite3.Connection
) -> None:
    """Whitespace-only chat keeps the socket open with a warning."""
    _seed_invite(db)
    _register(client, "moth")
    _logout(client)
    _register(client, "joel")
    club_id = _make_club(client, "joel + moth", ["moth"])

    with client.websocket_connect(f"/ws/clubs/{club_id}") as ws:
        ws.receive_json()  # clubState
        ws.send_json({"type": "chat", "text": "   "})
        fb = ws.receive_json()
        assert fb["type"] == "feedback"
        assert fb["level"] == "warn"


def test_ws_invalid_message_gets_feedback(
    client: TestClient, db: sqlite3.Connection
) -> None:
    """Unknown ``type`` keeps the socket open with a warning."""
    _seed_invite(db)
    _register(client, "moth")
    _logout(client)
    _register(client, "joel")
    club_id = _make_club(client, "joel + moth", ["moth"])

    with client.websocket_connect(f"/ws/clubs/{club_id}") as ws:
        ws.receive_json()  # clubState
        ws.send_json({"type": "nonsense"})
        fb = ws.receive_json()
        assert fb["type"] == "feedback"


# --- Presence broadcasts --------------------------------------------------


def test_ws_presence_on_member_join_and_leave(
    client: TestClient, db: sqlite3.Connection
) -> None:
    """An existing socket sees memberPresence(online=True) when
    another member connects, and memberPresence(online=False) when
    they leave."""
    joel_token, moth_token, club_id = _setup_two_members(client, db)

    with _ws_connect(client, joel_token, f"/ws/clubs/{club_id}") as joel_ws:
        joel_ws.receive_json()  # clubState

        with _ws_connect(client, moth_token, f"/ws/clubs/{club_id}") as moth_ws:
            moth_ws.receive_json()  # clubState
            join_msg = joel_ws.receive_json()
            assert join_msg["type"] == "memberPresence"
            assert join_msg["online"] is True

        # moth_ws closed → joel sees the leave.
        leave_msg = joel_ws.receive_json()
        assert leave_msg["type"] == "memberPresence"
        assert leave_msg["online"] is False


def test_ws_second_tab_does_not_double_broadcast_presence(
    client: TestClient, db: sqlite3.Connection
) -> None:
    """A user opening a second tab doesn't generate a fresh
    presence broadcast — they were already online."""
    joel_token, moth_token, club_id = _setup_two_members(client, db)

    with _ws_connect(client, joel_token, f"/ws/clubs/{club_id}") as joel_ws:
        joel_ws.receive_json()  # clubState
        with _ws_connect(client, moth_token, f"/ws/clubs/{club_id}") as moth_ws_a:
            moth_ws_a.receive_json()  # clubState
            joel_ws.receive_json()    # presence: moth online

            # Second moth tab — should NOT produce another presence
            # frame on joel_ws.
            with _ws_connect(client, moth_token, f"/ws/clubs/{club_id}") as moth_ws_b:
                moth_ws_b.receive_json()  # clubState

                # Send something on moth_ws_b so we can prove joel_ws
                # still works without buffering an extra presence
                # frame: the next thing joel sees is the chat, not a
                # duplicate presence.
                moth_ws_b.send_json({"type": "chat", "text": "ping"})
                seen = joel_ws.receive_json()
                assert seen["type"] == "chatMessage"
                assert seen["message"]["text"] == "ping"
