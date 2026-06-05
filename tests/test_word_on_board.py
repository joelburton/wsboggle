"""Unit tests for the adjacency walker in :func:`games._word_on_board`.

These don't need a server or a real DB — they build a tiny
:class:`GameState` directly and ask whether a target word can be
traced on the constructed board. Covers the case the audit
flagged: a real word that *is* traceable but not in the DAWG must
not be labelled ``not_on_board``.
"""

from __future__ import annotations

from datetime import UTC, datetime

from wsboggle import games
from wsboggle.shared import GameConfig


def _state(board_raw: str, num: int = 4) -> games.GameState:
    """Build a minimal GameState whose only used fields are
    ``board_raw`` and ``config.dice_set`` (the adjacency walker
    only reads those)."""
    return games.GameState(
        id=1,
        club_id=None,
        created_by=1,
        config=GameConfig(dice_set=str(num)),
        board_raw=board_raw,
        legal_words=frozenset(),
        started_at=datetime.now(UTC),
        ends_at=None,
        ended_at=None,
    )


# A 4×4 board with letters laid out as:
#
#   A B C D
#   E F G H
#   I J K L
#   M N O P
#
# Useful because horizontal/vertical/diagonal paths are easy to
# read off by hand.
_LINEAR_BOARD = "ABCDEFGHIJKLMNOP"


def test_horizontal_path() -> None:
    s = _state(_LINEAR_BOARD)
    assert games._word_on_board("abc", s) is True
    assert games._word_on_board("abcd", s) is True


def test_diagonal_path() -> None:
    # A, F, K, P run down the main diagonal.
    s = _state(_LINEAR_BOARD)
    assert games._word_on_board("afkp", s) is True


def test_l_shaped_path() -> None:
    # A, E, F, G — left edge, then right along row 2.
    s = _state(_LINEAR_BOARD)
    assert games._word_on_board("aefg", s) is True


def test_no_repeat_tile() -> None:
    # "ABA" would need to reuse the A tile.
    s = _state(_LINEAR_BOARD)
    assert games._word_on_board("aba", s) is False


def test_non_adjacent_letters() -> None:
    # A and C are not adjacent (B sits between them); without going
    # through B, "AC" can't be traced.
    s = _state(_LINEAR_BOARD)
    assert games._word_on_board("ad", s) is False


def test_word_uses_multiface_tile() -> None:
    # Single multi-face "Qu" tile encoded as '1' in raw form. The
    # board has Q-U at (0,0), then E E N on row 0.
    #   Qu E E N
    #   A  B C D
    #   E  F G H
    #   I  J K L
    board = "1EEN" "ABCD" "EFGH" "IJKL"
    s = _state(board)
    # "QUEEN" should be traceable: Qu tile (counts as Q+U), then
    # the two E tiles in (0,1) and (0,2), then N at (0,3).
    assert games._word_on_board("queen", s) is True
    # "QU" alone — just the multi-face tile — also a valid path.
    assert games._word_on_board("qu", s) is True


def test_empty_word() -> None:
    s = _state(_LINEAR_BOARD)
    # Empty string is vacuously traceable; the walker terminates
    # immediately. (submit_guess would have caught it as
    # too_short, but the walker itself shouldn't crash.)
    assert games._word_on_board("", s) is True


# --- submit_guess classification ------------------------------------------


import json  # noqa: E402
import sqlite3  # noqa: E402

import pytest  # noqa: E402

from wsboggle import dictionary  # noqa: E402
from wsboggle.shared import GameConfig  # noqa: E402


def _persist_state(db: sqlite3.Connection, board_raw: str, legal_words: list[str]) -> games.GameState:
    """Insert a games row whose ``legal_words`` we explicitly
    control, so we can simulate "this word isn't in our DAWG"
    without needing a real DAWG miss to hit. Also seeds the two
    user rows the guesses-table FK requires (the submit_guess
    INSERT would otherwise fail with IntegrityError, which the
    same handler treats as ``already_submitted`` and mask the
    real failure)."""
    now = datetime.now(UTC).isoformat()
    for uid in (1, 2):
        db.execute(
            "INSERT INTO users (id, handle, handle_lower, password_hash, created_at) "
            "VALUES (?, ?, ?, 'x', ?)",
            (uid, f"user{uid}", f"user{uid}", now),
        )
    config = GameConfig(dice_set="4")
    cur = db.execute(
        """
        INSERT INTO games
            (club_id, created_by, config, board, legal_words,
             started_at, ends_at, ended_at)
        VALUES (NULL, NULL, ?, ?, ?, ?, NULL, NULL)
        """,
        (
            config.model_dump_json(),
            board_raw,
            json.dumps(legal_words),
            now,
        ),
    )
    state = games.find_game(db, cur.lastrowid)  # type: ignore[arg-type]
    assert state is not None
    return state


def test_classifier_traceable_in_dict_but_not_in_dawg(
    db: sqlite3.Connection,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The bug Joel reported: a word that *is* on the board and
    *is* a real word but isn't in our DAWG must label as
    ``not_in_word_list``, not ``not_on_board``."""
    # ABCD across row 0; legal_words is empty (i.e., our DAWG
    # doesn't have "abcd"). Force dictionary.define to say "yes,
    # ABCD is a word."
    monkeypatch.setattr(dictionary, "define", lambda _w: "fake def")
    state = _persist_state(db, _LINEAR_BOARD, legal_words=[])

    outcome = games.submit_guess(db, state, user_id=1, word="abcd")
    assert outcome.result == "not_in_word_list"


def test_classifier_in_dict_but_not_traceable(
    db: sqlite3.Connection,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the word *is* a real word but the user *can't*
    actually trace it on this board, ``not_on_board`` is the
    correct label."""
    monkeypatch.setattr(dictionary, "define", lambda _w: "fake def")
    state = _persist_state(db, _LINEAR_BOARD, legal_words=[])

    # "XYZ" isn't on a board that only contains A–P.
    outcome = games.submit_guess(db, state, user_id=1, word="xyz")
    assert outcome.result == "not_on_board"


def test_classifier_not_in_dict_falls_back_to_not_a_word(
    db: sqlite3.Connection,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If the dictionary doesn't have the word, the on_board
    distinction doesn't matter — it's just ``not_a_word`` either
    way."""
    monkeypatch.setattr(dictionary, "define", lambda _w: None)
    state = _persist_state(db, _LINEAR_BOARD, legal_words=[])

    # Traceable but not in dict.
    outcome = games.submit_guess(db, state, user_id=1, word="abcd")
    assert outcome.result == "not_a_word"

    # Not traceable and not in dict.
    outcome = games.submit_guess(db, state, user_id=2, word="xyz")
    assert outcome.result == "not_a_word"
