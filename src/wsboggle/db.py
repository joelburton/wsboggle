"""SQLite connection + schema bootstrap.

The schema is a single inline DDL script applied on first boot (when
the DB file is created). Subsequent boots open the existing DB
unchanged. No migration framework in v1 — when the schema needs to
change we add an explicit migration step here.

Tables mirror the data model in ``CLAUDE.md``: ``users``, ``clubs``,
``clubs_users``, ``games``, ``guesses``, ``chat_messages``,
``invite_codes``, ``sessions``. Foreign keys are enabled per
connection (``PRAGMA foreign_keys = ON``).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from wsboggle.config import settings


SCHEMA = """
CREATE TABLE users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    handle        TEXT NOT NULL,
    handle_lower  TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    created_at    TEXT NOT NULL
);

CREATE TABLE invite_codes (
    code        TEXT PRIMARY KEY COLLATE NOCASE,
    label       TEXT,
    created_at  TEXT NOT NULL
);

CREATE TABLE sessions (
    id           TEXT PRIMARY KEY,
    user_id      INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at   TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    expires_at   TEXT NOT NULL
);
CREATE INDEX sessions_user_id    ON sessions(user_id);
CREATE INDEX sessions_expires_at ON sessions(expires_at);

CREATE TABLE clubs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    created_by  INTEGER REFERENCES users(id) ON DELETE SET NULL,
    created_at  TEXT NOT NULL
);

-- Insert-only join table: there is no leave-club UX in v1.
CREATE TABLE clubs_users (
    club_id    INTEGER NOT NULL REFERENCES clubs(id) ON DELETE CASCADE,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    joined_at  TEXT NOT NULL,
    PRIMARY KEY (club_id, user_id)
);
CREATE INDEX clubs_users_user_id ON clubs_users(user_id);

-- One game = one timed Boggle round inside a club (or solo, when
-- club_id IS NULL). config is a JSON blob holding every per-game knob
-- (dice set, scoring ladder, mode, dupes-cancel, timer params, good-
-- board constraints, etc.); see wsboggle.shared.GameConfig.
-- ends_at NULL means "untimed game" — server-side timer expiry only
-- fires when the column is populated.
CREATE TABLE games (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    club_id      INTEGER REFERENCES clubs(id) ON DELETE CASCADE,
    created_by   INTEGER REFERENCES users(id) ON DELETE SET NULL,
    config       TEXT NOT NULL,
    board        TEXT NOT NULL,
    legal_words  TEXT NOT NULL,
    started_at   TEXT NOT NULL,
    ends_at      TEXT,
    ended_at     TEXT
);
CREATE INDEX games_club_id    ON games(club_id);
CREATE INDEX games_created_by ON games(created_by);

-- Every submitted guess, including rejected ones — useful for review
-- and for showing the player their own list. Scoring (with
-- dupes-cancel applied) is computed at game-end / on review from
-- this raw log + the game's config, not stored.
--
-- UNIQUE(game_id, user_id, word) is the source of truth for "no
-- duplicates per user per game": ``submit_guess`` relies on the
-- INSERT to fail with IntegrityError when a duplicate slips past
-- its own SELECT (real possibility under concurrent submits, even
-- though SQLite serialises writes — the SELECT-then-INSERT pair is
-- not itself atomic in autocommit mode).
CREATE TABLE guesses (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    game_id   INTEGER NOT NULL REFERENCES games(id) ON DELETE CASCADE,
    user_id   INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    word      TEXT NOT NULL,
    is_legal  INTEGER NOT NULL,
    ts        TEXT NOT NULL,
    UNIQUE (game_id, user_id, word)
);
CREATE INDEX guesses_game_id ON guesses(game_id);

CREATE TABLE chat_messages (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    club_id   INTEGER NOT NULL REFERENCES clubs(id) ON DELETE CASCADE,
    user_id   INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    text      TEXT NOT NULL,
    ts        TEXT NOT NULL
);
CREATE INDEX chat_messages_club_id ON chat_messages(club_id, id);
"""


def _open(path: Path) -> sqlite3.Connection:
    """Open one configured sqlite3 connection — autocommit, WAL,
    foreign-keys, row factory. Used by both the lifespan (long-lived
    WS-side connection) and per-request HTTP handlers.

    ``check_same_thread=False`` is required because FastAPI sync
    handlers run on a threadpool worker; a connection opened on the
    event-loop thread would refuse use otherwise. Despite the flag,
    sqlite3 cursor state is **not** safe under concurrent use from
    multiple threads — see :func:`get_request_connection` for the
    per-request pattern that avoids this.
    """
    conn = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.row_factory = sqlite3.Row
    return conn


def connect() -> sqlite3.Connection:
    """Open the process-wide connection used by the WS layer + the
    startup recovery sweep.

    The WS side runs entirely on the event loop, so concurrent
    access through this single connection is impossible: every
    ``db.execute`` either runs to completion or yields, never
    interleaves with another thread's cursor. HTTP handlers must
    NOT use this connection — they're on a threadpool and a second
    request's call into the same cursor mid-step would corrupt
    sqlite3's internal state (SQLITE_MISUSE). Use
    :func:`get_request_connection` from the HTTP path instead.

    The schema is applied when the DB is *empty* — detected by
    looking for any user-defined table, not by checking the file's
    existence. That way an empty DB file (e.g. one ``tempfile``
    pre-created in tests) gets bootstrapped correctly.

    Trade-off of autocommit: **multi-statement operations are not
    atomic by default**. Code that needs grouped INSERTs / UPDATEs
    (e.g. ``clubs.create_club``) must wrap its work in ``with conn:``
    so sqlite issues an explicit BEGIN/COMMIT.
    """
    path = Path(settings.db_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    conn = _open(path)
    has_schema = (
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='users'"
        ).fetchone()
        is not None
    )
    if not has_schema:
        conn.executescript(SCHEMA)

    return conn


def get_request_connection() -> sqlite3.Connection:
    """Open a fresh connection for one HTTP request — caller closes.

    The per-request pattern sidesteps the shared-connection thread
    race entirely: each handler gets its own cursor space. sqlite
    open is a few syscalls and reading the shared WAL file pages
    that the OS already has cached, so the overhead is negligible
    at the scale of a friend-group app.

    Schema bootstrap is deliberately skipped here — it should have
    already run via :func:`connect` at process startup.
    """
    return _open(Path(settings.db_path))
