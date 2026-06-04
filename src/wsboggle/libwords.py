"""ctypes binding for ``libwords.so`` — the C solver ported from
tboggle.

The C library exports two functions we use:

- ``read_dawg(path)`` — load the DAWG word list (``words.dat``).
  Idempotent; call once at startup.
- ``get_words(...)`` — generate a random board (filling dice randomly
  and optionally rejection-sampling to meet min/max constraints) and
  return its full legal-word list.

The C source has no ``restore_game`` — we cannot recompute a known
board's word list. Persist the list at game creation instead (see
``games.legal_words`` in :mod:`wsboggle.db`).

The shared library is expected at ``{DATA_DIR}/libwords.so``;
``words.dat`` at ``{DATA_DIR}/words.dat``. Both ``DATA_DIR``
defaults to ``./data`` and is overridable via env (see
:mod:`wsboggle.config`).
"""

from __future__ import annotations

import ctypes
import os
from ctypes import POINTER, byref, c_char_p, c_int, c_void_p
from dataclasses import dataclass
from pathlib import Path
from random import randint

from wsboggle.config import settings
from wsboggle.dice import DiceSet
from wsboggle.scoring import LADDERS


@dataclass(frozen=True)
class GeneratedBoard:
    """What :func:`generate_board` returns.

    - ``raw_board`` is the row-major dice-character string straight from
      the C solver (specials encoded as ``1``–``6``).
    - ``legal_words`` is the lowercase, sorted list of every word the
      solver found at or above ``min_legal_length``.
    - ``tries`` is the number of rejection-sample iterations the solver
      ran; mostly useful for diagnostics ("did our constraints make this
      slow?").
    """

    raw_board: str
    legal_words: list[str]
    tries: int


# ---------------------------------------------------------------------------
# Library loading
# ---------------------------------------------------------------------------


def _data_path(filename: str) -> Path:
    """Resolve a path inside the configured data directory."""
    return Path(settings.data_dir) / filename


def _load_library() -> ctypes.CDLL:
    """Locate and dlopen libwords.so, then set the C function signatures."""
    so_path = _data_path("libwords.so")
    if not so_path.exists():
        raise RuntimeError(
            f"libwords.so not found at {so_path}. Run `make lib` to build it."
        )
    lib = ctypes.cdll.LoadLibrary(os.fspath(so_path))

    lib.read_dawg.argtypes = [c_char_p]
    lib.read_dawg.restype = None

    lib.get_words.restype = POINTER(c_char_p)
    # argtypes set per-call because the dice / score arrays are
    # variable-length (depends on the dice set and ladder).

    lib.free_words.argtypes = [POINTER(c_char_p), c_char_p, c_void_p]
    lib.free_words.restype = None

    return lib


_lib: ctypes.CDLL | None = None
_dawg_loaded = False


def _ensure_loaded() -> ctypes.CDLL:
    """Lazy-load the shared library + DAWG on first use.

    Boards are generated infrequently (on game start), so the lazy
    init keeps the import cheap; tests and tools that don't touch
    libwords don't need the .so present.
    """
    global _lib, _dawg_loaded
    if _lib is None:
        _lib = _load_library()
    if not _dawg_loaded:
        dawg_path = _data_path("words.dat")
        if not dawg_path.exists():
            raise RuntimeError(
                f"words.dat not found at {dawg_path}. "
                "Run `make bootstrap` to copy it from tboggle."
            )
        _lib.read_dawg(os.fspath(dawg_path).encode("utf-8"))
        _dawg_loaded = True
    return _lib


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def generate_board(
    dice_set: DiceSet,
    *,
    scoring_ladder: str = "basic",
    min_legal_length: int = 3,
    min_words: int | None = None,
    max_words: int | None = None,
    min_score: int | None = None,
    max_score: int | None = None,
    min_longest: int | None = None,
    max_longest: int | None = None,
    max_tries: int = 10_000,
    random_seed: int | None = None,
) -> GeneratedBoard:
    """Generate a fresh random board using the C solver.

    All ``min_*`` / ``max_*`` arguments are rejection-sampling
    constraints; ``None`` means "no constraint" (mapped to the
    sentinel values the C side expects: 0 for ``min_*``, -1 for
    ``max_*``).

    Raises ``RuntimeError`` if the solver gives up after ``max_tries``
    iterations (constraints too tight).
    """
    lib = _ensure_loaded()

    if random_seed is None:
        random_seed = randint(0, 2**31 - 1)

    dice_bytes = [face.encode("utf-8") for face in dice_set.dice]
    dice_arr = (c_char_p * len(dice_bytes))(*dice_bytes)

    ladder = LADDERS[scoring_ladder]
    score_arr = (c_int * len(ladder))(*ladder)

    tries = c_int(0)
    raw_board_buf = c_char_p()
    board_handle = c_void_p()

    words_p = lib.get_words(
        dice_arr,
        score_arr,
        c_int(dice_set.num),
        c_int(dice_set.num),
        c_int(0 if min_words is None else min_words),
        c_int(-1 if max_words is None else max_words),
        c_int(0 if min_score is None else min_score),
        c_int(-1 if max_score is None else max_score),
        c_int(0 if min_longest is None else min_longest),
        c_int(-1 if max_longest is None else max_longest),
        c_int(min_legal_length),
        c_int(max_tries),
        c_int(random_seed),
        byref(tries),
        byref(raw_board_buf),
        byref(board_handle),
    )

    try:
        if not raw_board_buf.value:
            raise RuntimeError(
                f"libwords.get_words returned no board after {tries.value} "
                "tries; constraints may be too tight."
            )

        raw_board = raw_board_buf.value.decode("utf-8")

        # Walk the null-terminated word array. Each entry is a
        # pointer into the C-side arena; the decode below copies the
        # bytes into Python-owned strings before the arena dies.
        words: list[str] = []
        i = 0
        while words_p[i]:
            words.append(words_p[i].decode("utf-8").lower())
            i += 1
    finally:
        # Releases the arena (and with it every word's bytes), the
        # array itself, the dice-simple buffer, and the Board
        # metadata. After this call every pointer above is dead.
        lib.free_words(words_p, raw_board_buf, board_handle)

    return GeneratedBoard(
        raw_board=raw_board,
        legal_words=sorted(words),
        tries=tries.value,
    )
