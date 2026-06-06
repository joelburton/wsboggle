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

from typing import Annotated, Literal, Union

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

    in_flight: Literal["playing", "proposing", "reviewing"] | None = None
    """Set when there's something happening *right now* in this club:
    an active game (``playing``), a pending proposal awaiting Ready
    clicks (``proposing``), or an end-of-game results panel awaiting
    Done clicks (``reviewing``). Used by the home page to warn the
    viewer before they start a solo game while owing something to a
    club. ``None`` on the freshly-created-from-POST path (no in-flight
    state can exist for a brand-new club)."""


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

    ``added_by_*`` is populated in collaborative mode so the
    client can color a recently-added word in its adder's shade
    for 5 s. ``None`` in competitive (the viewer is always the
    adder of their own list) and historical entries from before
    the field existed.
    """

    word: str
    is_legal: bool
    points: int
    added_by_user_id: int | None = None
    added_by_handle: str | None = None


class BoardStats(BaseModel):
    """Per-board "what's findable" counts: total legal words, the
    points they're worth in aggregate, and the longest one. Constant
    for the life of a game; clients render these as a "you vs. max"
    table so a player can see how much of the board they've covered.

    Safe to ship in the snapshot: in competitive mode it's the
    *board*'s ceiling, not any opponent's progress."""

    total_words: int
    total_points: int
    longest_word: int


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
    board_stats: BoardStats


class GuessRequest(BaseModel):
    """``POST /api/solo/games/:id/guess`` body."""

    word: str


GuessResult = Literal[
    "accepted",
    "too_short",        # real word but below ``min_legal_length``
    "not_on_board",     # real word per the dictionary, but not findable on this board
    "not_in_word_list", # real word, *is* findable on this board, but not in our DAWG
    "not_a_word",       # not in the dictionary at all
    "already_submitted",
    "game_inactive",
]
"""Finer-grained than just "legal vs not": the four illegal-but-
recorded outcomes (``too_short`` / ``not_on_board`` /
``not_in_word_list`` / ``not_a_word``) all show up struck through
in the player's word list, but the UI renders different immediate
feedback under the entry box.

The split between ``not_on_board`` and ``not_in_word_list`` exists
because the solver's word file (``words.dat``) and the dictionary
of definitions (``all.sqlite3``) are independent — a word can sit
in the dictionary but not the DAWG, in which case the user can
legitimately trace it on the board, the solver just never credits
it. Saying "not on board" in that case would be a lie.
"""


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

    Per-player final totals are inlined so the club view can render
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
# Discriminated unions on a ``type`` literal — Pydantic dispatches to the
# right subclass on parse. The TS mirror uses the same shape so the
# client can switch on `msg.type`.
#
# This slice covers the main club view (chat + presence). The game-flow
# messages from CLAUDE.md (`gameStarted`, `guessAccepted`,
# `guessSubmitted`, `guessRejected`, `gameEnded`, the client-side
# `guess`, `newGame`, `endGame`) get added when the multiplayer game
# loop lands.


# --- Club-state payload pieces -------------------------------------------


class ClubMember(BaseModel):
    """One member of a club as it appears in ``clubState``.

    ``online`` is computed at snapshot time from the WS registry —
    whoever is currently connected to this club's socket counts as
    in-club. Mirrors the *in club* presence semantic from CLAUDE.md
    (not "authenticated to the server").
    """

    user_id: int
    handle: str
    online: bool


class ChatMessage(BaseModel):
    """A persisted chat line. ``id`` is the DB rowid so the client
    can dedupe and order; ``ts`` is server-stamped."""

    id: int
    user_id: int
    handle: str
    text: str
    ts: str


# --- Client → server ------------------------------------------------------


class CHello(BaseModel):
    """Handshake the client sends right after the socket opens.

    Currently carries no fields — auth already happened on the HTTP
    handshake (the session cookie). Reserved as an extension point
    (resume tokens, client version, etc.)."""

    type: Literal["hello"] = "hello"


class CChat(BaseModel):
    """One chat line from the user. Server stamps id + timestamp."""

    type: Literal["chat"] = "chat"
    text: str


class CNewGame(BaseModel):
    """Request to start a new game. Server validates that all
    members are currently in-club and that no game is already
    active before persisting + broadcasting ``gameStarted``."""

    type: Literal["newGame"] = "newGame"
    config: "GameConfig"


class CGuess(BaseModel):
    """One word submission. The server replies with ``guessAccepted``
    or ``guessRejected`` privately to the sender (competitive mode);
    nothing about the guess is broadcast to other members during
    the timer."""

    type: Literal["guess"] = "guess"
    word: str


class CEndGame(BaseModel):
    """End the current game now.

    Any member can send (per CLAUDE.md). The server stops the
    timer task, marks the game ended, and broadcasts
    ``gameEnded``. The primary use cases are untimed / count-up
    games (where it's the only end path) and "the timer-driven end
    didn't fire for some reason" recovery — without this, a stuck
    game has no way out short of restarting the server.
    """

    type: Literal["endGame"] = "endGame"


class CGameReady(BaseModel):
    """Confirm readiness for the pending proposal. A no-op if there's
    no pending proposal (the server replies with a feedback toast)."""

    type: Literal["gameReady"] = "gameReady"


class CCancelProposal(BaseModel):
    """Cancel the pending proposal. Any connected member can send —
    the gate is "block on offline," so a stuck proposal can always be
    aborted by anyone still in the club."""

    type: Literal["cancelProposal"] = "cancelProposal"


class CReviewDone(BaseModel):
    """Mark the sender as done reviewing the most-recently-ended
    game. New games are gated until every member has sent this."""

    type: Literal["reviewDone"] = "reviewDone"


ClientMessage = Annotated[
    Union[
        CHello, CChat, CNewGame, CGuess, CEndGame,
        CGameReady, CCancelProposal, CReviewDone,
    ],
    Field(discriminator="type"),
]
"""Everything the client can send over the club WS."""


# --- Server → client ------------------------------------------------------


class SClubState(BaseModel):
    """Full snapshot sent right after the socket opens.

    Replays every chat line so a freshly-connected client doesn't
    need a separate REST fetch. ``members`` carries presence as of
    the moment this snapshot was assembled — subsequent changes
    flow as ``memberPresence`` deltas.

    ``current_game`` is populated when a game is currently active
    in the club: a mid-game reconnect (refresh, dropped socket) gets
    enough to render the play view immediately. The snapshot is
    per-viewer — ``your_guesses`` reflects only the connecting
    user's own submissions (competitive privacy).

    ``last_config`` is the config of the club's most recent game
    (active or ended), or ``None`` for a club that has never
    played. The club view pre-fills the new-game dialog from it and
    offers a one-click "Play again" shortcut.
    """

    type: Literal["clubState"] = "clubState"
    club_id: int
    name: str
    members: list[ClubMember]
    chat: list[ChatMessage]
    current_game: "GameSnapshot | None" = None
    last_config: "GameConfig | None" = None
    # Ephemeral confirmation states. Cleared by a server restart.
    pending_proposal: "PendingProposal | None" = None
    pending_review: "PendingReview | None" = None


class PendingProposal(BaseModel):
    """A game has been proposed but not all members have clicked
    Ready. Carried on the initial ``clubState`` snapshot (so mid-flow
    reconnects see the prompt) and on ``proposalUpdate`` deltas.

    ``ready_user_ids`` always includes the initiator (clicking Start
    counts as their ready). Order is not significant; the client
    treats it as a set.
    """

    config: "GameConfig"
    initiator_id: int
    ready_user_ids: list[int]


class PendingReview(BaseModel):
    """The most-recently-ended game's results are being reviewed.
    No new game can start until every member has clicked Done."""

    done_user_ids: list[int]


class SChatMessage(BaseModel):
    """Incremental chat broadcast — one of these per accepted
    ``chat`` from any member."""

    type: Literal["chatMessage"] = "chatMessage"
    message: ChatMessage


class SMemberPresence(BaseModel):
    """Presence delta: one member just connected or disconnected.

    Broadcast to every other member's socket. The connector
    themselves learns their own state from the ``clubState`` snapshot.
    """

    type: Literal["memberPresence"] = "memberPresence"
    user_id: int
    online: bool


class SFeedback(BaseModel):
    """Short toast — used for non-fatal errors that the client
    should surface (e.g. "chat too long"). Distinct from socket
    close: feedback keeps the socket open."""

    type: Literal["feedback"] = "feedback"
    text: str
    level: Literal["info", "warn", "error"] = "info"


class SGameStarted(BaseModel):
    """A new game has been started for this club. Broadcast to
    every member's socket; each viewer reads ``snapshot.your_guesses``
    (always empty here) for their own list."""

    type: Literal["gameStarted"] = "gameStarted"
    snapshot: "GameSnapshot"


class SGuessAccepted(BaseModel):
    """The server recorded one of your guesses. Sent **only to the
    submitting socket** in competitive mode — other members see
    nothing about the guess during the timer (classic Boggle
    privacy).

    Mirrors the solo HTTP ``GuessResponse``: ``result`` is one of
    ``accepted`` / ``too_short`` / ``not_on_board`` / ``not_a_word``
    so the client renders distinct feedback under the entry box.
    The legality flag in the word-list history is derived as
    ``result == "accepted"``.
    """

    type: Literal["guessAccepted"] = "guessAccepted"
    word: str
    points: int
    result: Literal[
        "accepted",
        "too_short",
        "not_on_board",
        "not_in_word_list",
        "not_a_word",
    ]


class SGuessSubmitted(BaseModel):
    """A team member's accepted guess in collaborative mode.
    Broadcast to **every** member's socket (including the sender);
    everyone's WordList renders the same shared word from this.

    The client highlights newly-arrived entries in the adder's
    player color for 5 s — that's the only persistent
    attribution; after the fade the word is anonymous in the
    shared list. Competitive mode never emits this message
    (everything stays private as ``guessAccepted``).
    """

    type: Literal["guessSubmitted"] = "guessSubmitted"
    word: str
    points: int
    user_id: int
    handle: str


class SGuessRejected(BaseModel):
    """The server rejected one of your guesses. Sent only to the
    submitting socket. ``reason`` mirrors :data:`GuessResult`
    minus ``accepted``."""

    type: Literal["guessRejected"] = "guessRejected"
    word: str
    reason: Literal["already_submitted", "game_inactive"]


class SGameEnded(BaseModel):
    """The current game has ended — timer expired (v1) or a
    manual end fired (v2). Broadcast to every member; carries the
    full end-of-game result with all players' word lists revealed.

    Implicitly opens a review phase — the server emits a
    ``reviewUpdate`` with an empty ``done_user_ids`` immediately
    after this. The client may treat ``gameEnded`` itself as the
    "review opens" signal if convenient.
    """

    type: Literal["gameEnded"] = "gameEnded"
    result: "GameResult"


class SProposalUpdate(BaseModel):
    """The pending-proposal state changed. ``proposal=None`` means
    the proposal was resolved — either cancelled, or all members
    readied and a ``gameStarted`` is on its way."""

    type: Literal["proposalUpdate"] = "proposalUpdate"
    proposal: "PendingProposal | None"


class SReviewUpdate(BaseModel):
    """The pending-review state changed. ``review=None`` means every
    member has clicked Done; the club is free to start a new game."""

    type: Literal["reviewUpdate"] = "reviewUpdate"
    review: "PendingReview | None"


ServerMessage = Annotated[
    Union[
        SClubState,
        SChatMessage,
        SMemberPresence,
        SFeedback,
        SGameStarted,
        SGuessAccepted,
        SGuessSubmitted,
        SGuessRejected,
        SGameEnded,
        SProposalUpdate,
        SReviewUpdate,
    ],
    Field(discriminator="type"),
]
"""Everything the server sends over the club WS."""
