"""HTTP routes for clubs — currently just creation.

Listing happens via ``GET /api/me`` so the home page loads in one
round-trip; per-club routes (members, history, the WS) get added as
those features land.
"""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, HTTPException

from wsboggle import auth, clubs, games
from wsboggle.deps import get_db, require_user
from wsboggle.shared import ClubGameSummary, ClubSummary, CreateClubRequest


router = APIRouter(prefix="/api/clubs")


# Sanity bounds — club names are user-set so we want a guardrail
# against accidentally-pasted novels, but nothing tight.
_NAME_MIN_LEN = 1
_NAME_MAX_LEN = 100


@router.post("", response_model=ClubSummary)
def create_club(
    body: CreateClubRequest,
    db: sqlite3.Connection = Depends(get_db),
    user: auth.User = Depends(require_user),
) -> ClubSummary:
    """Create a club. The creator is implicitly added.

    Validation:

    - ``name`` is stripped, then 1..100 characters.
    - ``member_handles`` non-empty (we need n ≥ 2; creator + 1 other),
      and every handle valid + existing in the users table.
    - No duplicate handles.
    - The creator's own handle isn't in ``member_handles`` (they're
      added automatically; including yourself would be confusing).

    Returns the new club as a :class:`ClubSummary` so the client can
    splice it straight into its home-page list with no extra fetch.
    """
    name = body.name.strip()
    if not (_NAME_MIN_LEN <= len(name) <= _NAME_MAX_LEN):
        raise HTTPException(
            400, f"name must be {_NAME_MIN_LEN}–{_NAME_MAX_LEN} characters"
        )

    if not body.member_handles:
        raise HTTPException(400, "club must have at least one other member")

    # Validate every handle and resolve to user IDs.
    seen_lower: set[str] = set()
    other_user_ids: list[int] = []
    for raw in body.member_handles:
        lower = auth.validate_handle(raw)
        if lower is None:
            raise HTTPException(400, f"invalid handle: {raw!r}")
        if lower == user.handle_lower:
            raise HTTPException(
                400, "you can't add yourself; you're added automatically"
            )
        if lower in seen_lower:
            raise HTTPException(400, f"duplicate handle: {raw!r}")
        seen_lower.add(lower)

        other_id = clubs.find_user_id_by_handle(db, lower)
        if other_id is None:
            raise HTTPException(400, f"no user with handle {raw!r}")
        other_user_ids.append(other_id)

    club_id = clubs.create_club(
        db,
        name=name,
        creator_id=user.id,
        other_user_ids=other_user_ids,
    )
    summary = clubs.get_club_summary(db, club_id)
    # Newly created — the SELECT can't miss it.
    assert summary is not None
    return summary


@router.get("/{club_id}", response_model=ClubSummary)
def get_club(
    club_id: int,
    db: sqlite3.Connection = Depends(get_db),
    _user: auth.User = Depends(require_user),
) -> ClubSummary:
    """Fetch one club's summary.

    Open to **any authed user**, not just members — the client uses
    this on a 4403 WS close to render a friendly "you're not in
    <name>" page that lists the actual members so the visitor knows
    who to ask for an invite. The trusted-friends model makes that
    disclosure fine (and tboggle / crossplay both already treat
    handles as public).
    """
    summary = clubs.get_club_summary(db, club_id)
    if summary is None:
        raise HTTPException(404, "no such club")
    return summary


@router.get("/{club_id}/games", response_model=list[ClubGameSummary])
def list_club_games(
    club_id: int,
    db: sqlite3.Connection = Depends(get_db),
    user: auth.User = Depends(require_user),
) -> list[ClubGameSummary]:
    """List ended games for one club, most recent first.

    Caller must be a member. Active games are excluded — the live one
    (if any) is what the club's WS surfaces; history is "what we've
    finished."
    """
    if not clubs.user_is_member(db, club_id, user.id):
        raise HTTPException(404, "no such club")
    return games.list_club_games(db, club_id)
