"""``GET /api/games/:id`` — universal load-by-id for games.

Returns a :class:`~wsboggle.shared.GameView` carrying the snapshot
(always) and the result (when the game has ended). The same endpoint
serves three UX paths:

- **Post-game review:** ``result`` is populated; client renders the
  scoreboard + missed words.
- **Mid-game resume:** ``result`` is ``None``, ``snapshot.ended_at``
  is ``None``; client renders the play view with ``ends_at`` driving
  the countdown.
- **Abandoned game:** ``snapshot.ends_at`` is in the past but
  ``ended_at`` is still ``None``. The client treats this as
  game-over and posts ``/end`` to crystallise the result.

Authorization:

- Solo games (``club_id IS NULL``): caller must be the creator.
- Club games: caller must be a member of the game's club.
"""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, HTTPException

from wsboggle import auth, clubs, games
from wsboggle.deps import get_db, require_user
from wsboggle.shared import GameView


router = APIRouter(prefix="/api/games")


@router.get("/{game_id}", response_model=GameView)
def get_game(
    game_id: int,
    db: sqlite3.Connection = Depends(get_db),
    user: auth.User = Depends(require_user),
) -> GameView:
    """Load a game by id. 404 if it doesn't exist or the caller can't see it."""
    state = games.find_game(db, game_id)
    if state is None:
        raise HTTPException(404, "no such game")

    # Authorization branches on solo vs club.
    if state.club_id is None:
        if state.created_by != user.id:
            raise HTTPException(404, "no such game")
    else:
        if not clubs.user_is_member(db, state.club_id, user.id):
            raise HTTPException(404, "no such game")

    snapshot = games.to_snapshot(db, state, viewer_user_id=user.id)
    result = games.build_result(db, state) if state.ended_at is not None else None
    return GameView(snapshot=snapshot, result=result)
