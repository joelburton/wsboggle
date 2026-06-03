"""FastAPI dependencies — the glue that hands handlers a resolved
database connection and current user.

Three Depends-ables:

- :func:`get_db` — returns the connection stashed on ``app.state.db``
  by the lifespan. Handlers ``Depends(get_db)`` to get a sqlite3
  connection rather than digging through ``request.app.state``.
- :func:`current_user` — returns a :class:`~wsboggle.auth.User` or
  ``None`` based on the session cookie. Public endpoints use this to
  branch on auth state.
- :func:`require_user` — same, but 401s instead of returning ``None``.
  Use for endpoints that demand auth.

Session resolution also bumps the sliding expiry — every authed
request extends the session by ``SESSION_TTL``.
"""

from __future__ import annotations

import sqlite3

from fastapi import Depends, HTTPException, Request

from wsboggle import auth


SESSION_COOKIE_NAME = "wsboggle_session"


def get_db(request: Request) -> sqlite3.Connection:
    """Return the shared sqlite3 connection from app state."""
    conn: sqlite3.Connection = request.app.state.db
    return conn


def current_user(
    request: Request,
    db: sqlite3.Connection = Depends(get_db),
) -> auth.User | None:
    """Resolve the request's authenticated user from the session
    cookie, or ``None`` if not signed in / session expired.

    Side effect: a successful resolution calls
    :func:`auth.touch_session` so the sliding window advances.
    """
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if not token:
        return None
    session = auth.find_session(db, token)
    if session is None or auth.is_expired(session):
        return None
    user = auth.find_user_by_id(db, session.user_id)
    if user is None:
        return None
    auth.touch_session(db, token)
    return user


def require_user(user: auth.User | None = Depends(current_user)) -> auth.User:
    """Same as :func:`current_user` but 401s when unauthenticated."""
    if user is None:
        raise HTTPException(status_code=401, detail="not authenticated")
    return user
