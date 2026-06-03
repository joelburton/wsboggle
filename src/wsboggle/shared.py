"""Wire-protocol types — the single source of truth for messages
that cross the client/server boundary.

These Pydantic models are hand-mirrored as TypeScript interfaces in
``client/src/shared.ts``. A contract test (TBD) asserts representative
JSON samples round-trip through both sides without drift. See
``CLAUDE.md`` § *Wire protocol* for the message catalog.

The module is intentionally dependency-free aside from Pydantic — no
sqlite, no FastAPI imports — so it can be imported from anywhere
without pulling in the framework.

Convention: wire field names are ``snake_case`` (matches Python, no
alias overhead). The TS mirror uses the same names.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


# --- Game configuration ----------------------------------------------------
# Stored as JSON on ``games.config``. Every per-game knob lives here so
# adding new options later is a schema-free change. v1 always writes
# defaults except for the two fields the New game dialog exposes
# (``dice_set`` and ``timer_seconds``).


class GameConfig(BaseModel):
    """All per-game knobs. Stored as JSON on ``games.config``."""

    dice_set: str = "4"
    """Key into the dice-set registry (see ``wsboggle.dice``).
    Default is the modern 4×4 set."""

    scoring_ladder: str = "basic"
    """Key into the scoring-ladder registry. v1 always uses ``basic``."""

    min_legal_length: int = 3
    """Words shorter than this aren't counted as legal."""

    mode: Literal["competitive", "collaborative"] = "competitive"
    """Competitive = private lists, revealed at end. Collaborative =
    shared list, broadcast immediately. v1 is competitive-only."""

    dupes_cancel: bool = True
    """When True, a word found by ≥ 2 players scores 0 for everyone
    (classic Boggle). When False, every player scores their own list
    independently. Meaningful only in competitive mode."""

    timer_seconds: int | None = 180
    """Game length in seconds. ``None`` means untimed (manual end).
    v1 always populates."""

    timer_direction: Literal["down", "up"] = "down"
    """Display only — the server's end-of-game decision is based on
    ``timer_seconds``, not the direction. ``up`` is a forward-compat
    knob for count-up games."""

    # "Good board" rejection-sampling constraints (passed to
    # libwords.get_words). All optional; None means "no constraint".
    min_words: int | None = None
    max_words: int | None = None
    min_score: int | None = None
    max_score: int | None = None
    min_longest: int | None = None
    max_longest: int | None = None


# --- HTTP response shapes --------------------------------------------------


class PublicUser(BaseModel):
    """User info returned to clients. Strict subset — no password
    hash, no created_at, no internal columns."""

    id: int
    handle: str


class ClubSummary(BaseModel):
    """A club as it appears on the home page.

    Sorted-by-recency lists of these come back from ``GET /api/me``;
    a single one comes back from ``POST /api/clubs`` so the client
    can append it to the local list without a re-fetch.
    """

    id: int
    name: str
    member_handles: list[str]
    """All members (including the requesting user), sorted by
    ``handle_lower`` for predictable display."""

    game_count: int
    last_played_at: str | None
    """ISO timestamp of the most recent game in this club, or
    ``None`` if no games yet."""


class MeResponse(BaseModel):
    """Payload for ``GET /api/me``. The home page's single load."""

    user: PublicUser
    clubs: list[ClubSummary]


# --- HTTP request shapes --------------------------------------------------


class RegisterRequest(BaseModel):
    """``POST /api/auth/register`` body."""

    handle: str
    password: str
    invite_code: str


class LoginRequest(BaseModel):
    """``POST /api/auth/login`` body."""

    handle: str
    password: str


class CreateClubRequest(BaseModel):
    """``POST /api/clubs`` body.

    The creator is implicit — they're not listed in ``member_handles``.
    For 2-person clubs the client computes the default name
    (``"alice + bob"``) before submitting; the server doesn't
    auto-generate.
    """

    name: str
    member_handles: list[str]


# --- Game lifecycle shapes -------------------------------------------------
# Used for both the solo HTTP routes and (eventually) the multiplayer
# WS messages. Solo and multiplayer share these same payloads — only
# the transport differs.


class GameStartRequest(BaseModel):
    """``POST /api/solo/games`` body; also the payload of the
    ``newGame`` WS message. Just wraps a :class:`GameConfig`."""

    config: GameConfig


class GuessRecord(BaseModel):
    """One submitted guess as it appears in a snapshot's history.

    ``points`` is the *raw* score under the game's ladder (no
    dupes-cancel adjustment yet — that's an end-of-game concern).
    Illegal guesses have ``points=0`` and ``is_legal=False``.
    """

    word: str
    is_legal: bool
    points: int


class GameSnapshot(BaseModel):
    """Returned when a game starts (or is loaded mid-play).

    ``board`` is the *display* grid — `"Qu"`/`"In"`/etc. already
    expanded from the raw die chars. ``server_now`` is included so
    the client can compute its skew against ``ends_at`` for the
    countdown without trusting the local clock.
    """

    game_id: int
    board: list[list[str]]
    config: GameConfig
    started_at: str
    ends_at: str | None
    ended_at: str | None
    server_now: str
    your_guesses: list[GuessRecord]


class GuessRequest(BaseModel):
    """``POST /api/solo/games/:id/guess`` body."""

    word: str


GuessResult = Literal[
    "accepted",
    "not_in_dictionary",
    "already_submitted",
    "game_inactive",
]


class GuessResponse(BaseModel):
    """Server's verdict on a single guess.

    ``points`` is the raw score under the configured ladder when the
    guess was accepted; 0 otherwise. (Dupes-cancel adjustment, when
    applicable, only happens at end-of-game.)
    """

    word: str
    result: GuessResult
    points: int


class PlayerWordEntry(BaseModel):
    """One word as it appears in a player's end-of-game list.

    ``points`` is the *final* score: 0 if ``dupes_cancel`` and
    ``shared_with`` is non-empty; raw ladder score otherwise.
    ``shared_with`` is the list of other ``user_id``s who also
    submitted this word — empty in solo, sorted ascending for
    deterministic rendering.
    """

    word: str
    points: int
    shared_with: list[int]


class PlayerResult(BaseModel):
    """Per-player summary at end-of-game."""

    user_id: int
    handle: str
    words: list[PlayerWordEntry]
    """In submission order (oldest first)."""

    raw_total: int
    """Sum of raw ladder scores — before dupes-cancel."""

    final_total: int
    """Sum after dupes-cancel; what shows on the leaderboard."""


class MissedWord(BaseModel):
    """A legal word that no player found. ``points`` is what it
    would've been worth."""

    word: str
    points: int


class GameResult(BaseModel):
    """End-of-game payload.

    Returned by the ``POST /api/solo/games/:id/end`` route and (later)
    broadcast as the ``gameEnded`` WS message. ``players`` is sorted
    by ``final_total`` desc — natural leaderboard order.
    """

    game_id: int
    config: GameConfig
    board: list[list[str]]
    started_at: str
    ended_at: str
    duration_seconds: int
    players: list[PlayerResult]
    missed_words: list[MissedWord]


class GameView(BaseModel):
    """Returned by ``GET /api/games/:id``.

    Always carries the ``snapshot`` (board, config, the viewer's own
    guesses). When the game has ended, ``result`` is also populated
    — same shape ``POST /api/solo/games/:id/end`` returns. The
    client renders the board off the snapshot in both states and
    conditionally renders the results panel.
    """

    snapshot: "GameSnapshot"
    result: GameResult | None


# --- History summaries ----------------------------------------------------


class SoloGameSummary(BaseModel):
    """One row in ``GET /api/solo/games``.

    Lean shape for listing — the full game is loaded via
    ``GET /api/games/:id`` only when the user clicks through.
    ``ended_at`` ``None`` means the game is still active (or
    abandoned mid-play).
    """

    game_id: int
    started_at: str
    ended_at: str | None
    dice_set: str
    timer_seconds: int | None
    your_score: int
    your_word_count: int


class PlayerScoreSummary(BaseModel):
    """One player's final number, for game-list rows."""

    handle: str
    final_total: int


class ClubGameSummary(BaseModel):
    """One row in ``GET /api/clubs/:id/games`` — only ended games appear.

    Per-player final totals are inlined so the lobby can render
    leaderboard rows without an N+1 fetch.
    """

    game_id: int
    started_at: str
    ended_at: str
    dice_set: str
    players: list[PlayerScoreSummary]


class DefineResponse(BaseModel):
    """Returned by ``GET /api/define``. ``definition`` is ``None``
    when the word isn't in the dictionary (or the dictionary isn't
    installed)."""

    word: str
    definition: str | None


# --- WS message envelopes -------------------------------------------------
# TODO(ws): stubs only — replace these with discriminated unions on a
# ``type`` field once the WS handlers exist. The catalog in CLAUDE.md
# (§ Wire protocol) is the spec.


class ClientMessage(BaseModel):
    """TODO(ws): base for everything the client sends over WS."""

    type: str = Field(..., description="Message discriminator")


class ServerMessage(BaseModel):
    """TODO(ws): base for everything the server sends over WS."""

    type: str = Field(..., description="Message discriminator")
