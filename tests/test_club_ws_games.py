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


def _new_game_config(
    timer_seconds: int | None = 180,
    mode: str = "competitive",
) -> dict[str, Any]:
    return {
        "dice_set": "4",
        "scoring_ladder": "basic",
        "min_legal_length": 3,
        "mode": mode,
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


def _propose_and_ready(
    initiator_ws,
    *others,
    config: dict[str, Any] | None = None,
) -> None:
    """Run the propose-then-ready dance.

    Initiator sends ``newGame`` (auto-counts as their ready signal),
    every other socket sends ``gameReady``. Caller still drains
    ``gameStarted`` afterward — this only fires the messages."""
    initiator_ws.send_json(
        {"type": "newGame", "config": config or _new_game_config()}
    )
    for ws in others:
        ws.send_json({"type": "gameReady"})


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

            _propose_and_ready(joel_ws, moth_ws)

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

            _propose_and_ready(joel_ws, moth_ws)
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


# --- Proposal flow --------------------------------------------------------


def test_proposal_broadcasts_with_initiator_ready(
    client: TestClient, db: sqlite3.Connection
) -> None:
    """A bare ``newGame`` produces ``proposalUpdate`` with the
    initiator already in ``ready_user_ids`` — no game row yet."""
    joel_token, moth_token, club_id = _setup_club(client, db)

    with _ws_connect(client, joel_token, f"/ws/clubs/{club_id}") as joel_ws:
        joel_ws.receive_json()
        with _ws_connect(client, moth_token, f"/ws/clubs/{club_id}") as moth_ws:
            moth_ws.receive_json()
            joel_ws.receive_json()

            joel_ws.send_json({"type": "newGame", "config": _new_game_config()})
            for_joel = _drain_until(joel_ws, "proposalUpdate")
            for_moth = _drain_until(moth_ws, "proposalUpdate")

    joel_id = db.execute(
        "SELECT id FROM users WHERE handle_lower = 'joel'"
    ).fetchone()["id"]

    for msg in (for_joel, for_moth):
        assert msg["proposal"] is not None
        assert msg["proposal"]["initiator_id"] == joel_id
        assert msg["proposal"]["ready_user_ids"] == [joel_id]

    rows = db.execute(
        "SELECT COUNT(*) AS n FROM games WHERE club_id = ?", (club_id,)
    ).fetchone()
    assert rows["n"] == 0


def test_cancel_proposal_clears_for_everyone(
    client: TestClient, db: sqlite3.Connection
) -> None:
    """Either side can cancel a pending proposal; both sockets see
    ``proposalUpdate`` with a null proposal."""
    joel_token, moth_token, club_id = _setup_club(client, db)

    with _ws_connect(client, joel_token, f"/ws/clubs/{club_id}") as joel_ws:
        joel_ws.receive_json()
        with _ws_connect(client, moth_token, f"/ws/clubs/{club_id}") as moth_ws:
            moth_ws.receive_json()
            joel_ws.receive_json()

            joel_ws.send_json({"type": "newGame", "config": _new_game_config()})
            _drain_until(joel_ws, "proposalUpdate")
            _drain_until(moth_ws, "proposalUpdate")

            # moth (the non-initiator) cancels.
            moth_ws.send_json({"type": "cancelProposal"})

            for msg_ws in (joel_ws, moth_ws):
                cleared = _drain_until(msg_ws, "proposalUpdate")
                assert cleared["proposal"] is None

            # A fresh newGame works now (no stuck state).
            _propose_and_ready(joel_ws, moth_ws)
            _drain_until(joel_ws, "gameStarted")
            _drain_until(moth_ws, "gameStarted")


def test_new_game_refused_during_pending_proposal(
    client: TestClient, db: sqlite3.Connection
) -> None:
    """A second ``newGame`` while a proposal is still pending → the
    initiator gets a feedback toast, the proposal is unchanged."""
    joel_token, moth_token, club_id = _setup_club(client, db)

    with _ws_connect(client, joel_token, f"/ws/clubs/{club_id}") as joel_ws:
        joel_ws.receive_json()
        with _ws_connect(client, moth_token, f"/ws/clubs/{club_id}") as moth_ws:
            moth_ws.receive_json()
            joel_ws.receive_json()

            joel_ws.send_json({"type": "newGame", "config": _new_game_config()})
            _drain_until(joel_ws, "proposalUpdate")
            _drain_until(moth_ws, "proposalUpdate")

            moth_ws.send_json(
                {"type": "newGame", "config": _new_game_config()}
            )
            msg = _drain_until(moth_ws, "feedback")
            assert "proposed" in msg["text"].lower()


def test_reconnect_during_proposal_sees_pending_state(
    client: TestClient, db: sqlite3.Connection
) -> None:
    """A socket that opens mid-proposal gets the current
    ``pending_proposal`` on its initial ``clubState`` snapshot —
    enough to render the Ready prompt immediately."""
    joel_token, moth_token, club_id = _setup_club(client, db)

    with _ws_connect(client, joel_token, f"/ws/clubs/{club_id}") as joel_ws:
        joel_ws.receive_json()
        with _ws_connect(client, moth_token, f"/ws/clubs/{club_id}") as moth_ws:
            moth_ws.receive_json()
            joel_ws.receive_json()

            joel_ws.send_json({"type": "newGame", "config": _new_game_config()})
            _drain_until(joel_ws, "proposalUpdate")
            _drain_until(moth_ws, "proposalUpdate")

            # moth drops + reconnects.
            moth_ws.close()
            joel_ws.receive_json()  # memberPresence: moth offline
            with _ws_connect(client, moth_token, f"/ws/clubs/{club_id}") as moth2:
                state = moth2.receive_json()
                assert state["type"] == "clubState"
                assert state["pending_proposal"] is not None
                joel_id = db.execute(
                    "SELECT id FROM users WHERE handle_lower = 'joel'"
                ).fetchone()["id"]
                assert state["pending_proposal"]["initiator_id"] == joel_id


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

            _propose_and_ready(joel_ws, moth_ws)
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

            _propose_and_ready(joel_ws, moth_ws)
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

            _propose_and_ready(joel_ws, moth_ws)
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

            _propose_and_ready(
                joel_ws, moth_ws, config=_new_game_config(timer_seconds=1)
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
            _propose_and_ready(joel_ws, moth_ws)
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


# --- Manual end ----------------------------------------------------------


def test_end_game_from_any_member(
    client: TestClient, db: sqlite3.Connection
) -> None:
    """A manual ``endGame`` from any member ends the game; everyone
    receives ``gameEnded``; the games row gets an ``ended_at`` stamp."""
    joel_token, moth_token, club_id = _setup_club(client, db)

    with _ws_connect(client, joel_token, f"/ws/clubs/{club_id}") as joel_ws:
        joel_ws.receive_json()
        with _ws_connect(client, moth_token, f"/ws/clubs/{club_id}") as moth_ws:
            moth_ws.receive_json()
            joel_ws.receive_json()  # moth online

            # Start with a very long timer so the auto-end can't race us.
            _propose_and_ready(
                joel_ws, moth_ws, config=_new_game_config(timer_seconds=3600)
            )
            _drain_until(joel_ws, "gameStarted")
            _drain_until(moth_ws, "gameStarted")

            # moth ends the game (not the starter).
            moth_ws.send_json({"type": "endGame"})

            joel_end = _drain_until(joel_ws, "gameEnded")
            moth_end = _drain_until(moth_ws, "gameEnded")

    for msg in (joel_end, moth_end):
        assert msg["result"]["ended_at"] is not None

    row = db.execute(
        "SELECT ended_at FROM games WHERE club_id = ?", (club_id,)
    ).fetchone()
    assert row["ended_at"] is not None


def test_end_game_with_no_active_game(
    client: TestClient, db: sqlite3.Connection
) -> None:
    """``endGame`` when nothing's running → ``feedback`` toast,
    no broadcast."""
    joel_token, _moth_token, club_id = _setup_club(client, db)

    with _ws_connect(client, joel_token, f"/ws/clubs/{club_id}") as ws:
        ws.receive_json()
        ws.send_json({"type": "endGame"})
        msg = _drain_until(ws, "feedback")
        assert "no game" in msg["text"].lower()


# --- Review flow ----------------------------------------------------------


def test_review_opens_on_game_ended(
    client: TestClient, db: sqlite3.Connection
) -> None:
    """A ``reviewUpdate`` with an empty ``done_user_ids`` follows
    every ``gameEnded`` — that's the signal the review phase is
    open."""
    joel_token, moth_token, club_id = _setup_club(client, db)

    with _ws_connect(client, joel_token, f"/ws/clubs/{club_id}") as joel_ws:
        joel_ws.receive_json()
        with _ws_connect(client, moth_token, f"/ws/clubs/{club_id}") as moth_ws:
            moth_ws.receive_json()
            joel_ws.receive_json()

            _propose_and_ready(
                joel_ws, moth_ws, config=_new_game_config(timer_seconds=3600)
            )
            _drain_until(joel_ws, "gameStarted")
            _drain_until(moth_ws, "gameStarted")

            joel_ws.send_json({"type": "endGame"})
            _drain_until(joel_ws, "gameEnded")
            _drain_until(moth_ws, "gameEnded")

            for ws in (joel_ws, moth_ws):
                opened = _drain_until(ws, "reviewUpdate")
                assert opened["review"] is not None
                assert opened["review"]["done_user_ids"] == []


def test_review_done_completes_when_all_acked(
    client: TestClient, db: sqlite3.Connection
) -> None:
    """After every member sends ``reviewDone``, a
    ``reviewUpdate(None)`` broadcasts and ``newGame`` works again."""
    joel_token, moth_token, club_id = _setup_club(client, db)

    with _ws_connect(client, joel_token, f"/ws/clubs/{club_id}") as joel_ws:
        joel_ws.receive_json()
        with _ws_connect(client, moth_token, f"/ws/clubs/{club_id}") as moth_ws:
            moth_ws.receive_json()
            joel_ws.receive_json()

            _propose_and_ready(
                joel_ws, moth_ws, config=_new_game_config(timer_seconds=3600)
            )
            _drain_until(joel_ws, "gameStarted")
            _drain_until(moth_ws, "gameStarted")

            joel_ws.send_json({"type": "endGame"})
            _drain_until(joel_ws, "gameEnded")
            _drain_until(moth_ws, "gameEnded")
            # Drain the initial review-opened delta so the cleared
            # delta below is the next reviewUpdate each side sees.
            _drain_until(joel_ws, "reviewUpdate")
            _drain_until(moth_ws, "reviewUpdate")

            # First member acks — review still open, but with one
            # more user in done_user_ids.
            joel_ws.send_json({"type": "reviewDone"})
            for ws in (joel_ws, moth_ws):
                partial = _drain_until(ws, "reviewUpdate")
                assert partial["review"] is not None
                assert len(partial["review"]["done_user_ids"]) == 1

            # Second member acks — review clears.
            moth_ws.send_json({"type": "reviewDone"})
            for ws in (joel_ws, moth_ws):
                cleared = _drain_until(ws, "reviewUpdate")
                assert cleared["review"] is None

            # And now a fresh proposal can land.
            _propose_and_ready(
                joel_ws, moth_ws, config=_new_game_config(timer_seconds=3600)
            )
            _drain_until(joel_ws, "gameStarted")
            _drain_until(moth_ws, "gameStarted")


def test_api_me_in_flight_tracks_phase(
    client: TestClient, db: sqlite3.Connection
) -> None:
    """``/api/me`` reports ``in_flight`` for each club: null when
    nothing's happening, ``proposing`` / ``playing`` / ``reviewing``
    as the club moves through a full cycle. The home page reads this
    to badge clubs + confirm before starting a solo game."""
    joel_token, moth_token, club_id = _setup_club(client, db)

    def fetch_phase() -> str | None:
        client.cookies.clear()
        client.cookies.set("wsboggle_session", joel_token)
        body = client.get("/api/me").json()
        clubs = [c for c in body["clubs"] if c["id"] == club_id]
        assert clubs, "club missing from /api/me"
        return clubs[0]["in_flight"]

    # Idle: no game, no proposal, no review.
    assert fetch_phase() is None

    with _ws_connect(client, joel_token, f"/ws/clubs/{club_id}") as joel_ws:
        joel_ws.receive_json()
        with _ws_connect(client, moth_token, f"/ws/clubs/{club_id}") as moth_ws:
            moth_ws.receive_json()
            joel_ws.receive_json()

            # Open a proposal but don't have moth ready yet.
            joel_ws.send_json({"type": "newGame", "config": _new_game_config(timer_seconds=3600)})
            _drain_until(joel_ws, "proposalUpdate")
            _drain_until(moth_ws, "proposalUpdate")
            assert fetch_phase() == "proposing"

            # moth acks → game starts → in_flight flips to playing.
            moth_ws.send_json({"type": "gameReady"})
            _drain_until(joel_ws, "gameStarted")
            _drain_until(moth_ws, "gameStarted")
            assert fetch_phase() == "playing"

            # End the game → review opens.
            joel_ws.send_json({"type": "endGame"})
            _drain_until(joel_ws, "gameEnded")
            _drain_until(joel_ws, "reviewUpdate")
            _drain_until(moth_ws, "gameEnded")
            _drain_until(moth_ws, "reviewUpdate")
            assert fetch_phase() == "reviewing"

            # Both ack done → review clears.
            joel_ws.send_json({"type": "reviewDone"})
            moth_ws.send_json({"type": "reviewDone"})
            _drain_until(joel_ws, "reviewUpdate")
            _drain_until(moth_ws, "reviewUpdate")
            _drain_until(joel_ws, "reviewUpdate")
            _drain_until(moth_ws, "reviewUpdate")
    assert fetch_phase() is None


def test_new_game_refused_during_pending_review(
    client: TestClient, db: sqlite3.Connection
) -> None:
    """While a review is pending, ``newGame`` from anyone is
    refused with a feedback toast."""
    joel_token, moth_token, club_id = _setup_club(client, db)

    with _ws_connect(client, joel_token, f"/ws/clubs/{club_id}") as joel_ws:
        joel_ws.receive_json()
        with _ws_connect(client, moth_token, f"/ws/clubs/{club_id}") as moth_ws:
            moth_ws.receive_json()
            joel_ws.receive_json()

            _propose_and_ready(
                joel_ws, moth_ws, config=_new_game_config(timer_seconds=3600)
            )
            _drain_until(joel_ws, "gameStarted")
            _drain_until(moth_ws, "gameStarted")

            joel_ws.send_json({"type": "endGame"})
            _drain_until(joel_ws, "gameEnded")
            _drain_until(joel_ws, "reviewUpdate")

            joel_ws.send_json({"type": "newGame", "config": _new_game_config()})
            msg = _drain_until(joel_ws, "feedback")
            assert "reviewing" in msg["text"].lower()


# --- Collaborative mode --------------------------------------------------


def _pick_legal_word(db: sqlite3.Connection, game_id: int) -> str:
    """Pull one legal word off the freshly-generated board so we have
    a guaranteed-acceptable guess to feed into the WS."""
    import json
    row = db.execute(
        "SELECT legal_words FROM games WHERE id = ?", (game_id,)
    ).fetchone()
    legal = sorted(json.loads(row["legal_words"]), key=len, reverse=True)
    assert legal, "board should have at least one legal word"
    return legal[0]


def test_collab_accepted_guess_broadcasts(
    client: TestClient, db: sqlite3.Connection
) -> None:
    """In collaborative mode an accepted guess broadcasts
    ``guessSubmitted`` to every connected socket, *including* the
    submitter — the sender's WordEntry promise resolves off that
    same broadcast."""
    joel_token, moth_token, club_id = _setup_club(client, db)

    with _ws_connect(client, joel_token, f"/ws/clubs/{club_id}") as joel_ws:
        joel_ws.receive_json()
        with _ws_connect(client, moth_token, f"/ws/clubs/{club_id}") as moth_ws:
            moth_ws.receive_json()
            joel_ws.receive_json()  # moth online

            _propose_and_ready(
                joel_ws, moth_ws, config=_new_game_config(mode="collaborative")
            )
            snap = _drain_until(joel_ws, "gameStarted")["snapshot"]
            _drain_until(moth_ws, "gameStarted")

            word = _pick_legal_word(db, snap["game_id"])
            joel_ws.send_json({"type": "guess", "word": word})

            for_joel = _drain_until(joel_ws, "guessSubmitted")
            for_moth = _drain_until(moth_ws, "guessSubmitted")

    for seen in (for_joel, for_moth):
        assert seen["word"] == word.lower()
        assert seen["points"] > 0
        assert seen["handle"] == "joel"


def test_collab_dedup_across_users(
    client: TestClient, db: sqlite3.Connection
) -> None:
    """Once any team member accepts a word, the next submitter
    gets ``already_submitted`` (no second broadcast, no
    double-count)."""
    joel_token, moth_token, club_id = _setup_club(client, db)

    with _ws_connect(client, joel_token, f"/ws/clubs/{club_id}") as joel_ws:
        joel_ws.receive_json()
        with _ws_connect(client, moth_token, f"/ws/clubs/{club_id}") as moth_ws:
            moth_ws.receive_json()
            joel_ws.receive_json()

            _propose_and_ready(
                joel_ws, moth_ws, config=_new_game_config(mode="collaborative")
            )
            snap = _drain_until(joel_ws, "gameStarted")["snapshot"]
            _drain_until(moth_ws, "gameStarted")

            word = _pick_legal_word(db, snap["game_id"])
            joel_ws.send_json({"type": "guess", "word": word})
            _drain_until(joel_ws, "guessSubmitted")
            _drain_until(moth_ws, "guessSubmitted")

            moth_ws.send_json({"type": "guess", "word": word})
            seen = _drain_until(moth_ws, "guessRejected")
            assert seen["reason"] == "already_submitted"

    # Only one row should exist in the DB.
    n = db.execute(
        "SELECT COUNT(*) AS n FROM guesses WHERE game_id = ?",
        (snap["game_id"],),
    ).fetchone()["n"]
    assert n == 1


def test_collab_reconnect_sees_team_list(
    client: TestClient, db: sqlite3.Connection
) -> None:
    """A mid-game reconnect's ``clubState.current_game.your_guesses``
    is the *shared* team list, not just the reconnecter's own
    submissions."""
    joel_token, moth_token, club_id = _setup_club(client, db)

    with _ws_connect(client, joel_token, f"/ws/clubs/{club_id}") as joel_ws:
        joel_ws.receive_json()
        with _ws_connect(client, moth_token, f"/ws/clubs/{club_id}") as moth_ws:
            moth_ws.receive_json()
            joel_ws.receive_json()

            _propose_and_ready(
                joel_ws, moth_ws, config=_new_game_config(mode="collaborative")
            )
            snap = _drain_until(joel_ws, "gameStarted")["snapshot"]
            _drain_until(moth_ws, "gameStarted")

            word = _pick_legal_word(db, snap["game_id"])
            # moth submits — joel reconnects below and should see it.
            moth_ws.send_json({"type": "guess", "word": word})
            _drain_until(joel_ws, "guessSubmitted")
            _drain_until(moth_ws, "guessSubmitted")

        # joel reconnects (close + reopen).
        joel_ws.close()

    with _ws_connect(client, joel_token, f"/ws/clubs/{club_id}") as joel2:
        state = joel2.receive_json()
        cg = state["current_game"]
        assert cg is not None
        words = [g["word"] for g in cg["your_guesses"]]
        assert word.lower() in words
        added_by = cg["your_guesses"][0]["added_by_handle"]
        assert added_by == "moth"


def test_collab_result_is_single_team_block(
    client: TestClient, db: sqlite3.Connection
) -> None:
    """End-of-game result in collaborative mode has one entry,
    handle 'team', containing every accepted word."""
    joel_token, moth_token, club_id = _setup_club(client, db)

    with _ws_connect(client, joel_token, f"/ws/clubs/{club_id}") as joel_ws:
        joel_ws.receive_json()
        with _ws_connect(client, moth_token, f"/ws/clubs/{club_id}") as moth_ws:
            moth_ws.receive_json()
            joel_ws.receive_json()

            _propose_and_ready(
                joel_ws, moth_ws, config=_new_game_config(mode="collaborative")
            )
            snap = _drain_until(joel_ws, "gameStarted")["snapshot"]
            _drain_until(moth_ws, "gameStarted")

            word = _pick_legal_word(db, snap["game_id"])
            moth_ws.send_json({"type": "guess", "word": word})
            _drain_until(joel_ws, "guessSubmitted")
            _drain_until(moth_ws, "guessSubmitted")

            joel_ws.send_json({"type": "endGame"})
            result_msg = _drain_until(joel_ws, "gameEnded")
            _drain_until(moth_ws, "gameEnded")

    result = result_msg["result"]
    assert len(result["players"]) == 1
    team = result["players"][0]
    assert team["handle"] == "team"
    assert team["final_total"] > 0
    assert any(w["word"] == word.lower() for w in team["words"])
