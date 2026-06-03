"""Scoring ladders, ported from ``tboggle/src/tboggle/chooser.py``.

A *ladder* maps word length → points. Ladders are 17-element tuples
(``ladder[n]`` is the score for a word of length ``n``); index 0–2 are
zero so the table is index-by-length. Words longer than 16 chars are
treated as length 16 — Boggle boards top out well below that.

Only ``"basic"`` is exposed in v1 (default in
:class:`wsboggle.shared.GameConfig`); the others are wired in for the
deferred "pick your ladder" UI.
"""

from __future__ import annotations

# Ladder length must cover the longest word libwords might return + 1
# index for the 0-padding at the short end.
_LADDER_LEN = 17

# (0, 0, 0, ...): index 0–2 score 0 — the minimum-legal-length filter
# in libwords excludes shorter words anyway.

LADDERS: dict[str, tuple[int, ...]] = {
    "flat":    (0, 0, 0, 1, 1, 1, 1,  1,  1,  1,  1,  1,  1,  1,   1,   1,   1),
    "basic":   (0, 0, 0, 1, 1, 2, 3,  5, 11, 11, 11, 11, 11, 11,  11,  11,  11),
    "fib":     (0, 0, 0, 1, 1, 2, 3,  5,  8, 13, 21, 34, 55, 89, 144, 233, 377),
    "big":     (0, 0, 0, 1, 1, 2, 4,  6,  9, 12, 16, 20, 25, 30,  36,  42,  50),
}

# Sanity-check at import — every ladder is the expected length.
for _name, _ladder in LADDERS.items():
    assert len(_ladder) == _LADDER_LEN, f"{_name!r} ladder is wrong length"


def score_word(word: str, ladder_name: str) -> int:
    """Return the point value of a single word under the named ladder.

    Words longer than the ladder are clamped to its top index. Unknown
    ladder names raise :class:`KeyError`.
    """
    ladder = LADDERS[ladder_name]
    idx = min(len(word), len(ladder) - 1)
    return ladder[idx]


def is_known(name: str) -> bool:
    """Predicate for config validation."""
    return name in LADDERS
