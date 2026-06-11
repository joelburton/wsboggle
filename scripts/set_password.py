#!/usr/bin/env python3
"""Reset a user's password from the command line.

Usage:
    python scripts/set_password.py --db path/to/wsboggle.sqlite3 HANDLE PASSWORD

Looks up the user by handle (case-insensitive), hashes the supplied
password with the same scrypt parameters the app uses, and writes the
new hash to the ``users`` row. Exits non-zero if the handle is not
found or the password is shorter than ``MIN_PASSWORD_LENGTH``.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

# Allow running as a plain script (no install) from the repo root.
_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "src"))

from wsboggle.auth import MIN_PASSWORD_LENGTH, hash_password  # noqa: E402


def parse_args() -> argparse.Namespace:
    """Parse CLI arguments."""
    p = argparse.ArgumentParser(description="Reset a wsboggle user's password.")
    p.add_argument("--db", required=True, type=Path, help="Path to the SQLite DB file.")
    p.add_argument("username", help="User handle (case-insensitive).")
    p.add_argument("password", help="New password (>= 6 chars).")
    return p.parse_args()


def main() -> int:
    """Entry point. Returns a process exit code."""
    args = parse_args()

    if len(args.password) < MIN_PASSWORD_LENGTH:
        print(
            f"error: password must be at least {MIN_PASSWORD_LENGTH} characters",
            file=sys.stderr,
        )
        return 2

    if not args.db.exists():
        print(f"error: db not found: {args.db}", file=sys.stderr)
        return 2

    handle_lower = args.username.strip().lower()
    new_hash = hash_password(args.password)

    db = sqlite3.connect(args.db)
    try:
        db.execute("PRAGMA foreign_keys = ON")
        cur = db.execute(
            "UPDATE users SET password_hash = ? WHERE handle_lower = ?",
            (new_hash, handle_lower),
        )
        if cur.rowcount == 0:
            print(f"error: no user with handle {args.username!r}", file=sys.stderr)
            db.rollback()
            return 1
        db.commit()
    finally:
        db.close()

    print(f"password updated for {args.username}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
