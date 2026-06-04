"""Game lifecycle — transport-agnostic.

The functions here are the single implementation of the game logic
used by both the solo HTTP routes (now) and the multiplayer WS
handlers (next). The split keeps WS-specific concerns (broadcast
scope, per-socket state) out of the game's own state machine.

State machine:

- :func:`start_game` generates the board, persists, returns the
  initial :class:`GameState`.
- :func:`submit_guess` records one guess against an active game.
- :func:`end_game` marks the game ended (idempotent).
- :func:`build_result` computes the end-of-game payload from the DB
  (raw guesses + the config's scoring rules).

Authorization is *not* the game core's concern. Callers (route /
handler layer) check who's allowed to start a game in a given club
or who's allowed to submit a guess in a given game. The functions
here assume the caller has already vouched for the user.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from wsboggle import dice, dictionary, libwords, scoring
from wsboggle.shared import (
    ClubGameSummary,
    GameConfig,
    GameResult,
    GameSnapshot,
    GuessRecord,
    GuessResult,
    MissedWord,
    PlayerResult,
    PlayerScoreSummary,
    PlayerWordEntry,
    SoloGameSummary,
)


# --- Errors ----------------------------------------------------------------


class GameConfigError(ValueError):
    """Raised by :func:`validate_config` when the caller-supplied
    config has unknown keys / out-of-range values. Routes catch and
    translate to 400."""


# --- In-memory state -------------------------------------------------------


@dataclass(frozen=True)
class GameState:
    """A game's row, loaded and parsed into useful types.

    ``legal_words`` is a frozenset so membership checks during guess
    submission are O(1). ``config`` is the parsed Pydantic model, not
    the JSON string from the DB.
    """

    id: int
    club_id: int | None
    created_by: int | None
    config: GameConfig
    board_raw: str
    legal_words: frozenset[str]
    started_at: datetime
    ends_at: datetime | None
    ended_at: datetime | None


@dataclass(frozen=True)
class GuessOutcome:
    """What :func:`submit_guess` reports back to the caller. Routes
    translate this into the wire :class:`GuessResponse`."""

    result: GuessResult
    points: int


# --- Config validation -----------------------------------------------------


def validate_config(config: GameConfig) -> None:
    """Raise :class:`GameConfigError` if the config can't be played.

    Pydantic already validated shape and types; this is the
    domain-knowledge layer (does this dice set exist? is the timer
    positive?).
    """
    if not dice.is_known(config.dice_set):
        raise GameConfigError(f"unknown dice_set: {config.dice_set!r}")
    if not scoring.is_known(config.scoring_ladder):
        raise GameConfigError(f"unknown scoring_ladder: {config.scoring_ladder!r}")
    if config.min_legal_length < 2:
        raise GameConfigError("min_legal_length must be at least 2")
    if config.timer_seconds is not None and config.timer_seconds < 1:
        raise GameConfigError("timer_seconds must be a positive integer or null")


# --- Time helpers ----------------------------------------------------------


def _now() -> datetime:
    """UTC now. Single helper so tests can monkeypatch consistently."""
    return datetime.now(UTC)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _parse_iso(s: str | None) -> datetime | None:
    return None if s is None else datetime.fromisoformat(s)


# --- Row → state ----------------------------------------------------------


def _row_to_state(row: sqlite3.Row) -> GameState:
    """Inflate a games row. ``config`` and ``legal_words`` get parsed
    from their JSON columns; ``legal_words`` becomes a frozenset for
    O(1) membership."""
    return GameState(
        id=row["id"],
        club_id=row["club_id"],
        created_by=row["created_by"],
        config=GameConfig.model_validate_json(row["config"]),
        board_raw=row["board"],
        legal_words=frozenset(json.loads(row["legal_words"])),
        started_at=datetime.fromisoformat(row["started_at"]),
        ends_at=_parse_iso(row["ends_at"]),
        ended_at=_parse_iso(row["ended_at"]),
    )


def find_game(db: sqlite3.Connection, game_id: int) -> GameState | None:
    """Load a game by id, or return ``None``."""
    row = db.execute(
        "SELECT id, club_id, created_by, config, board, legal_words, "
        "started_at, ends_at, ended_at FROM games WHERE id = ?",
        (game_id,),
    ).fetchone()
    return None if row is None else _row_to_state(row)


def find_last_club_config(
    db: sqlite3.Connection, club_id: int
) -> GameConfig | None:
    """The config of the club's most recently-started game, or
    ``None`` when the club has never played.

    Surfaces in ``clubState.last_config`` so the new-game dialog
    can pre-fill from "what we played last" and the club view can
    offer a one-click "Play again" shortcut. CLAUDE.md notes that
    last-config-per-club is derived at read time from the games
    table — no separate config column on ``clubs``.
    """
    row = db.execute(
        "SELECT config FROM games WHERE club_id = ? "
        "ORDER BY id DESC LIMIT 1",
        (club_id,),
    ).fetchone()
    if row is None:
        return None
    return GameConfig.model_validate_json(row["config"])


def find_unended_games(db: sqlite3.Connection) -> list[GameState]:
    """Every game whose ``ended_at`` is still NULL across all clubs.

    Used at server startup to recover from a restart that interrupted
    one or more active games — the in-memory timer tasks are gone,
    so we either re-schedule them (timer still in the future) or
    sweep them shut (timer already expired). Untimed games stay
    open and wait for a manual ``endGame``.
    """
    rows = db.execute(
        "SELECT id, club_id, created_by, config, board, legal_words, "
        "started_at, ends_at, ended_at FROM games "
        "WHERE ended_at IS NULL "
        "ORDER BY id ASC"
    ).fetchall()
    return [_row_to_state(row) for row in rows]


def find_active_club_game(
    db: sqlite3.Connection, club_id: int
) -> GameState | None:
    """The currently-active game for one club, if any.

    "Active" = ``ended_at IS NULL`` — the live one, regardless of
    whether the timer has elapsed. Callers should also call
    :func:`is_active` if they need to distinguish "running" from
    "timer past but ``gameEnded`` not yet broadcast". The
    multiplayer WS uses this on connect to populate
    ``clubState.current_game`` for mid-game reconnects.
    """
    row = db.execute(
        "SELECT id, club_id, created_by, config, board, legal_words, "
        "started_at, ends_at, ended_at FROM games "
        "WHERE club_id = ? AND ended_at IS NULL "
        "ORDER BY id DESC LIMIT 1",
        (club_id,),
    ).fetchone()
    return None if row is None else _row_to_state(row)


# --- Lifecycle -------------------------------------------------------------


def start_game(
    db: sqlite3.Connection,
    *,
    club_id: int | None,
    created_by: int,
    config: GameConfig,
) -> GameState:
    """Generate a board with libwords, persist, return the new game.

    ``club_id=None`` denotes a solo game. ``created_by`` is always
    set (we always know who clicked New Game). Caller has already
    called :func:`validate_config` and done authorization.
    """
    dice_set = dice.get_by_name(config.dice_set)
    generated = libwords.generate_board(
        dice_set,
        scoring_ladder=config.scoring_ladder,
        min_legal_length=config.min_legal_length,
        min_words=config.min_words,
        max_words=config.max_words,
        min_score=config.min_score,
        max_score=config.max_score,
        min_longest=config.min_longest,
        max_longest=config.max_longest,
    )

    started_at = _now()
    ends_at: datetime | None = None
    if config.timer_seconds is not None:
        ends_at = started_at + timedelta(seconds=config.timer_seconds)

    cur = db.execute(
        """
        INSERT INTO games
            (club_id, created_by, config, board, legal_words, started_at, ends_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            club_id,
            created_by,
            config.model_dump_json(),
            generated.raw_board,
            json.dumps(generated.legal_words),
            _iso(started_at),
            None if ends_at is None else _iso(ends_at),
        ),
    )
    assert cur.lastrowid is not None
    return GameState(
        id=cur.lastrowid,
        club_id=club_id,
        created_by=created_by,
        config=config,
        board_raw=generated.raw_board,
        legal_words=frozenset(generated.legal_words),
        started_at=started_at,
        ends_at=ends_at,
        ended_at=None,
    )


def is_active(game: GameState, now: datetime | None = None) -> bool:
    """True if guesses are still being accepted: not ended, and
    (if timed) the timer hasn't elapsed."""
    if game.ended_at is not None:
        return False
    if game.ends_at is not None and (now or _now()) >= game.ends_at:
        return False
    return True


def submit_guess(
    db: sqlite3.Connection,
    game: GameState,
    *,
    user_id: int,
    word: str,
) -> GuessOutcome:
    """Record one guess for one user, returning the outcome.

    Authorization (caller is allowed to play this game) is the
    caller's responsibility. Liveness — is the game still active? —
    is **ours**: any code path that calls us, including the future
    WS handlers, gets the same activity check for free, and the
    time-of-check / time-of-use gap stays small.

    Behavior:

    - Early-out with ``"game_inactive"`` if the game has ended or
      its timer has elapsed.
    - The word is lowercased + stripped before comparison.
    - **Duplicates are detected per-user.** A fast SELECT covers the
      common "user re-submits" path; the table's UNIQUE constraint
      is the backstop for concurrent submits that race past the
      SELECT (IntegrityError → ``"already_submitted"``).
    - Otherwise the guess is recorded with ``is_legal=1`` iff the
      lowercased word is in :data:`game.legal_words`. Legal guesses
      get points from the configured ladder; illegal ones get 0.
    """
    if not is_active(game):
        return GuessOutcome(result="game_inactive", points=0)

    normalized = word.strip().lower()

    existing = db.execute(
        "SELECT 1 FROM guesses WHERE game_id = ? AND user_id = ? AND word = ?",
        (game.id, user_id, normalized),
    ).fetchone()
    if existing is not None:
        return GuessOutcome(result="already_submitted", points=0)

    is_legal = normalized in game.legal_words
    try:
        db.execute(
            "INSERT INTO guesses (game_id, user_id, word, is_legal, ts) "
            "VALUES (?, ?, ?, ?, ?)",
            (game.id, user_id, normalized, 1 if is_legal else 0, _iso(_now())),
        )
    except sqlite3.IntegrityError:
        # A concurrent submit beat us past the SELECT. UNIQUE
        # constraint is the source of truth; treat as a duplicate.
        return GuessOutcome(result="already_submitted", points=0)

    if is_legal:
        points = scoring.score_word(normalized, game.config.scoring_ladder)
        return GuessOutcome(result="accepted", points=points)

    # Illegal — classify so the client can show the right hint.
    # Length comes first because it's the cheapest check and is a
    # cleaner UX message than "not on board" for a 2-letter real word.
    if len(normalized) < game.config.min_legal_length:
        return GuessOutcome(result="too_short", points=0)
    # Dictionary lookup distinguishes "real word, just not on this
    # board" from "not a word at all". When the dictionary file
    # isn't installed (some test envs) ``define`` returns None for
    # everything; everything illegal collapses to ``not_a_word``,
    # which is the same imprecise feedback we had before.
    if dictionary.define(normalized) is not None:
        return GuessOutcome(result="not_on_board", points=0)
    return GuessOutcome(result="not_a_word", points=0)


def end_game(db: sqlite3.Connection, game: GameState) -> GameState:
    """Stamp ``ended_at = now`` if not already ended. Idempotent —
    returns the game's state either way."""
    if game.ended_at is not None:
        return game
    ended_at = _now()
    db.execute(
        "UPDATE games SET ended_at = ? WHERE id = ?",
        (_iso(ended_at), game.id),
    )
    return GameState(
        id=game.id,
        club_id=game.club_id,
        created_by=game.created_by,
        config=game.config,
        board_raw=game.board_raw,
        legal_words=game.legal_words,
        started_at=game.started_at,
        ends_at=game.ends_at,
        ended_at=ended_at,
    )


# --- Snapshot / history readers --------------------------------------------


def to_snapshot(
    db: sqlite3.Connection,
    state: GameState,
    *,
    viewer_user_id: int,
) -> GameSnapshot:
    """Build the wire :class:`GameSnapshot` for one viewer.

    ``viewer_user_id`` shapes ``your_guesses`` — for multiplayer the
    same game produces a different snapshot per viewer (each sees
    only their own list, per competitive rules).
    """
    dice_set = dice.get_by_name(state.config.dice_set)
    return GameSnapshot(
        game_id=state.id,
        board=dice.board_to_display(state.board_raw, dice_set.num),
        config=state.config,
        started_at=_iso(state.started_at),
        ends_at=None if state.ends_at is None else _iso(state.ends_at),
        ended_at=None if state.ended_at is None else _iso(state.ended_at),
        server_now=_iso(_now()),
        your_guesses=user_guesses(
            db,
            game_id=state.id,
            user_id=viewer_user_id,
            scoring_ladder=state.config.scoring_ladder,
        ),
    )


def user_guesses(
    db: sqlite3.Connection,
    *,
    game_id: int,
    user_id: int,
    scoring_ladder: str,
) -> list[GuessRecord]:
    """All guesses one user has submitted in one game, in submission
    order. Used to populate ``GameSnapshot.your_guesses`` so
    rejoiners see their own history."""
    rows = db.execute(
        "SELECT word, is_legal FROM guesses WHERE game_id = ? AND user_id = ? "
        "ORDER BY id ASC",
        (game_id, user_id),
    ).fetchall()
    out: list[GuessRecord] = []
    for row in rows:
        is_legal = bool(row["is_legal"])
        points = scoring.score_word(row["word"], scoring_ladder) if is_legal else 0
        out.append(GuessRecord(word=row["word"], is_legal=is_legal, points=points))
    return out


# --- End-of-game scoring --------------------------------------------------


def _expected_player_ids(db: sqlite3.Connection, game: GameState) -> list[int]:
    """Players who should appear in the result even if they never
    guessed. For solo: just the creator. For club: every club member
    at end-of-game time (membership is insert-only so this is stable).
    """
    if game.club_id is None:
        return [] if game.created_by is None else [game.created_by]
    rows = db.execute(
        "SELECT user_id FROM clubs_users WHERE club_id = ? ORDER BY user_id",
        (game.club_id,),
    ).fetchall()
    return [row["user_id"] for row in rows]


@dataclass(frozen=True)
class _ScoredWord:
    """One scored guess in a per-user breakdown.

    ``shared_with`` is the sorted list of *other* user_ids who also
    submitted this word; empty when the user is the only finder.
    ``final_points`` is ``0`` when ``shared_with`` is non-empty *and*
    the game's ``dupes_cancel`` is on, otherwise ``raw_points``.
    """

    word: str
    raw_points: int
    final_points: int
    shared_with: list[int]


@dataclass(frozen=True)
class _ScoredPlayer:
    """A user's full scoring breakdown for one game."""

    user_id: int
    handle: str
    words: list[_ScoredWord]
    raw_total: int
    final_total: int


@dataclass
class _PlayerBag:
    """Mutable scratch space during scoring — never escapes the
    module. Plain dataclass beats nested dicts for mypy's sake."""

    handle: str
    words: list[str] = field(default_factory=list)


def _score_players(
    db: sqlite3.Connection,
    *,
    game_id: int,
    scoring_ladder: str,
    dupes_cancel: bool,
    expected_user_ids: list[int],
) -> list[_ScoredPlayer]:
    """Shared scoring engine. Used by both :func:`build_result` (full
    breakdown) and :func:`list_club_games` (just the totals).

    Steps:

    1. Pull every legal guess for the game.
    2. Group by user; index each word to its set of finders.
    3. Add zero-guess entries for every ``expected_user_ids`` not
       already present, so club leaderboards always show all members.
    4. Score each user's words with the dupes-cancel rule applied.

    Returns one :class:`_ScoredPlayer` per user. Order is whatever
    the per-user iteration produced (insertion order from the
    grouping pass); callers that need a leaderboard sort do that
    themselves.
    """
    rows = db.execute(
        """
        SELECT g.user_id AS user_id, g.word AS word, u.handle AS handle
        FROM guesses g
        JOIN users u ON u.id = g.user_id
        WHERE g.game_id = ? AND g.is_legal = 1
        ORDER BY g.id ASC
        """,
        (game_id,),
    ).fetchall()

    by_user: dict[int, _PlayerBag] = {}
    word_finders: dict[str, set[int]] = {}
    for row in rows:
        uid: int = row["user_id"]
        bag = by_user.setdefault(uid, _PlayerBag(handle=row["handle"]))
        bag.words.append(row["word"])
        word_finders.setdefault(row["word"], set()).add(uid)

    for uid in expected_user_ids:
        if uid not in by_user:
            handle_row = db.execute(
                "SELECT handle FROM users WHERE id = ?", (uid,)
            ).fetchone()
            if handle_row is None:
                continue  # user row was deleted; skip silently
            by_user[uid] = _PlayerBag(handle=handle_row["handle"])

    out: list[_ScoredPlayer] = []
    for uid, bag in by_user.items():
        scored: list[_ScoredWord] = []
        raw_total = 0
        final_total = 0
        for w in bag.words:
            raw_pts = scoring.score_word(w, scoring_ladder)
            others = sorted(word_finders[w] - {uid})
            shared = bool(others)
            final_pts = 0 if (shared and dupes_cancel) else raw_pts
            scored.append(
                _ScoredWord(
                    word=w,
                    raw_points=raw_pts,
                    final_points=final_pts,
                    shared_with=others,
                )
            )
            raw_total += raw_pts
            final_total += final_pts
        out.append(
            _ScoredPlayer(
                user_id=uid,
                handle=bag.handle,
                words=scored,
                raw_total=raw_total,
                final_total=final_total,
            )
        )
    return out


def build_result(db: sqlite3.Connection, game: GameState) -> GameResult:
    """Compute the end-of-game payload from raw guesses + config.

    Pure read of the DB — does not mutate. Safe to call repeatedly
    on a finished game (e.g. for review). For a still-active game,
    the result reflects "what would the leaderboard be if we ended
    right now," which is occasionally useful for testing.
    """
    scored = _score_players(
        db,
        game_id=game.id,
        scoring_ladder=game.config.scoring_ladder,
        dupes_cancel=game.config.dupes_cancel,
        expected_user_ids=_expected_player_ids(db, game),
    )

    players = [
        PlayerResult(
            user_id=p.user_id,
            handle=p.handle,
            words=[
                PlayerWordEntry(word=w.word, points=w.final_points, shared_with=w.shared_with)
                for w in p.words
            ],
            raw_total=p.raw_total,
            final_total=p.final_total,
        )
        for p in scored
    ]
    players.sort(key=lambda p: -p.final_total)

    # Missed = legal words no one found, with what they'd have been worth.
    found = {w.word for p in scored for w in p.words}
    missed = [
        MissedWord(word=w, points=scoring.score_word(w, game.config.scoring_ladder))
        for w in sorted(game.legal_words)
        if w not in found
    ]

    ended_at = game.ended_at or _now()
    duration = int((ended_at - game.started_at).total_seconds())

    return GameResult(
        game_id=game.id,
        config=game.config,
        board=dice.board_to_display(game.board_raw, dice.get_by_name(game.config.dice_set).num),
        started_at=_iso(game.started_at),
        ended_at=_iso(ended_at),
        duration_seconds=duration,
        players=players,
        missed_words=missed,
    )


# --- History listings -----------------------------------------------------


def list_solo_games_for_user(
    db: sqlite3.Connection, user_id: int
) -> list[SoloGameSummary]:
    """All solo games for one user, most recent first.

    Includes both ended and still-active games (the latter so the
    UI can show "resume" for games someone walked away from).
    Per-game score is computed from the user's legal guesses; dupes-
    cancel doesn't apply in solo so raw == final.
    """
    rows = db.execute(
        """
        SELECT id, config, started_at, ended_at
        FROM games
        WHERE club_id IS NULL AND created_by = ?
        ORDER BY started_at DESC
        """,
        (user_id,),
    ).fetchall()

    out: list[SoloGameSummary] = []
    for row in rows:
        config = GameConfig.model_validate_json(row["config"])
        legal_guesses = db.execute(
            "SELECT word FROM guesses "
            "WHERE game_id = ? AND user_id = ? AND is_legal = 1",
            (row["id"], user_id),
        ).fetchall()
        words = [g["word"] for g in legal_guesses]
        score = sum(scoring.score_word(w, config.scoring_ladder) for w in words)
        out.append(
            SoloGameSummary(
                game_id=row["id"],
                started_at=row["started_at"],
                ended_at=row["ended_at"],
                dice_set=config.dice_set,
                timer_seconds=config.timer_seconds,
                your_score=score,
                your_word_count=len(words),
            )
        )
    return out


def list_club_games(
    db: sqlite3.Connection, club_id: int
) -> list[ClubGameSummary]:
    """All ended games for one club, most recent first.

    Active games are excluded — the live one (if any) is what the WS
    surfaces; history is "what we've finished." Per-player final
    totals (with dupes-cancel applied per the game's own config) are
    inlined so the club view's recent-games list doesn't need per-row
    fetches.
    """
    rows = db.execute(
        """
        SELECT id, config, started_at, ended_at
        FROM games
        WHERE club_id = ? AND ended_at IS NOT NULL
        ORDER BY started_at DESC
        """,
        (club_id,),
    ).fetchall()

    # All members appear in every game's leaderboard, even those who
    # never submitted a legal guess. Fetched once for the whole list.
    member_ids = [
        row["user_id"]
        for row in db.execute(
            "SELECT user_id FROM clubs_users WHERE club_id = ?", (club_id,)
        ).fetchall()
    ]

    out: list[ClubGameSummary] = []
    for row in rows:
        config = GameConfig.model_validate_json(row["config"])
        scored = _score_players(
            db,
            game_id=row["id"],
            scoring_ladder=config.scoring_ladder,
            dupes_cancel=config.dupes_cancel,
            expected_user_ids=member_ids,
        )
        players = sorted(
            [PlayerScoreSummary(handle=p.handle, final_total=p.final_total) for p in scored],
            key=lambda p: -p.final_total,
        )

        out.append(
            ClubGameSummary(
                game_id=row["id"],
                started_at=row["started_at"],
                ended_at=row["ended_at"],
                dice_set=config.dice_set,
                players=players,
            )
        )
    return out
