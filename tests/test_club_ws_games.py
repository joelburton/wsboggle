"""Tests for the game-flow over the club WebSocket.

Covers newGame / gameStarted / guess → guessAccepted|guessRejected /
the server-driven timer firing gameEnded, plus mid-game reconnect.

Strategy mirrors ``test_club_ws.py``: one TestClient stands in for
multiple "browser tabs" by swapping cookies on each
``websocket_connect`` call (real session rows are reused — we don't
``/logout`` between sessions since that deletes the row).
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from typing import Any

from fastapi.testclient import TestClient


# --- Setup helpers (mirrors test_club_ws.py) ------------------------------


def _seed_invite(db: sqlite3.Connection, code: str = "friends-2026") -> None:
    db.execute(
        "INSERT INTO invite_codes (code, label, created_at) VALUES (?, ?, ?)",
        (code, "test", datetime.now(UTC).isoformat()),
    )


def _register(client: TestClient, handle: str) -> str:
    resp = client.post(
        "/api/auth/register",
        json={"handle": handle, "password": "hunter2!", "invite_code": "friends-2026"},
    )
    assert resp.status_code == 200, resp.text
    token = client.cookies.get("wsboggle_session")
    assert token is not None
    return token


def _make_club(client: TestClient, name: str, members: list[str]) -> int:
    resp = client.post("/api/clubs", json={"name": name, "member_handles": members})
    assert resp.status_code == 200, resp.text
    return resp.json()["id"]


def _setup_club(
    client: TestClient, db: sqlite3.Connection
) -> tuple[str, str, int]:
    """Register joel + moth and create a club between them. Returns
    ``(joel_token, moth_token, club_id)``."""
    _seed_invite(db)
    client.cookies.clear()
    moth_token = _register(client, "moth")
    client.cookies.clear()
    joel_token = _register(client, "joel")
    club_id = _make_club(client, "joel + moth", ["moth"])
    return joel_token, moth_token, club_id


def _ws_connect(client: TestClient, token: str, url: str):
    client.cookies.clear()
    client.cookies.set("wsboggle_session", token)
    return client.websocket_connect(url)


# --- Wire helpers ---------------------------------------------------------


def _new_game_config(timer_seconds: int | None = 180) -> dict[str, Any]:
    return {
        "dice_set": "4",
        "scoring_ladder": "basic",
        "min_legal_length": 3,
        "mode": "competitive",
        "dupes_cancel": True,
        "timer_seconds": timer_seconds,
        "timer_direction": "down",
        "min_words": None,
        "max_words": None,
        "min_score": None,
        "max_score": None,
        "min_longest": None,
        "max_longest": None,
    }


def _drain_until(ws, type_: str, max_msgs: int = 20) -> dict[str, Any]:
    """Pull frames until one matches ``type_``; return it. Saves
    callers from caring about message-order interleaving (a chat
    line and a presence delta can show up between events)."""
    for _ in range(max_msgs):
        msg = ws.receive_json()
        if msg["type"] == type_:
            return msg
    raise AssertionError(f"never saw {type_}")


# --- newGame --------------------------------------------------------------


def test_new_game_happy_path(
    client: TestClient, db: sqlite3.Connection
) -> None:
    """A newGame from one member starts a game and broadcasts
    ``gameStarted`` to every connected socket with a per-viewer
    snapshot."""
    joel_token, moth_token, club_id = _setup_club(client, db)

    with _ws_connect(client, joel_token, f"/ws/clubs/{club_id}") as joel_ws:
        joel_ws.receive_json()  # clubState
        with _ws_connect(client, moth_token, f"/ws/clubs/{club_id}") as moth_ws:
            moth_ws.receive_json()  # clubState
            joel_ws.receive_json()  # memberPresence: moth online

            joel_ws.send_json({"type": "newGame", "config": _new_game_config()})

            for_joel = _drain_until(joel_ws, "gameStarted")
            for_moth = _drain_until(moth_ws, "gameStarted")

    for snap_msg in (for_joel, for_moth):
        snapshot = snap_msg["snapshot"]
        assert snapshot["game_id"] > 0
        assert len(snapshot["board"]) == 4
        assert snapshot["ends_at"] is not None
        assert snapshot["your_guesses"] == []


def test_new_game_refused_when_member_offline(
    client: TestClient, db: sqlite3.Connection
) -> None:
    """Only one member is online → feedback toast, no game created."""
    joel_token, _moth_token, club_id = _setup_club(client, db)

    with _ws_connect(client, joel_token, f"/ws/clubs/{club_id}") as ws:
        ws.receive_json()  # clubState
        ws.send_json({"type": "newGame", "config": _new_game_config()})
        msg = _drain_until(ws, "feedback")
        assert msg["level"] == "warn"
        assert "every member" in msg["text"].lower()

    rows = db.execute(
        "SELECT COUNT(*) AS n FROM games WHERE club_id = ?", (club_id,)
    ).fetchone()
    assert rows["n"] == 0


def test_new_game_refused_when_one_already_active(
    client: TestClient, db: sqlite3.Connection
) -> None:
    """Trying to start a second game while one is active → feedback,
    no second game row."""
    joel_token, moth_token, club_id = _setup_club(client, db)

    with _ws_connect(client, joel_token, f"/ws/clubs/{club_id}") as joel_ws:
        joel_ws.receive_json()
        with _ws_connect(client, moth_token, f"/ws/clubs/{club_id}") as moth_ws:
            moth_ws.receive_json()
            joel_ws.receive_json()  # moth online

            joel_ws.send_json({"type": "newGame", "config": _new_game_config()})
            _drain_until(joel_ws, "gameStarted")
            _drain_until(moth_ws, "gameStarted")

            # Second attempt from moth — should be refused.
            moth_ws.send_json({"type": "newGame", "config": _new_game_config()})
            msg = _drain_until(moth_ws, "feedback")
            assert "already" in msg["text"].lower()

    rows = db.execute(
        "SELECT COUNT(*) AS n FROM games WHERE club_id = ?", (club_id,)
    ).fetchone()
    assert rows["n"] == 1


# --- guess ----------------------------------------------------------------


def test_guess_accepted_private_to_sender(
    client: TestClient, db: sqlite3.Connection
) -> None:
    """A legal guess gets ``guessAccepted`` to the sender only —
    nothing about it is broadcast to other members during the
    timer (competitive privacy)."""
    joel_token, moth_token, club_id = _setup_club(client, db)

    with _ws_connect(client, joel_token, f"/ws/clubs/{club_id}") as joel_ws:
        joel_ws.receive_json()
        with _ws_connect(client, moth_token, f"/ws/clubs/{club_id}") as moth_ws:
            moth_ws.receive_json()
            joel_ws.receive_json()

            joel_ws.send_json({"type": "newGame", "config": _new_game_config()})
            joel_snap = _drain_until(joel_ws, "gameStarted")["snapshot"]
            _drain_until(moth_ws, "gameStarted")

            # Pick a legal word off the board — the snapshot doesn't
            # carry the full word list (that's competitive privacy
            # too), but the DB has it. Pull one directly.
            import json

            row = db.execute(
                "SELECT legal_words FROM games WHERE id = ?",
                (joel_snap["game_id"],),
            ).fetchone()
            legal = sorted(json.loads(row["legal_words"]), key=len, reverse=True)
            assert legal, "board should have at least one legal word"
            word = legal[0]

            joel_ws.send_json({"type": "guess", "word": word})
            seen = joel_ws.receive_json()
            assert seen["type"] == "guessAccepted"
            assert seen["word"] == word
            assert seen["result"] == "accepted"
            assert seen["points"] > 0

            # moth must NOT see anything about this guess.
            joel_ws.send_json({"type": "chat", "text": "ping"})
            seen_by_moth = moth_ws.receive_json()
            assert seen_by_moth["type"] == "chatMessage"
            assert seen_by_moth["message"]["text"] == "ping"


def test_guess_illegal_word_echoes_back_with_classification(
    client: TestClient, db: sqlite3.Connection
) -> None:
    """Typed-but-illegal words come back as ``guessAccepted`` with
    a finer ``result`` (``too_short`` / ``not_on_board`` /
    ``not_a_word``) so the UI can render distinct feedback in one
    round-trip. The row still lands in the player's word list,
    struck through."""
    joel_token, moth_token, club_id = _setup_club(client, db)

    with _ws_connect(client, joel_token, f"/ws/clubs/{club_id}") as joel_ws:
        joel_ws.receive_json()
        with _ws_connect(client, moth_token, f"/ws/clubs/{club_id}") as moth_ws:
            moth_ws.receive_json()
            joel_ws.receive_json()

            joel_ws.send_json({"type": "newGame", "config": _new_game_config()})
            _drain_until(joel_ws, "gameStarted")
            _drain_until(moth_ws, "gameStarted")

            # "zzqqx" is exceedingly unlikely to be a real word.
            joel_ws.send_json({"type": "guess", "word": "zzqqx"})
            seen = joel_ws.receive_json()
            assert seen["type"] == "guessAccepted"
            assert seen["result"] in ("not_a_word", "not_on_board")
            assert seen["points"] == 0

            # "do" is a real word but below the default min length.
            joel_ws.send_json({"type": "guess", "word": "do"})
            seen = joel_ws.receive_json()
            assert seen["type"] == "guessAccepted"
            assert seen["result"] == "too_short"


def test_guess_duplicate_rejected(
    client: TestClient, db: sqlite3.Connection
) -> None:
    """Re-submitting the same word → ``guessRejected("already_submitted")``."""
    joel_token, moth_token, club_id = _setup_club(client, db)

    with _ws_connect(client, joel_token, f"/ws/clubs/{club_id}") as joel_ws:
        joel_ws.receive_json()
        with _ws_connect(client, moth_token, f"/ws/clubs/{club_id}") as moth_ws:
            moth_ws.receive_json()
            joel_ws.receive_json()

            joel_ws.send_json({"type": "newGame", "config": _new_game_config()})
            _drain_until(joel_ws, "gameStarted")
            _drain_until(moth_ws, "gameStarted")

            joel_ws.send_json({"type": "guess", "word": "zzqqx"})
            joel_ws.receive_json()  # first time
            joel_ws.send_json({"type": "guess", "word": "zzqqx"})
            seen = joel_ws.receive_json()
            assert seen["type"] == "guessRejected"
            assert seen["reason"] == "already_submitted"


def test_guess_with_no_active_game(
    client: TestClient, db: sqlite3.Connection
) -> None:
    """A guess sent before any newGame → ``game_inactive`` rejection."""
    joel_token, _moth_token, club_id = _setup_club(client, db)

    with _ws_connect(client, joel_token, f"/ws/clubs/{club_id}") as ws:
        ws.receive_json()
        ws.send_json({"type": "guess", "word": "hello"})
        msg = ws.receive_json()
        assert msg["type"] == "guessRejected"
        assert msg["reason"] == "game_inactive"


# --- Timer fires gameEnded -----------------------------------------------


def test_timer_fires_game_ended(
    client: TestClient, db: sqlite3.Connection
) -> None:
    """A 1-second-timer game ends on its own; everyone connected
    receives ``gameEnded`` with the full result."""
    joel_token, moth_token, club_id = _setup_club(client, db)

    with _ws_connect(client, joel_token, f"/ws/clubs/{club_id}") as joel_ws:
        joel_ws.receive_json()
        with _ws_connect(client, moth_token, f"/ws/clubs/{club_id}") as moth_ws:
            moth_ws.receive_json()
            joel_ws.receive_json()

            joel_ws.send_json(
                {"type": "newGame", "config": _new_game_config(timer_seconds=1)}
            )
            _drain_until(joel_ws, "gameStarted")
            _drain_until(moth_ws, "gameStarted")

            # The starlette test client runs the asyncio loop on the
            # server thread; receive_json blocks until a frame
            # arrives, which lets the asyncio timer fire.
            joel_end = _drain_until(joel_ws, "gameEnded", max_msgs=5)
            moth_end = _drain_until(moth_ws, "gameEnded", max_msgs=5)

    for msg in (joel_end, moth_end):
        result = msg["result"]
        assert result["ended_at"] is not None
        # Two players show up in the result, even though neither
        # guessed anything.
        handles = {p["handle"] for p in result["players"]}
        assert handles == {"joel", "moth"}


# --- Reconnect mid-game --------------------------------------------------


def test_reconnect_mid_game_includes_current_game(
    client: TestClient, db: sqlite3.Connection
) -> None:
    """Closing and reopening a socket during an active game →
    ``clubState.current_game`` is populated with the viewer's own
    ``your_guesses``."""
    joel_token, moth_token, club_id = _setup_club(client, db)

    # Start a game, submit one guess (legal or not, doesn't matter
    # for the snapshot), then reconnect.
    with _ws_connect(client, joel_token, f"/ws/clubs/{club_id}") as joel_ws:
        joel_ws.receive_json()
        with _ws_connect(client, moth_token, f"/ws/clubs/{club_id}") as moth_ws:
            moth_ws.receive_json()
            joel_ws.receive_json()
            joel_ws.send_json({"type": "newGame", "config": _new_game_config()})
            joel_snap = _drain_until(joel_ws, "gameStarted")["snapshot"]
            _drain_until(moth_ws, "gameStarted")

            # Submit a word.
            import json

            legal = sorted(
                json.loads(
                    db.execute(
                        "SELECT legal_words FROM games WHERE id = ?",
                        (joel_snap["game_id"],),
                    ).fetchone()["legal_words"]
                ),
                key=len,
                reverse=True,
            )
            joel_ws.send_json({"type": "guess", "word": legal[0]})
            joel_ws.receive_json()  # guessAccepted

        # moth_ws closed first; reopen joel's socket.
        joel_ws.close()

    with _ws_connect(client, joel_token, f"/ws/clubs/{club_id}") as joel_ws2:
        state = joel_ws2.receive_json()
        assert state["type"] == "clubState"
        assert state["current_game"] is not None
        cg = state["current_game"]
        assert cg["game_id"] == joel_snap["game_id"]
        assert len(cg["your_guesses"]) == 1
        assert cg["your_guesses"][0]["word"] == legal[0].lower()
