"""Smoke test for the libwords C-bridge.

Skipped (not failed) when the native library or word file isn't
present locally — that way ``pytest`` is green for anyone who hasn't
run ``make bootstrap && make lib`` yet, but the bridge is exercised
end-to-end as soon as those artifacts exist.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

# Match wsboggle.config's default before importing the package.
os.environ.setdefault("WSBOGGLE_DEV", "1")

from wsboggle import dice  # noqa: E402
from wsboggle.config import settings  # noqa: E402

_DATA = Path(settings.data_dir)

_assets_present = (_DATA / "libwords.so").exists() and (_DATA / "words.dat").exists()
pytestmark = pytest.mark.skipif(
    not _assets_present,
    reason=(
        f"libwords.so and/or words.dat missing under {_DATA}; "
        "run `make bootstrap && make lib` to enable this test."
    ),
)


def test_generate_4x4_board() -> None:
    """A 4×4 board generates with at least a handful of legal words."""
    from wsboggle import libwords

    result = libwords.generate_board(dice.get_by_name("4"))
    assert len(result.raw_board) == 16
    assert len(result.legal_words) > 10  # any reasonable board
    assert all(len(w) >= 3 for w in result.legal_words)
    # Words are lowercase, sorted, unique.
    assert result.legal_words == sorted(set(result.legal_words))


def test_generate_5x5_board() -> None:
    """5×5 dice set produces a 25-char board."""
    from wsboggle import libwords

    result = libwords.generate_board(dice.get_by_name("5"))
    assert len(result.raw_board) == 25
    assert len(result.legal_words) > 20


def test_generate_with_constraint() -> None:
    """Min-words constraint forces a denser board."""
    from wsboggle import libwords

    result = libwords.generate_board(dice.get_by_name("4"), min_words=80)
    assert len(result.legal_words) >= 80


def test_special_face_decoding() -> None:
    """Boards containing a ``1`` decode to a display grid with ``Qu``."""
    # Pick a set where ``1`` (Qu) appears on multiple dice so it's
    # very likely to land. Just sanity-check the display mapping
    # given any board with a special.
    grid = dice.board_to_display("1AAAAAAAAAAAAAAA", 4)
    assert grid[0][0] == "Qu"
    assert grid[0][1] == "A"
