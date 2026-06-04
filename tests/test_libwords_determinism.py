"""Determinism check for libwords across C-code refactors.

Each profile fingerprint is a SHA-256 prefix over the first 10
boards' raw dice + sorted legal-word lists. A pure-perf refactor
(arena allocator, hash-table dedup, dead-code removal) **must not
change** any of these digests; if it does, the change altered the
solver's output and that's a regression to investigate.

If you intentionally change behavior (a new score ladder, a
different shuffle algorithm, a DAWG rebuild), update the
expected values here and note why in the commit message.

Captured against ``c/libwords.c`` at commit c7eadf3 (the bench
baseline), built with ``make lib`` on macOS arm64. Seeds and
constraints come from ``bench/bench_libwords.py``.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Make the bench module importable.
sys.path.insert(0, str(Path(__file__).parent.parent / "bench"))

from bench_libwords import PROFILES, fingerprint  # noqa: E402


EXPECTED_FINGERPRINTS = {
    "loose":     "6b2dd39f5b530f60",
    "medium":    "1217c9e4e8723f94",
    "tight":     "670400ea89ac324d",
    "extreme":   "054b4602b27771e0",
    "5x5-loose": "5654139ede1c0112",
    "5x5-tight": "25c501d9bf04400e",
}


@pytest.mark.parametrize("profile_name", list(PROFILES))
def test_fingerprint_stable(profile_name: str) -> None:
    """The first 10 boards per profile (deterministic seeds) hash
    to the captured baseline. Mismatch = libwords changed meaning."""
    profile = PROFILES[profile_name]
    expected = EXPECTED_FINGERPRINTS[profile_name]
    actual = fingerprint(profile)
    assert actual == expected, (
        f"libwords fingerprint changed for profile {profile_name!r}: "
        f"expected {expected}, got {actual}. "
        "If the change is intentional, update EXPECTED_FINGERPRINTS."
    )
