"""HTTP routes for solo play.

Solo is single-player, no chat, no presence, no WS. Each route is a
thin wrapper over the transport-agnostic functions in
:mod:`wsboggle.games`:

- ``POST /api/solo/games`` → start a new game.
- ``POST /api/solo/games/:id/guess`` → submit a guess.
- ``POST /api/solo/games/:id/end`` → end the game and return the
  full results payload.

Authorization is "you created it, you own it." Solo games have
``club_id IS NULL``; club games go through a different route family.
"""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, HTTPException

from wsboggle import auth, games
from wsboggle.deps import get_db, require_user
from wsboggle.shared import (
    GameResult,
    GameSnapshot,
    GameStartRequest,
    GuessRequest,
    GuessResponse,
    SoloGameSummary,
)


router = APIRouter(prefix="/api/solo")


def _load_owned_solo_game(
    db: sqlite3.Connection, game_id: int, user_id: int
) -> games.GameState:
    """Look up a solo game, 404ing if it doesn't exist *or* if the
    caller isn't its owner — same response in both cases so the
    endpoint doesn't double as a game-id existence oracle."""
    state = games.find_game(db, game_id)
    if state is None or state.club_id is not None or state.created_by != user_id:
        raise HTTPException(404, "no such game")
    return state


@router.post("/games", response_model=GameSnapshot)
def start_solo_game(
    body: GameStartRequest,
    db: sqlite3.Connection = Depends(get_db),
    user: auth.User = Depends(require_user),
) -> GameSnapshot:
    """Generate a board, persist the game, and return the snapshot."""
    try:
        games.validate_config(body.config)
    except games.GameConfigError as e:
        raise HTTPException(400, str(e)) from e

    state = games.start_game(
        db, club_id=None, created_by=user.id, config=body.config
    )
    return games.to_snapshot(db, state, viewer_user_id=user.id)


@router.get("/games", response_model=list[SoloGameSummary])
def list_solo_games(
    db: sqlite3.Connection = Depends(get_db),
    user: auth.User = Depends(require_user),
) -> list[SoloGameSummary]:
    """List the current user's solo games, most recent first.

    Includes still-active games (``ended_at: null``) so the UI can
    show "resume" affordances for games someone walked away from.
    """
    return games.list_solo_games_for_user(db, user.id)


@router.post("/games/{game_id}/guess", response_model=GuessResponse)
def submit_solo_guess(
    game_id: int,
    body: GuessRequest,
    db: sqlite3.Connection = Depends(get_db),
    user: auth.User = Depends(require_user),
) -> GuessResponse:
    """Submit one guess.

    Auto-ends the game (and returns 410) if the timer has elapsed —
    the client may have lost the race. Words are required to be
    ASCII letters; length is validated against the game's
    ``min_legal_length``.
    """
    state = _load_owned_solo_game(db, game_id, user.id)

    if not games.is_active(state):
        # Auto-end if the timer just expired and nobody's called /end yet.
        if state.ended_at is None:
            games.end_game(db, state)
        raise HTTPException(410, "game has ended")

    raw = body.word.strip()
    if not raw.isalpha() or not raw.isascii():
        raise HTTPException(400, "word must be letters only")
    if len(raw) < state.config.min_legal_length:
        raise HTTPException(
            400, f"word must be at least {state.config.min_legal_length} letters"
        )

    outcome = games.submit_guess(db, state, user_id=user.id, word=raw)
    if outcome.result == "game_inactive":
        # Race between our pre-check and submit_guess's own check;
        # caller deserves the same 410 either way.
        if state.ended_at is None:
            games.end_game(db, state)
        raise HTTPException(410, "game has ended")
    return GuessResponse(word=raw.lower(), result=outcome.result, points=outcome.points)


@router.post("/games/{game_id}/end", response_model=GameResult)
def end_solo_game(
    game_id: int,
    db: sqlite3.Connection = Depends(get_db),
    user: auth.User = Depends(require_user),
) -> GameResult:
    """End the game (idempotent) and return the result payload.

    Safe to call on a game whose timer has already elapsed — the
    earlier auto-end from a guess attempt would have already stamped
    ``ended_at``; this just builds the result.
    """
    state = _load_owned_solo_game(db, game_id, user.id)
    state = games.end_game(db, state)
    return games.build_result(db, state)
