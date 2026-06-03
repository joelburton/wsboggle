"""Authentication primitives and DB helpers.

Mirrors crossplay's pattern, trimmed to wsboggle's leaner model
(no email, no admin flag, no per-user invite tracking, no prefs).

- **Password hashing** uses :func:`hashlib.scrypt` from the stdlib
  (no native dep). Stored as a self-describing string,
  ``scrypt$N$r$p$saltHex$keyHex``, so future tuning of the cost
  parameters doesn't break old hashes.
- **Sessions** are server-side rows keyed by a random 32-byte hex
  token. The token is the cookie value the browser sends back; the
  row carries user_id, sliding expiry, creation time.
- **Handles** validate as ``^[a-z0-9_-]{2,32}$`` against the
  lowercased form. Display form preserves case.
- **Invite codes** lookup case-insensitively against
  ``invite_codes``; codes are reusable and not tracked per-user.

This module is intentionally framework-free (no FastAPI imports) so
it's straightforward to test and reuse. FastAPI glue lives in
:mod:`wsboggle.deps` and :mod:`wsboggle.auth_routes`.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta


# --- Public constants ------------------------------------------------------

SESSION_TTL = timedelta(days=30)
"""Sliding session window. Each authed request bumps expiry by this much."""

MIN_PASSWORD_LENGTH = 6
"""Length floor only — we don't pester about composition."""

HANDLE_RE = re.compile(r"^[a-z0-9_-]{2,32}$")
"""Tested against the lowercased form. Display form preserves case."""


# --- Internal scrypt parameters --------------------------------------------

_SCRYPT_N = 16384
_SCRYPT_R = 8
_SCRYPT_P = 1
_SCRYPT_KEYLEN = 32
_SCRYPT_SALTLEN = 16


# --- Public types ----------------------------------------------------------


@dataclass(frozen=True)
class User:
    """Public user info. Safe to expose to handlers and serialize."""

    id: int
    handle: str
    handle_lower: str


@dataclass(frozen=True)
class Session:
    """A session row, in just-enough-to-decide-auth form."""

    id: str
    user_id: int
    expires_at: datetime


# --- Password hashing ------------------------------------------------------


def hash_password(password: str) -> str:
    """Hash a password using scrypt and return the storage string."""
    salt = secrets.token_bytes(_SCRYPT_SALTLEN)
    key = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=_SCRYPT_N,
        r=_SCRYPT_R,
        p=_SCRYPT_P,
        dklen=_SCRYPT_KEYLEN,
    )
    return f"scrypt${_SCRYPT_N}${_SCRYPT_R}${_SCRYPT_P}${salt.hex()}${key.hex()}"


def verify_password(password: str, stored: str) -> bool:
    """Constant-time verify. Any parse failure → False (treated as
    "doesn't match" rather than raising — a malformed stored hash
    shouldn't be able to crash the login route)."""
    parts = stored.split("$")
    if len(parts) != 6 or parts[0] != "scrypt":
        return False
    try:
        n, r, p = int(parts[1]), int(parts[2]), int(parts[3])
        salt = bytes.fromhex(parts[4])
        stored_key = bytes.fromhex(parts[5])
    except ValueError:
        return False
    if not stored_key:
        return False
    derived = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=n,
        r=r,
        p=p,
        dklen=len(stored_key),
    )
    return hmac.compare_digest(derived, stored_key)


# --- Handle validation -----------------------------------------------------


def validate_handle(handle: object) -> str | None:
    """Return the normalized (lowercased, stripped) handle, or None
    if the input is not a string or fails :data:`HANDLE_RE`."""
    if not isinstance(handle, str):
        return None
    lower = handle.strip().lower()
    if not HANDLE_RE.match(lower):
        return None
    return lower


# --- Session tokens --------------------------------------------------------


def new_session_token() -> str:
    """32 random bytes, hex-encoded (64 chars). Cookie-friendly."""
    return secrets.token_hex(32)


# --- Time helpers ----------------------------------------------------------


def _now() -> datetime:
    """UTC now — single helper so tests can monkeypatch consistently."""
    return datetime.now(UTC)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


# --- DB helpers ------------------------------------------------------------


def invite_code_exists(db: sqlite3.Connection, code: str) -> bool:
    """Case-insensitive existence check against ``invite_codes``.

    Codes are reusable (no per-use tracking) and don't expire; admins
    delete rows by hand to invalidate. The ``code`` column is
    ``COLLATE NOCASE`` so the lowercase lookup hits regardless of how
    the row was inserted.
    """
    row = db.execute(
        "SELECT 1 FROM invite_codes WHERE code = ?",
        (code.lower(),),
    ).fetchone()
    return row is not None


def find_user_by_id(db: sqlite3.Connection, user_id: int) -> User | None:
    """Lookup for the session-resolution path. No password hash."""
    row = db.execute(
        "SELECT id, handle, handle_lower FROM users WHERE id = ?",
        (user_id,),
    ).fetchone()
    if row is None:
        return None
    return User(id=row["id"], handle=row["handle"], handle_lower=row["handle_lower"])


def find_user_password_hash(db: sqlite3.Connection, handle_lower: str) -> tuple[User, str] | None:
    """Lookup for the login path. Returns the user + their stored
    password hash, or None if no such handle. The hash is only ever
    consumed by :func:`verify_password` — never returned to callers
    outside this module."""
    row = db.execute(
        "SELECT id, handle, handle_lower, password_hash FROM users WHERE handle_lower = ?",
        (handle_lower,),
    ).fetchone()
    if row is None:
        return None
    return (
        User(id=row["id"], handle=row["handle"], handle_lower=row["handle_lower"]),
        row["password_hash"],
    )


def handle_is_taken(db: sqlite3.Connection, handle_lower: str) -> bool:
    """Cheap check used at registration."""
    row = db.execute(
        "SELECT 1 FROM users WHERE handle_lower = ?",
        (handle_lower,),
    ).fetchone()
    return row is not None


def create_user(
    db: sqlite3.Connection,
    *,
    handle: str,
    handle_lower: str,
    password_hash: str,
) -> int:
    """Insert a new user. The caller has already validated everything
    and hashed the password."""
    now = _iso(_now())
    cur = db.execute(
        "INSERT INTO users (handle, handle_lower, password_hash, created_at) "
        "VALUES (?, ?, ?, ?)",
        (handle, handle_lower, password_hash, now),
    )
    assert cur.lastrowid is not None
    return cur.lastrowid


def create_session(db: sqlite3.Connection, user_id: int) -> str:
    """Create a session row and return its token."""
    token = new_session_token()
    now = _now()
    expires = now + SESSION_TTL
    db.execute(
        "INSERT INTO sessions (id, user_id, created_at, last_seen_at, expires_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (token, user_id, _iso(now), _iso(now), _iso(expires)),
    )
    return token


def find_session(db: sqlite3.Connection, token: str) -> Session | None:
    """Look up a session by token. Does NOT touch expiry — the caller
    is responsible for calling :func:`touch_session` after a
    successful authed request."""
    row = db.execute(
        "SELECT id, user_id, expires_at FROM sessions WHERE id = ?",
        (token,),
    ).fetchone()
    if row is None:
        return None
    return Session(
        id=row["id"],
        user_id=row["user_id"],
        expires_at=datetime.fromisoformat(row["expires_at"]),
    )


def touch_session(db: sqlite3.Connection, token: str) -> None:
    """Push the session's sliding window forward to (now + TTL)."""
    now = _now()
    db.execute(
        "UPDATE sessions SET last_seen_at = ?, expires_at = ? WHERE id = ?",
        (_iso(now), _iso(now + SESSION_TTL), token),
    )


def delete_session(db: sqlite3.Connection, token: str) -> None:
    """Idempotent — a stale cookie on re-logout is a no-op."""
    db.execute("DELETE FROM sessions WHERE id = ?", (token,))


def is_expired(session: Session, now: datetime | None = None) -> bool:
    """``expires_at`` is the source of truth; rows past that are
    treated as logged out (and should be swept eventually, not v1)."""
    return session.expires_at <= (now or _now())
