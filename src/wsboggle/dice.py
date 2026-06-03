"""Dice-set registry, ported verbatim from ``tboggle/src/tboggle/dice.py``.

Each :class:`DiceSet` is a named bag of six-sided dice; ``num`` is the
board side length (so ``num=4`` means a 4×4 board with 16 dice).
Special faces are encoded as digits and decoded for display:

============  ===========
char on die   display
============  ===========
``1``         ``Qu``
``2``         ``In``
``3``         ``Th``
``4``         ``Er``
``5``         ``He``
``6``         ``An``
other         the char itself
============  ===========

The C solver only sees the raw chars; we expand to display strings on
the way out to the client.
"""

from __future__ import annotations

from dataclasses import dataclass


_SPECIAL_FACES: dict[str, str] = {
    "1": "Qu",
    "2": "In",
    "3": "Th",
    "4": "Er",
    "5": "He",
    "6": "An",
}


@dataclass(frozen=True)
class DiceSet:
    """One named bag of dice. ``num`` is the board side length."""

    name: str
    desc: str
    num: int
    dice: tuple[str, ...]

    @property
    def board_size(self) -> int:
        """Number of dice == ``num * num`` for a square board."""
        return self.num * self.num


def face_to_display(face: str) -> str:
    """Map a single raw die char to the player-facing display string."""
    return _SPECIAL_FACES.get(face, face)


def board_to_display(board: str, num: int) -> list[list[str]]:
    """Turn a row-major raw board string into a 2-D display grid."""
    if len(board) != num * num:
        raise ValueError(f"board length {len(board)} != {num * num}")
    grid: list[list[str]] = []
    for y in range(num):
        row = [face_to_display(board[y * num + x]) for x in range(num)]
        grid.append(row)
    return grid


# Eight sets, in display order. ``name`` is the key used in
# :class:`wsboggle.shared.GameConfig.dice_set`.
SETS: list[DiceSet] = [
    DiceSet(
        "4-classic",
        "4×4 Classic",
        4,
        (
            "AACIOT", "ABILTY", "ABJMOQ", "ACDEMP",
            "ACELRS", "ADENVZ", "AHMORS", "BIFORX",
            "DENOSW", "DKNOTU", "EEFHIY", "EGKLUY",
            "EGINTV", "EHINPS", "ELPSTU", "GILRUW",
        ),
    ),
    DiceSet(
        "4",
        "4×4 Revised",
        4,
        (
            "AAEEGN", "ABBJOO", "ACHOPS", "AFFKPS",
            "AOOTTW", "CIMOTU", "DEILRX", "DELRVY",
            "DISTTY", "EEGHNW", "EEINSU", "EHRTVW",
            "EIOSST", "ELRTTY", "HIMNU1", "HLNNRZ",
        ),
    ),
    DiceSet(
        "5-orig",
        "5×5 Original",
        5,
        (
            "AAAFRS", "AAEEEE", "AAFIRS", "ADENNN", "AEEEEM",
            "AEEGMU", "AEGMNN", "AFIRSY", "BJK1XZ", "CCENST",
            "CEIILT", "CEIPST", "DDHNOT", "DHHLOR", "DHHLOR",
            "DHLNOR", "EIIITT", "CEILPT", "EMOTTT", "ENSSSU",
            "FIPRSY", "GORRVW", "IPRRRY", "NOOTUW", "OOOTTU",
        ),
    ),
    DiceSet(
        "5-challenge",
        "5×5 Challenge",
        5,
        (
            "AAAFRS", "AAEEEE", "AAFIRS", "ADENNN", "AEEEEM",
            "AEEGMU", "AEGMNN", "AFIRSY", "BJK1XZ", "CCENST",
            "CEIILT", "CEIPST", "DDHNOT", "DHHLOR", "IKLM1U",
            "DHLNOR", "EIIITT", "CEILPT", "EMOTTT", "ENSSSU",
            "FIPRSY", "GORRVW", "IPRRRY", "NOOTUW", "OOOTTU",
        ),
    ),
    DiceSet(
        "5-big-deluxe",
        "5×5 Big Deluxe",
        5,
        (
            "AAAFRS", "AAEEEE", "AAFIRS", "ADENNN", "AEEEEM",
            "AEEGMU", "AEGMNN", "AFIRSY", "BJK1XZ", "CCNSTW",
            "CEIILT", "CEIPST", "DDLNOR", "DHHLOR", "DHHNOT",
            "DHLNOR", "EIIITT", "CEILPT", "EMOTTT", "ENSSSU",
            "FIPRSY", "GORRVW", "HIPRRY", "NOOTUW", "OOOTTU",
        ),
    ),
    DiceSet(
        "5",
        "5×5 Big 2012",
        5,
        (
            "AAAFRS", "AAEEEE", "AAFIRS", "ADENNN", "AEEEEM",
            "AEEGMU", "AEGMNN", "AFIRSY", "BBJKXZ", "CCENST",
            "EIILST", "CEIPST", "DDHNOT", "DHHLOR", "DHHNOW",
            "DHLNOR", "EIIITT", "EILPST", "EMOTTT", "ENSSSU",
            "123456", "GORRVW", "IPRSYY", "NOOTUW", "OOOTTU",
        ),
    ),
    DiceSet(
        "6-super",
        "6×6 Super Big",
        6,
        (
            "AAAFRS", "AAEEEE", "AAEEOO", "AAFIRS", "ABDEIO", "ADENNN",
            "AEEEEM", "AEEGMU", "AEGMNN", "AEILMN", "AEINOU", "AFIRSY",
            "123456", "BBJKXZ", "CCENST", "CDDLNN", "CEIITT", "CEIPST",
            "CFGNUY", "DDHNOT", "DHHLOR", "DHHNOW", "DHLNOR", "EHILRS",
            "EIILST", "EILPST", "EIO000", "EMTTTO", "ENSSSU", "GORRVW",
            "HIRSTV", "HOPRST", "IPRSYY", "JK1WXZ", "NOOTUW", "OOOTTU",
        ),
    ),
    DiceSet(
        "6",
        "6×6 Super Big Simple",
        6,
        (
            "AAAFRS", "AAEEEE", "AAEEOO", "AAFIRS", "ABDEIO", "ADENNN",
            "AEEEEM", "AEEGMU", "AEGMNN", "AEILMN", "AEINOU", "AFIRSY",
            "AEIOUS", "BBJKXZ", "CCENST", "CDDLNN", "CEIITT", "CEIPST",
            "CFGNUY", "DDHNOT", "DHHLOR", "DHHNOW", "DHLNOR", "EHILRS",
            "EIILST", "EILPST", "EIOSSS", "EMTTTO", "ENSSSU", "GORRVW",
            "HIRSTV", "HOPRST", "IPRSYY", "JK1WXZ", "NOOTUW", "OOOTTU",
        ),
    ),
]

_BY_NAME: dict[str, DiceSet] = {s.name: s for s in SETS}


def get_by_name(name: str) -> DiceSet:
    """Look up a dice set by its key. Raises ``KeyError`` if unknown."""
    return _BY_NAME[name]


def is_known(name: str) -> bool:
    """Predicate form of :func:`get_by_name` for config validation."""
    return name in _BY_NAME
