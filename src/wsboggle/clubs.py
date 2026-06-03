"""Club DB helpers — create, list-for-user, single-club summary.

Membership is insert-only (CLAUDE.md): there is no leave-club path,
no kick, no archive. ``clubs`` and ``clubs_users`` are populated by
:func:`create_club` and never modified afterward.

Game stats per club (count, last-played-at) are aggregated on the
read side rather than denormalized — at our scale a `LEFT JOIN +
GROUP BY` is much cheaper than the bookkeeping cost of keeping
counters in sync.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

from wsboggle.shared import ClubSummary


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def find_user_id_by_handle(db: sqlite3.Connection, handle_lower: str) -> int | None:
    """Resolve a (normalized) handle to a user id, or ``None`` if no
    such user. Used at club-creation time to translate the
    ``member_handles`` list."""
    row = db.execute(
        "SELECT id FROM users WHERE handle_lower = ?",
        (handle_lower,),
    ).fetchone()
    return None if row is None else row["id"]


def create_club(
    db: sqlite3.Connection,
    *,
    name: str,
    creator_id: int,
    other_user_ids: list[int],
) -> int:
    """Insert a new club + membership rows atomically. Returns the new id.

    The club row and every membership row land in one transaction so
    a partial failure (e.g. an FK violation if a user was deleted
    between validation and insert) doesn't leave a half-populated
    club with no UI to fix it. SQLite's connection context manager
    treats ``with db:`` as ``BEGIN / COMMIT`` even when the connection
    was opened with ``isolation_level=None`` (see ``db.connect``).

    Caller is responsible for validating ``name`` (non-empty, length
    bounded), that ``other_user_ids`` has no duplicates, that the
    creator is *not* in ``other_user_ids`` (they're added
    automatically here), and that every id exists. This function does
    no validation of its own — keeping it boring lets it be the
    insert path for both the route layer and tests.
    """
    now = _now_iso()
    with db:
        cur = db.execute(
            "INSERT INTO clubs (name, created_by, created_at) VALUES (?, ?, ?)",
            (name, creator_id, now),
        )
        assert cur.lastrowid is not None
        club_id: int = cur.lastrowid

        member_ids = [creator_id, *other_user_ids]
        for uid in member_ids:
            db.execute(
                "INSERT INTO clubs_users (club_id, user_id, joined_at) "
                "VALUES (?, ?, ?)",
                (club_id, uid, now),
            )
    return club_id


def _member_handles_by_club(
    db: sqlite3.Connection,
    club_ids: list[int],
) -> dict[int, list[str]]:
    """Fetch the (display-cased) member handles for a batch of clubs.

    One query for the whole set so listing clubs doesn't trigger N+1
    member lookups. Returns ``{club_id: [handle, ...]}`` with each
    list sorted by ``handle_lower``. Clubs with no members (shouldn't
    happen — insert path always adds the creator) map to an empty list.
    """
    if not club_ids:
        return {}
    placeholders = ",".join("?" * len(club_ids))
    rows = db.execute(
        f"""
        SELECT cu.club_id AS club_id, u.handle AS handle
        FROM clubs_users cu
        JOIN users u ON u.id = cu.user_id
        WHERE cu.club_id IN ({placeholders})
        ORDER BY cu.club_id, u.handle_lower
        """,
        tuple(club_ids),
    ).fetchall()

    result: dict[int, list[str]] = {cid: [] for cid in club_ids}
    for row in rows:
        result[row["club_id"]].append(row["handle"])
    return result


def list_clubs_for_user(
    db: sqlite3.Connection,
    user_id: int,
) -> list[ClubSummary]:
    """All clubs the user is in, sorted most-recently-played first.

    Sort order: by ``last_played_at`` desc, with never-played clubs
    pushed to the bottom; tiebreak by ``c.id`` desc so newer clubs
    sit above older ones. Game count and last-played-at are computed
    via a ``LEFT JOIN games`` so a club with zero games still appears
    (with ``game_count=0, last_played_at=None``).
    """
    rows = db.execute(
        """
        SELECT
            c.id          AS id,
            c.name        AS name,
            COUNT(g.id)   AS game_count,
            MAX(g.started_at) AS last_played_at
        FROM clubs c
        JOIN clubs_users cu ON cu.club_id = c.id AND cu.user_id = ?
        LEFT JOIN games g    ON g.club_id  = c.id
        GROUP BY c.id
        ORDER BY (last_played_at IS NULL) ASC, last_played_at DESC, c.id DESC
        """,
        (user_id,),
    ).fetchall()

    if not rows:
        return []

    club_ids = [row["id"] for row in rows]
    members = _member_handles_by_club(db, club_ids)

    return [
        ClubSummary(
            id=row["id"],
            name=row["name"],
            member_handles=members[row["id"]],
            game_count=row["game_count"],
            last_played_at=row["last_played_at"],
        )
        for row in rows
    ]


def get_club_summary(db: sqlite3.Connection, club_id: int) -> ClubSummary | None:
    """Fetch one club's summary, or ``None`` if no such club.

    Used right after :func:`create_club` so the route can return the
    just-created club in the same shape the client already renders for
    the home-page list.
    """
    row = db.execute(
        """
        SELECT
            c.id          AS id,
            c.name        AS name,
            COUNT(g.id)   AS game_count,
            MAX(g.started_at) AS last_played_at
        FROM clubs c
        LEFT JOIN games g ON g.club_id = c.id
        WHERE c.id = ?
        GROUP BY c.id
        """,
        (club_id,),
    ).fetchone()
    if row is None:
        return None
    members = _member_handles_by_club(db, [club_id])[club_id]
    return ClubSummary(
        id=row["id"],
        name=row["name"],
        member_handles=members,
        game_count=row["game_count"],
        last_played_at=row["last_played_at"],
    )


def user_is_member(db: sqlite3.Connection, club_id: int, user_id: int) -> bool:
    """Authorization check — used by anything that operates on a
    specific club (later: opening the WS, fetching game history)."""
    row = db.execute(
        "SELECT 1 FROM clubs_users WHERE club_id = ? AND user_id = ?",
        (club_id, user_id),
    ).fetchone()
    return row is not None
