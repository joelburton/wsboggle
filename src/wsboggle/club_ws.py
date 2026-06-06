"""The club WebSocket — ``/ws/clubs/:id``.

One socket per (club × tab); a user with two browser tabs gets two
sockets and counts as a single "in-club" presence (the registry
tracks user → set-of-sockets, presence transitions only fire on
0↔1 edges).

Auth lifts the session cookie off the WS handshake. Reject paths
``accept()`` first and then close with a custom 4xxx code — pre-
``accept`` close turns into an HTTP-level rejection that the
browser's WebSocket API surfaces as the opaque code 1006, hiding
*why* it failed. Accepting then closing gives the client a real
close code to branch on.

Game flow lives on this same socket: ``newGame`` (any member, all
must be in-club) → ``gameStarted`` broadcast; ``guess`` →
``guessAccepted`` / ``guessRejected`` *to sender only* (competitive
privacy); server-authoritative timer fires ``gameEnded`` to
everyone when ``ends_at`` elapses. CLAUDE.md's collaborative-mode
broadcast (``guessSubmitted``) is reserved for v2 — v1 is
competitive-only.

Concurrency:

- ``_lock`` guards the connection registry; held briefly across
  in-memory work + ``ws.send_json``, never across DB calls.
- ``_game_lock`` is a separate :class:`asyncio.Lock` held across
  the ``newGame`` critical section ("check no active game; insert
  new game; schedule timer"). Separating it from the registry
  lock keeps chat/presence broadcasts from blocking on board
  generation.
- The timer task is fire-and-forget. If the server restarts during
  an active game, on next boot we'd have a stale ``ended_at IS
  NULL`` row with a past ``ends_at`` — a sweep-at-startup is the
  right fix and is deferred.
"""

from __future__ import annotations

import asyncio
import sqlite3
from datetime import datetime
from typing import Any

from fastapi import APIRouter, FastAPI, WebSocket
from fastapi.websockets import WebSocketDisconnect
from pydantic import TypeAdapter, ValidationError

from wsboggle import auth, chat, clubs, games
from wsboggle.deps import SESSION_COOKIE_NAME
from wsboggle.shared import (
    CCancelProposal,
    CChat,
    CEndGame,
    CGameReady,
    CGuess,
    CHello,
    CNewGame,
    CReviewDone,
    ChatMessage,
    ClientMessage,
    ClubMember,
    PendingProposal,
    PendingReview,
    SChatMessage,
    SClubState,
    SFeedback,
    SGameEnded,
    SGameStarted,
    SGuessAccepted,
    SGuessRejected,
    SGuessSubmitted,
    SMemberPresence,
    SProposalUpdate,
    SReviewUpdate,
    ServerMessage,
)


router = APIRouter()


# --- WS close codes --------------------------------------------------------
# RFC 6455 reserves 4000-4999 for application use. We use them so the
# browser console clearly shows *why* a connection was refused.

_CLOSE_UNAUTHENTICATED = 4401
_CLOSE_FORBIDDEN = 4403
_CLOSE_NOT_FOUND = 4404


# --- Connection registry ---------------------------------------------------
# Maps club_id → user_id → set of live sockets. One user can hold
# multiple sockets (e.g. two tabs); presence is "any socket alive."
# Module-global because the lifetime is the process — no per-request
# state to thread through.

_registry: dict[int, dict[int, set[WebSocket]]] = {}
_lock = asyncio.Lock()

# Per-club timer tasks for active games. Keyed by club_id (one
# active game per club at a time). The task fires ``gameEnded``
# when the game's ``ends_at`` elapses.
_timers: dict[int, asyncio.Task[None]] = {}
_game_lock = asyncio.Lock()

# Per-club pending proposal. A ``newGame`` no longer starts a game
# directly — it parks here until every member has clicked Ready, at
# which point the proposal is resolved and ``gameStarted`` fires.
# Lives in memory only; a server restart drops it (the initiator
# will have to Start again).
_pending_proposals: dict[int, PendingProposal] = {}

# Per-club pending review. Opens implicitly when a game ends, closes
# when every member has clicked "Done reviewing". While open, no new
# game can be proposed. Same in-memory lifetime as proposals — a
# restart wipes it; whoever reconnects sees no review pending and the
# club is free to start a new game.
_pending_reviews: dict[int, PendingReview] = {}


def _ts_validate_client_message(raw: Any) -> ClientMessage:
    """Wrap :class:`TypeAdapter` so the call site reads naturally.

    A fresh adapter would work, but caching the instance avoids
    rebuilding the discriminator schema on every message — cheap, but
    we do it for every line of chat the server sees."""
    return _client_adapter.validate_python(raw)


_client_adapter: TypeAdapter[ClientMessage] = TypeAdapter(ClientMessage)


async def _send(ws: WebSocket, msg: ServerMessage) -> None:
    """Serialize a server message via Pydantic and send as JSON.

    Pydantic handles the ``type`` literal and any nested models;
    ``model_dump(mode="json")`` would also work but ``send_json``
    already runs ``json.dumps``, so we go through Python dicts."""
    # ServerMessage is an Annotated union; isinstance checks via
    # ``model_dump`` keep this generic across variants.
    payload = msg.model_dump()  # type: ignore[union-attr]
    await ws.send_json(payload)


async def _broadcast(
    club_id: int,
    msg: ServerMessage,
    *,
    exclude: WebSocket | None = None,
) -> None:
    """Send ``msg`` to every socket in ``club_id``'s registry,
    optionally skipping one (used to keep the sender from echoing
    their own chat back through the broadcast path).

    Failures on individual sockets are swallowed: a disconnected
    socket will be cleaned up by its own receive loop. We do not
    let one dead peer take down everyone else's broadcast.
    """
    sockets = [
        ws
        for sockets_by_user in _registry.get(club_id, {}).values()
        for ws in sockets_by_user
        if ws is not exclude
    ]
    for ws in sockets:
        try:
            await _send(ws, msg)
        except Exception:
            # The peer's recv loop will notice the close and remove
            # itself from the registry. Don't propagate.
            pass


def _members_snapshot(db: sqlite3.Connection, club_id: int) -> list[ClubMember]:
    """Pull every member of the club + tag with current presence.

    Online = at least one socket currently registered for that
    user. Read-only against the DB; the registry read is fine
    without the lock because the caller is already inside it (or
    is doing a one-shot HTTP-like fetch where staleness doesn't
    matter)."""
    rows = db.execute(
        """
        SELECT u.id AS id, u.handle AS handle
        FROM clubs_users cu
        JOIN users u ON u.id = cu.user_id
        WHERE cu.club_id = ?
        ORDER BY u.handle_lower
        """,
        (club_id,),
    ).fetchall()
    by_user = _registry.get(club_id, {})
    return [
        ClubMember(
            user_id=row["id"],
            handle=row["handle"],
            online=bool(by_user.get(row["id"])),
        )
        for row in rows
    ]


async def _register(club_id: int, user_id: int, ws: WebSocket) -> bool:
    """Add ``ws`` to the registry. Returns True iff this is the
    user's first socket for this club (= a presence 0→1 transition
    the caller should broadcast)."""
    async with _lock:
        by_user = _registry.setdefault(club_id, {})
        sockets = by_user.setdefault(user_id, set())
        first = not sockets
        sockets.add(ws)
        return first


async def _unregister(club_id: int, user_id: int, ws: WebSocket) -> bool:
    """Remove ``ws`` from the registry. Returns True iff this was
    the user's last socket for this club (= a presence 1→0
    transition the caller should broadcast)."""
    async with _lock:
        by_user = _registry.get(club_id)
        if by_user is None:
            return False
        sockets = by_user.get(user_id)
        if sockets is None:
            return False
        sockets.discard(ws)
        if not sockets:
            del by_user[user_id]
            if not by_user:
                del _registry[club_id]
            return True
        return False


# --- Handshake auth --------------------------------------------------------


def _resolve_user(
    db: sqlite3.Connection, ws: WebSocket
) -> auth.User | None:
    """Pull the session cookie off the WS handshake and resolve to a
    user, or ``None`` if not authed."""
    token = ws.cookies.get(SESSION_COOKIE_NAME)
    if not token:
        return None
    session = auth.find_session(db, token)
    if session is None or auth.is_expired(session):
        return None
    user = auth.find_user_by_id(db, session.user_id)
    if user is not None:
        auth.touch_session(db, token)
    return user


# --- Route -----------------------------------------------------------------


@router.websocket("/ws/clubs/{club_id}")
async def club_socket(ws: WebSocket, club_id: int) -> None:
    """The club socket. See module docstring.

    Lifecycle:

    1. Resolve the user from the session cookie. Close on auth fail.
    2. Look up the club; close on not-found.
    3. Check membership; close on non-member.
    4. ``accept()`` the socket, add to the registry, send
       ``clubState``, broadcast presence (if this was a 0→1).
    5. Receive loop: validate each frame as a ``ClientMessage`` and
       dispatch on its ``type``.
    6. On disconnect or error, unregister and broadcast presence
       (if this was a 1→0).
    """
    db: sqlite3.Connection = ws.app.state.db

    user = _resolve_user(db, ws)
    if user is None:
        await ws.accept()
        await ws.close(code=_CLOSE_UNAUTHENTICATED)
        return

    summary = clubs.get_club_summary(db, club_id)
    if summary is None:
        await ws.accept()
        await ws.close(code=_CLOSE_NOT_FOUND)
        return
    if not clubs.user_is_member(db, club_id, user.id):
        await ws.accept()
        await ws.close(code=_CLOSE_FORBIDDEN)
        return

    await ws.accept()
    first_socket = await _register(club_id, user.id, ws)

    # Initial snapshot — sent only to this socket. Presence in the
    # snapshot reflects the registry *after* we added ourselves so
    # the connecter sees themselves as online from the first frame.
    try:
        async with _lock:
            members = _members_snapshot(db, club_id)
        active = games.find_active_club_game(db, club_id)
        current_game = (
            games.to_snapshot(db, active, viewer_user_id=user.id)
            if active is not None
            else None
        )
        await _send(
            ws,
            SClubState(
                club_id=club_id,
                name=summary.name,
                members=members,
                chat=chat.history(db, club_id),
                current_game=current_game,
                last_config=games.find_last_club_config(db, club_id),
                pending_proposal=_pending_proposals.get(club_id),
                pending_review=_pending_reviews.get(club_id),
            ),
        )
        if first_socket:
            await _broadcast(
                club_id,
                SMemberPresence(user_id=user.id, online=True),
                exclude=ws,
            )

        await _recv_loop(ws, db, club_id=club_id, user=user)
    except WebSocketDisconnect:
        pass
    finally:
        last_socket = await _unregister(club_id, user.id, ws)
        if last_socket:
            await _broadcast(
                club_id,
                SMemberPresence(user_id=user.id, online=False),
            )


async def _recv_loop(
    ws: WebSocket,
    db: sqlite3.Connection,
    *,
    club_id: int,
    user: auth.User,
) -> None:
    """Pull frames off the socket and dispatch each.

    Invalid JSON or invalid-shape messages reply with a ``feedback``
    toast and keep the socket open — these are usually a transient
    client bug, not a reason to drop the connection."""
    while True:
        raw = await ws.receive_json()

        try:
            msg = _ts_validate_client_message(raw)
        except ValidationError as e:
            await _send(
                ws,
                SFeedback(
                    text=f"bad message: {e.errors()[0]['msg']}",
                    level="warn",
                ),
            )
            continue

        if isinstance(msg, CHello):
            # Currently no-op. The presence broadcast already went
            # out on accept; the snapshot was already delivered.
            continue
        if isinstance(msg, CChat):
            await _handle_chat(ws, db, club_id=club_id, user=user, msg=msg)
            continue
        if isinstance(msg, CNewGame):
            await _handle_new_game(ws, db, club_id=club_id, user=user, msg=msg)
            continue
        if isinstance(msg, CGameReady):
            await _handle_game_ready(ws, db, club_id=club_id, user=user)
            continue
        if isinstance(msg, CCancelProposal):
            await _handle_cancel_proposal(ws, club_id=club_id)
            continue
        if isinstance(msg, CReviewDone):
            await _handle_review_done(ws, db, club_id=club_id, user=user)
            continue
        if isinstance(msg, CGuess):
            await _handle_guess(ws, db, club_id=club_id, user=user, msg=msg)
            continue
        if isinstance(msg, CEndGame):
            await _handle_end_game(ws, db, club_id=club_id)
            continue


async def _handle_chat(
    ws: WebSocket,
    db: sqlite3.Connection,
    *,
    club_id: int,
    user: auth.User,
    msg: CChat,
) -> None:
    """Persist + broadcast one chat line."""
    try:
        stored: ChatMessage = chat.append(
            db,
            club_id=club_id,
            user_id=user.id,
            handle=user.handle,
            text=msg.text,
        )
    except chat.ChatTooLongError as e:
        await _send(ws, SFeedback(text=str(e), level="warn"))
        return

    await _broadcast(club_id, SChatMessage(message=stored))


# --- Game flow ------------------------------------------------------------


def _club_member_ids(db: sqlite3.Connection, club_id: int) -> set[int]:
    """Set of user_ids that belong to a club. Membership is fixed at
    club creation (no add / remove path in v1), so this can be
    re-read whenever needed without caching."""
    return {
        row["user_id"]
        for row in db.execute(
            "SELECT user_id FROM clubs_users WHERE club_id = ?", (club_id,)
        ).fetchall()
    }


def in_flight_phase(club_id: int) -> str | None:
    """Return the in-memory phase for ``club_id``, or ``None`` if no
    pending state.

    Used by ``/api/me`` to warn the home page when the viewer owes
    something to a club. Reads happen without the lock — the snapshot
    can be stale by the time the caller acts on it, but for an HTTP
    indicator that's fine: the worst case is one stale frame before
    the next reload.
    """
    if club_id in _pending_proposals:
        return "proposing"
    if club_id in _pending_reviews:
        return "reviewing"
    return None


async def _handle_new_game(
    ws: WebSocket,
    db: sqlite3.Connection,
    *,
    club_id: int,
    user: auth.User,
    msg: CNewGame,
) -> None:
    """Propose a new game for the club.

    The proposal is parked in :data:`_pending_proposals` until every
    member has clicked Ready; that's when the game is actually
    created and ``gameStarted`` broadcasts. Preconditions:

    1. No game is currently active in the club.
    2. No proposal is already pending.
    3. Every member is currently in-club (otherwise the proposal
       would block forever waiting on an offline player to click).
    4. The supplied config validates.

    On success, the proposal is stored with the initiator already
    marked ready (clicking Start counts as their ready signal) and
    every member's socket receives ``proposalUpdate``.
    """
    async with _game_lock:
        if games.find_active_club_game(db, club_id) is not None:
            await _send(
                ws, SFeedback(text="A game is already in progress.", level="warn")
            )
            return

        if club_id in _pending_proposals:
            await _send(
                ws,
                SFeedback(
                    text="A game has already been proposed.", level="warn"
                ),
            )
            return

        if club_id in _pending_reviews:
            await _send(
                ws,
                SFeedback(
                    text="Waiting for everyone to finish reviewing.",
                    level="warn",
                ),
            )
            return

        # All-online check. Read the registry under its own lock so
        # we see a consistent snapshot.
        async with _lock:
            online_ids = set((_registry.get(club_id) or {}).keys())
        all_member_ids = _club_member_ids(db, club_id)
        missing = all_member_ids - online_ids
        if missing:
            await _send(
                ws,
                SFeedback(
                    text="Waiting for every member to be in the club.",
                    level="warn",
                ),
            )
            return

        try:
            games.validate_config(msg.config)
        except games.GameConfigError as e:
            await _send(ws, SFeedback(text=str(e), level="warn"))
            return

        proposal = PendingProposal(
            config=msg.config,
            initiator_id=user.id,
            ready_user_ids=[user.id],
        )
        _pending_proposals[club_id] = proposal

    await _broadcast(club_id, SProposalUpdate(proposal=proposal))


async def _handle_game_ready(
    ws: WebSocket,
    db: sqlite3.Connection,
    *,
    club_id: int,
    user: auth.User,
) -> None:
    """Mark ``user`` as ready on the pending proposal.

    If this completes the set, the proposal is resolved: the game is
    created via :func:`games.start_game`, the timer task scheduled,
    and ``gameStarted`` broadcast. Otherwise an updated
    ``proposalUpdate`` goes out so peers can render the waiting roster.

    A ready click from a user already in ``ready_user_ids`` is a
    silent no-op (no broadcast, no feedback). If the board generator
    can't satisfy the "good board" constraints, the proposal is
    dropped and everyone gets a feedback toast — the alternative is
    a stuck prompt with no way forward.
    """
    started_state: games.GameState | None = None
    updated_proposal: PendingProposal | None = None
    start_error: str | None = None

    async with _game_lock:
        proposal = _pending_proposals.get(club_id)
        if proposal is None:
            await _send(
                ws, SFeedback(text="No game has been proposed.", level="warn")
            )
            return
        if user.id in proposal.ready_user_ids:
            return
        proposal.ready_user_ids.append(user.id)

        all_member_ids = _club_member_ids(db, club_id)
        if set(proposal.ready_user_ids) >= all_member_ids:
            try:
                started_state = games.start_game(
                    db,
                    club_id=club_id,
                    created_by=proposal.initiator_id,
                    config=proposal.config,
                )
                if started_state.ends_at is not None:
                    _timers[club_id] = asyncio.create_task(
                        _run_timer(
                            ws.app, club_id, started_state.id, started_state.ends_at
                        )
                    )
            except RuntimeError as e:
                start_error = str(e)
            del _pending_proposals[club_id]
        else:
            updated_proposal = proposal

    if started_state is not None:
        await _broadcast(club_id, SProposalUpdate(proposal=None))
        await _broadcast_game_started(db, club_id, started_state)
    elif start_error is not None:
        await _broadcast(club_id, SProposalUpdate(proposal=None))
        await _broadcast(club_id, SFeedback(text=start_error, level="warn"))
    elif updated_proposal is not None:
        await _broadcast(club_id, SProposalUpdate(proposal=updated_proposal))


async def _handle_cancel_proposal(
    ws: WebSocket,
    *,
    club_id: int,
) -> None:
    """Drop the pending proposal. Any connected member can cancel —
    "block on offline" leaves the proposal stuck until someone
    explicitly aborts it (or every member happens to click Ready)."""
    async with _game_lock:
        if _pending_proposals.pop(club_id, None) is None:
            await _send(
                ws, SFeedback(text="No game has been proposed.", level="warn")
            )
            return

    await _broadcast(club_id, SProposalUpdate(proposal=None))


async def _broadcast_game_started(
    db: sqlite3.Connection,
    club_id: int,
    state: games.GameState,
) -> None:
    """Per-viewer fan-out of ``gameStarted``.

    Each recipient gets a snapshot keyed to *their* user id — the
    ``your_guesses`` field is per-viewer (always empty here, but the
    shape matches reconnect-mid-game)."""
    sockets_by_user = list((_registry.get(club_id) or {}).items())
    for user_id, sockets in sockets_by_user:
        snapshot = games.to_snapshot(db, state, viewer_user_id=user_id)
        for ws in sockets:
            try:
                await _send(ws, SGameStarted(snapshot=snapshot))
            except Exception:
                pass


async def _handle_guess(
    ws: WebSocket,
    db: sqlite3.Connection,
    *,
    club_id: int,
    user: auth.User,
    msg: CGuess,
) -> None:
    """Validate and record one guess; reply privately to the sender.

    Mode-aware broadcasting (collaborative ``guessSubmitted``) lands
    when v2 exposes the mode in the UI. v1 is competitive-only:
    nothing about the guess goes to other members during the timer.
    """
    state = games.find_active_club_game(db, club_id)
    if state is None:
        await _send(
            ws,
            SGuessRejected(word=msg.word, reason="game_inactive"),
        )
        return

    outcome = games.submit_guess(db, state, user_id=user.id, word=msg.word)
    normalized = msg.word.strip().lower()

    if outcome.result == "accepted" and state.config.mode == "collaborative":
        # Collaborative mode: the word lands in the shared team list,
        # so every member's socket gets it — including the sender's,
        # whose WordEntry promise resolves off the same broadcast.
        await _broadcast(
            club_id,
            SGuessSubmitted(
                word=normalized,
                points=outcome.points,
                user_id=user.id,
                handle=user.handle,
            ),
        )
        return

    if outcome.result in (
        "accepted",
        "too_short",
        "not_on_board",
        "not_in_word_list",
        "not_a_word",
    ):
        # Competitive accepted (private list) and any illegal-but-
        # recorded case (collaborative *or* competitive) come back
        # as guessAccepted. The client decides whether to append:
        # competitive always does, collaborative skips the
        # illegals (they're only useful for the entry-box feedback).
        await _send(
            ws,
            SGuessAccepted(
                word=normalized,
                points=outcome.points,
                result=outcome.result,
            ),
        )
        return
    # already_submitted / game_inactive — no row written; bubble up
    # as rejection so the client doesn't add to the word list.
    await _send(
        ws,
        SGuessRejected(word=normalized, reason=outcome.result),
    )


# --- Game-end + broadcast (shared by timer and manual end) ----------------


async def _end_and_broadcast(
    db: sqlite3.Connection, club_id: int, state: games.GameState
) -> None:
    """End ``state`` in the DB (if not already), open the review
    phase, and broadcast ``gameEnded`` + ``reviewUpdate`` to every
    socket in the club. Idempotent against a game that's already
    ended (re-end is a no-op in :func:`games.end_game`).

    The review phase gates the next ``newGame`` until every member
    has acked Done."""
    state = games.end_game(db, state)
    result = games.build_result(db, state)
    async with _game_lock:
        review = PendingReview(done_user_ids=[])
        _pending_reviews[club_id] = review
    await _broadcast(club_id, SGameEnded(result=result))
    await _broadcast(club_id, SReviewUpdate(review=review))


async def _handle_review_done(
    ws: WebSocket,
    db: sqlite3.Connection,
    *,
    club_id: int,
    user: auth.User,
) -> None:
    """Mark ``user`` as done reviewing. If this completes the set,
    the review is cleared and a ``reviewUpdate(None)`` broadcasts —
    the club is free to start a new game.

    Sending without a pending review is a silent no-op (common
    race: server cleared the review just as the user clicked Done,
    or the result panel was reopened from a stale tab)."""
    updated: PendingReview | None = None
    cleared = False
    async with _game_lock:
        review = _pending_reviews.get(club_id)
        if review is None:
            return
        if user.id in review.done_user_ids:
            return
        review.done_user_ids.append(user.id)
        all_member_ids = _club_member_ids(db, club_id)
        if set(review.done_user_ids) >= all_member_ids:
            del _pending_reviews[club_id]
            cleared = True
        else:
            updated = review

    if cleared:
        await _broadcast(club_id, SReviewUpdate(review=None))
    elif updated is not None:
        await _broadcast(club_id, SReviewUpdate(review=updated))


# --- Manual end ----------------------------------------------------------


async def _handle_end_game(
    ws: WebSocket,
    db: sqlite3.Connection,
    *,
    club_id: int,
) -> None:
    """End the club's active game now. Any member can fire this.

    Cancels the scheduled timer task (if any) so the timer-driven
    end path doesn't fire a second ``gameEnded`` after this one.
    No active game = friendly feedback rather than an error."""
    state = games.find_active_club_game(db, club_id)
    if state is None:
        await _send(ws, SFeedback(text="No game to end.", level="warn"))
        return

    timer = _timers.pop(club_id, None)
    if timer is not None and not timer.done():
        timer.cancel()
    await _end_and_broadcast(db, club_id, state)


# --- Server-driven timer --------------------------------------------------


async def _run_timer(
    app: FastAPI, club_id: int, game_id: int, ends_at: datetime
) -> None:
    """Sleep until ``ends_at`` then end the game and broadcast.

    Takes the FastAPI app so it can pull ``app.state.db`` directly —
    works for both the live ``newGame`` path (which has a websocket
    handy) and the startup-recovery path (which doesn't have any
    sockets yet).

    A ``CancelledError`` (server shutdown, manual ``endGame``) is
    swallowed silently: the manual path will have already done the
    end + broadcast itself, and a shutdown can't broadcast anyway.
    """
    try:
        now = datetime.now(ends_at.tzinfo)
        delay = (ends_at - now).total_seconds()
        if delay > 0:
            await asyncio.sleep(delay)

        db: sqlite3.Connection = app.state.db
        state = games.find_game(db, game_id)
        if state is None or state.ended_at is not None:
            return
        await _end_and_broadcast(db, club_id, state)
    except asyncio.CancelledError:
        pass
    finally:
        # Drop our entry if it's still us (a fresh newGame / manual
        # end may have already replaced or cleared it).
        if _timers.get(club_id) is asyncio.current_task():
            _timers.pop(club_id, None)


# --- Startup recovery ----------------------------------------------------


def recover_active_games(app: FastAPI) -> None:
    """Bring active games back into a consistent state at boot.

    For every game with ``ended_at IS NULL``:

    - Untimed (``ends_at IS NULL``) → leave alone. It waits for a
      manual ``endGame``.
    - Timer already expired → end it now in the DB. No broadcast
      (no sockets are connected yet); whoever next connects sees
      ``current_game = null`` in their snapshot and the result row
      in history. This is the "stuck game from a crash" sweep.
    - Timer still in the future → schedule a fresh asyncio task to
      fire ``gameEnded`` at the right time. Members who reconnect
      see the in-progress board with the correct remaining time
      because ``ends_at`` lives in the DB; the recovered task
      fires for whoever is connected when the timer hits zero.

    Called from the lifespan startup after :func:`db.connect`.
    Synchronous DB calls + ``create_task`` (which only schedules,
    doesn't await) keep this fast at boot.
    """
    db: sqlite3.Connection = app.state.db
    for state in games.find_unended_games(db):
        if state.ends_at is None:
            continue  # untimed; manual end only
        now = datetime.now(state.ends_at.tzinfo)
        if state.ends_at <= now:
            # Stale: timer would have fired during the downtime.
            games.end_game(db, state)
            continue
        # Live: re-schedule the timer task for the remaining window.
        # The club_id is non-None on multiplayer games; solo games
        # have club_id=None and don't have a registry/broadcast
        # surface to fire into, but their ends_at is still
        # meaningful — solo end happens via HTTP route. Skip them.
        if state.club_id is None:
            continue
        _timers[state.club_id] = asyncio.create_task(
            _run_timer(app, state.club_id, state.id, state.ends_at)
        )
