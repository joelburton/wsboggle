"""HTTP routes for register / login / logout, plus the public
``GET /api/me``.

Cookies use the ``wsboggle_session`` name (see :data:`SESSION_COOKIE_NAME`
in :mod:`wsboggle.deps`), HTTP-only, Lax SameSite, Path=/, and
``Secure`` outside dev. Wire shapes live in :mod:`wsboggle.shared`.
"""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from wsboggle import auth, clubs, games
from wsboggle.config import settings
from wsboggle.deps import SESSION_COOKIE_NAME, get_db, require_user
from wsboggle.shared import (
    LoginRequest,
    MeResponse,
    PublicUser,
    RegisterRequest,
)


router = APIRouter(prefix="/api")


# --- Helpers ---------------------------------------------------------------


def _set_session_cookie(response: Response, token: str) -> None:
    """Apply the session cookie to a response with our standard flags."""
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        max_age=int(auth.SESSION_TTL.total_seconds()),
        httponly=True,
        samesite="lax",
        secure=not settings.dev,
        path="/",
    )


def _clear_session_cookie(response: Response) -> None:
    """Delete the cookie on logout."""
    response.delete_cookie(key=SESSION_COOKIE_NAME, path="/")


def _build_me(db: sqlite3.Connection, user: auth.User) -> MeResponse:
    """Assemble the ``MeResponse`` (user + clubs).

    Layers per-club ``in_flight`` state on top: ``"playing"`` (active
    game in the DB) wins over ``"proposing"`` / ``"reviewing"`` (only
    one phase can be open at a time on the server anyway, but the
    precedence is explicit here). Used by the home page to warn
    before the viewer starts a solo game while owing something to a
    club.
    """
    # Lazy-import club_ws to dodge the import cycle
    # (club_ws → clubs → ..., touched at startup).
    from wsboggle import club_ws

    club_list = clubs.list_clubs_for_user(db, user.id)
    active_ids = games.find_active_club_ids(db, [c.id for c in club_list])
    for club in club_list:
        if club.id in active_ids:
            club.in_flight = "playing"
        else:
            phase = club_ws.in_flight_phase(club.id)
            if phase is not None:
                club.in_flight = phase  # type: ignore[assignment]
    return MeResponse(
        user=PublicUser(id=user.id, handle=user.handle),
        clubs=club_list,
    )


# --- Routes ----------------------------------------------------------------


@router.post("/auth/register", response_model=MeResponse)
def register(
    body: RegisterRequest,
    response: Response,
    db: sqlite3.Connection = Depends(get_db),
) -> MeResponse:
    """Create a new user and log them in.

    Gated by an invite code from the ``invite_codes`` table. The code
    is reusable and not tracked per-user — see ``CLAUDE.md`` for the
    trust-model rationale.
    """
    handle_lower = auth.validate_handle(body.handle)
    if handle_lower is None:
        raise HTTPException(400, "invalid handle")
    if len(body.password) < auth.MIN_PASSWORD_LENGTH:
        raise HTTPException(
            400, f"password must be at least {auth.MIN_PASSWORD_LENGTH} characters"
        )
    if not auth.invite_code_exists(db, body.invite_code):
        raise HTTPException(400, "invalid invite code")
    if auth.handle_is_taken(db, handle_lower):
        raise HTTPException(409, "handle already taken")

    user_id = auth.create_user(
        db,
        handle=body.handle.strip(),
        handle_lower=handle_lower,
        password_hash=auth.hash_password(body.password),
    )
    token = auth.create_session(db, user_id)
    _set_session_cookie(response, token)

    # Brand-new user — clubs list is empty.
    return MeResponse(
        user=PublicUser(id=user_id, handle=body.handle.strip()),
        clubs=[],
    )


@router.post("/auth/login", response_model=MeResponse)
def login(
    body: LoginRequest,
    response: Response,
    db: sqlite3.Connection = Depends(get_db),
) -> MeResponse:
    """Verify handle + password and start a session.

    Returns 401 for both "unknown handle" and "wrong password" — same
    response so the endpoint doesn't double as a handle-enumeration
    oracle.
    """
    handle_lower = auth.validate_handle(body.handle)
    if handle_lower is None:
        raise HTTPException(401, "invalid handle or password")

    record = auth.find_user_password_hash(db, handle_lower)
    if record is None:
        raise HTTPException(401, "invalid handle or password")
    user, stored = record
    if not auth.verify_password(body.password, stored):
        raise HTTPException(401, "invalid handle or password")

    token = auth.create_session(db, user.id)
    _set_session_cookie(response, token)
    return _build_me(db, user)


@router.post("/auth/logout")
def logout(
    request: Request,
    response: Response,
    db: sqlite3.Connection = Depends(get_db),
) -> dict[str, bool]:
    """Delete the session row and clear the cookie. Idempotent."""
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if token:
        auth.delete_session(db, token)
    _clear_session_cookie(response)
    return {"ok": True}


@router.get("/me", response_model=MeResponse)
def me(
    user: auth.User = Depends(require_user),
    db: sqlite3.Connection = Depends(get_db),
) -> MeResponse:
    """Return the signed-in user and their clubs. 401 when unauth.

    The home page fetches this on load: 200 → render the home view,
    401 → redirect to ``/login``.
    """
    return _build_me(db, user)
