"""Word-definition lookup against ``all.sqlite3`` (the dictionary that
ships with tboggle).

Read-only sqlite, opened lazily on first call and cached for the
process lifetime. When the file isn't present (e.g. tests that
haven't run ``make bootstrap``) every lookup returns ``None`` — no
error — so the absence of definitions degrades gracefully rather
than failing routes.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from threading import Lock

from wsboggle.config import settings


_lock = Lock()
_conn: sqlite3.Connection | None = None
_missing = False


def _get_conn() -> sqlite3.Connection | None:
    """Open ``all.sqlite3`` (read-only) on first call, cache after.

    Returns ``None`` and remembers that fact if the file's missing
    so subsequent calls are cheap (no statting / no re-attempt)."""
    global _conn, _missing
    with _lock:
        if _conn is not None:
            return _conn
        if _missing:
            return None
        path = Path(settings.data_dir) / "all.sqlite3"
        if not path.exists():
            _missing = True
            return None
        # Read-only via URI; check_same_thread=False so FastAPI
        # workers running on the threadpool can share the handle.
        uri = f"file:{path}?mode=ro"
        _conn = sqlite3.connect(uri, uri=True, check_same_thread=False)
        return _conn


def define(word: str) -> str | None:
    """Return the dictionary definition for ``word``, or ``None``
    if the word isn't in the dictionary (or the dictionary file
    isn't installed).

    Lookup is uppercase — the ``defs`` table in tboggle's
    ``all.sqlite3`` stores keys uppercased.
    """
    conn = _get_conn()
    if conn is None:
        return None
    row = conn.execute(
        "SELECT def FROM defs WHERE word = ?", (word.upper(),)
    ).fetchone()
    return None if row is None else row[0]
