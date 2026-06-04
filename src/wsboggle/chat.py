"""Chat persistence — append + history.

Chat is per-club and durable: every line lives in ``chat_messages``
forever and gets replayed in full on each WS connect (see the
``clubState`` snapshot). The trusted-friends scale makes that
viable; if a club ever piles up enough lines that the replay
becomes painful, the fix is to cap the snapshot at N lines and add
a paging fetch — no data shape changes needed.

This module is intentionally framework-free (no FastAPI, no
WebSocket types) so it can be called from any context and is
straightforward to test.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

from wsboggle.shared import ChatMessage


# Length cap on a single chat line — generous, but bounded so a
# pasted novel can't bloat the snapshot replay for everyone else.
MAX_CHAT_LEN = 2000


class ChatTooLongError(ValueError):
    """Raised by :func:`append` when ``text`` exceeds
    :data:`MAX_CHAT_LEN`. The WS handler catches and replies with
    a ``feedback`` toast rather than closing the socket."""


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def append(
    db: sqlite3.Connection,
    *,
    club_id: int,
    user_id: int,
    handle: str,
    text: str,
) -> ChatMessage:
    """Persist one chat line and return it (with the new rowid + ts).

    The caller is responsible for authorization (is ``user_id`` a
    member of ``club_id``?). ``handle`` is denormalized into the
    returned payload so subscribers don't need a second lookup to
    render the message; the DB row itself only stores ``user_id``,
    and the next replay rejoins to fetch the current display handle.
    """
    cleaned = text.strip()
    if not cleaned:
        raise ChatTooLongError("chat message is empty")
    if len(cleaned) > MAX_CHAT_LEN:
        raise ChatTooLongError(
            f"chat message exceeds {MAX_CHAT_LEN} characters"
        )

    ts = _now_iso()
    cur = db.execute(
        "INSERT INTO chat_messages (club_id, user_id, text, ts) "
        "VALUES (?, ?, ?, ?)",
        (club_id, user_id, cleaned, ts),
    )
    assert cur.lastrowid is not None
    return ChatMessage(
        id=cur.lastrowid,
        user_id=user_id,
        handle=handle,
        text=cleaned,
        ts=ts,
    )


def history(db: sqlite3.Connection, club_id: int) -> list[ChatMessage]:
    """Every chat line for one club, oldest first, with current
    handles joined in. Used to fill the ``clubState`` snapshot."""
    rows = db.execute(
        """
        SELECT c.id AS id, c.user_id AS user_id, u.handle AS handle,
               c.text AS text, c.ts AS ts
        FROM chat_messages c
        JOIN users u ON u.id = c.user_id
        WHERE c.club_id = ?
        ORDER BY c.id ASC
        """,
        (club_id,),
    ).fetchall()
    return [
        ChatMessage(
            id=row["id"],
            user_id=row["user_id"],
            handle=row["handle"],
            text=row["text"],
            ts=row["ts"],
        )
        for row in rows
    ]
