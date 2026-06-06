# wsboggle

A collaborative, web-based Boggle game. Two or more friends form a *club* and
play a series of timed boards together over WebSocket; scoring follows classic
competitive Boggle rules (each player has a private word list during the timer;
words found by multiple players score 0 for everyone; reveal at end of game).

Pre-implementation. This document records the design decisions reached before
any code was written; treat it as the spec until code and docs diverge, then
update both.

Companion projects:
- `../crossplay` — collaborative crossword. Source of architectural patterns
  (server-authoritative WS, cookie sessions, scrypt passwords, single-port
  prod deploy, hand-rolled routing). Mostly transferable in shape, not in code.
- `../tboggle` — terminal Boggle. Historical source of game logic, dice
  sets, scoring ladders, the C `libwords` solver, and the SQLite
  definitions DB. The vendored copies live in this repo now
  (`c/libwords.c`, `data/words.dat`, `data/all.sqlite3`); no runtime
  dependency on a sibling tboggle checkout. Cross-reference tboggle
  only for design history.

## Goals

1. **Trusted-friends app.** Small-scale, invite-gated, no abuse-mitigation
   surface. Single-process uvicorn is the target.
2. **Open source, self-hostable.** Someone with a Linux box should be able to
   clone, build the C library, init the DB, and run uvicorn in ~10 minutes.
   README walks through this.
3. **Educational, readable code.** Docstrings on every function. Type
   annotations throughout, but readability beats coverage — fall back to
   untyped if the type would be hard to read. `mypy --disallow-untyped-defs`,
   not full `--strict`.

## Forward compatibility — knobs v1 ships hard-coded but v2 will expose

We don't expose UI for these in v1, but the storage, wire protocol, and
server logic should accommodate them so v2 is purely additive (new UI on
top of existing branches), not a rewrite. All of these are **per-game
configuration**, not per-club — a club might play timed-competitive on
Friday and untimed-collaborative on Sunday.

1. **Mode: competitive vs collaborative.** Competitive = each player's
   list is private during the timer, revealed at end. Collaborative = one
   shared list, all guesses broadcast immediately to all members.
   *Design implication:* the server decides the broadcast scope of an
   accepted guess (`guessAccepted` private vs `guessSubmitted` broadcast)
   based on the game's mode. Both message types exist from day one.
2. **Dupes-cancel toggle.** "Word found by ≥ 2 players scores 0 for
   everyone" is classic competitive Boggle; some players prefer
   independent scoring. *Design implication:* end-of-game scoring is a
   function that takes the per-player word lists *and* the dupes-cancel
   flag from the game's config. No hard-coded rule in `gameEnded`'s
   payload computation.
3. **Board size and dice set.** tboggle ships 4×4 / 5×5 / 6×6 in
   several variants. The dice set is a key into a registry ported
   from `tboggle/dice.py`; board size is implied by the dice set.
   v1 **exposes** the dice set as a single dropdown in the New
   game dialog (all eight tboggle variants, labelled with their
   descriptive names — *4×4 Classic*, *5×5 Big 2012*, etc.). Picking
   a tileset implies picking a board size.
4. **"Good board" constraints.** tboggle's `fill_board()` accepts
   min/max for word count, total score, and longest word — rejection
   sampling produces a board meeting the constraints. *Design
   implication:* the server's board-fill call accepts these as optional
   parameters from day one. v1 always passes "no constraints" (pure
   random); v2 exposes the knobs.
5. **Timer modes.** Currently planned: fixed-duration countdown. Other
   modes: untimed (manual end), count-up (no fixed end). *Design
   implication:* `games.ends_at` is **nullable**. Server-side timer
   expiry only fires when set; untimed games end via an explicit
   `endGame` message from any participant. The client renders countdown
   or count-up based on which fields are populated.

These five are the load-bearing forward-compat features. Adding others
later (custom scoring ladders via UI, per-club defaults, etc.) is
straightforward as long as game config stays a typed blob rather than
fixed columns.

## Stack

- **Backend:** Python + FastAPI + Python's stdlib `sqlite3`. WebSockets via
  FastAPI's built-in support. The tboggle backend
  (`../tboggle/src/tboggle/`) is the starting point — port the Python modules
  as-is and call `libwords.so` via the existing `ctypes` binding.
- **Frontend:** React 18 + Vite + TypeScript + CSS Modules.
- **Build:** a small Makefile compiles `libwords.so` from the C source
  (already in tboggle). No Docker.
- **Auth:** cookie-keyed server-side sessions (mirroring crossplay's
  approach). Passwords via `hashlib.scrypt`. Registration is gated by
  invite codes, exactly as crossplay does it — an `invite_codes` table
  managed by the admin via direct SQL; the joiner enters the code on
  the registration form. No email verification, no password reset
  (admin handles via DB), no OAuth.

## Glossary

These terms are load-bearing — use them consistently in code, UI, and docs:

- **Club** — a durable group of n ≥ 2 users who play Boggle together.
  Membership is fixed at club creation. No add, no remove, no leave UX (out
  of scope for v1). Each club has a name (auto-generated for n=2, user-set
  for n≥3) and persistent chat.
- **Game** — a single timed Boggle round inside a club (or solo). One game =
  one board layout + one timer + per-player word lists.
- **Board** — the visible dice grid. A property of a game, not its own
  entity in the DB.
- **Solo** — single-player mode. Not a club. Separate UI affordance on the
  home page.
- **Invite code** — shared-secret string that gates registration. Admin
  curates the `invite_codes` table by hand; existing users hand a code
  to a friend over SMS / phone / whatever, the friend visits the home
  page and types it into the registration form. No URL-based invites,
  no per-user tracking. Has nothing to do with club membership; it only
  gets a new user into the system. Existing users add registered users
  to clubs by handle.

Explicitly *not* used:
- **Room** / **Lobby** — both imply transient "who's in it now"
  semantics. Our model is durable membership; *club* captures that
  better. Even when no game is active, that view is the *main club
  view*, not "the lobby."
- **Puzzle** — that's a crossplay word.

## Data model

```
users         (id, handle, handle_lower, password_hash, created_at)
clubs         (id, name, created_by, created_at)
clubs_users   (club_id, user_id, joined_at)        # insert-only, no DELETE path
games         (id, club_id NULL, created_by NULL,
               config JSON, board TEXT, legal_words TEXT,
               started_at, ends_at NULL, ended_at NULL)
guesses       (game_id, user_id, word, is_legal, ts,
               UNIQUE(game_id, user_id, word))
chat_messages (club_id, user_id, text, ts)
invite_codes  (code PK, label, created_at)         # admin-curated via SQL
sessions      (id, user_id, expires_at)
```

Notes:
- `games.club_id NULL` means solo. Same scoring code, same dice generation,
  just no broadcast.
- `clubs_users` is insert-only. Duplicate-member clubs are allowed.
- `chat_messages` is per-club, persists forever. Replayed in full on socket
  connect.
- `games.config` is a Pydantic-modelled JSON blob holding every per-game
  knob — dice set, scoring ladder, min legal length, mode
  (`"competitive"|"collaborative"`), dupes-cancel flag, timer-direction,
  and the optional "good board" min/max constraints. See *Forward
  compatibility* above. v1 always writes defaults; v2 exposes the knobs
  in UI without a schema change.
- `games.ends_at NULL` means untimed. The server only schedules a
  timer-expiry job when this is set.
- `guesses` has `UNIQUE(game_id, user_id, word)` — the constraint
  is the source of truth for "no duplicates per user per game."
  `games.submit_guess` does a SELECT short-circuit for the common
  case and treats the resulting IntegrityError as
  `"already_submitted"` for the concurrent-submit race. Scoring
  (with dupes-cancel applied) is computed on demand from this raw
  log + the game's config, not stored.
- `games.board` is the raw board string from `libwords.get_words`
  (row-major dice characters, with `1`–`6` encoding special faces
  like `Qu`/`In`/etc.). `games.legal_words` is the JSON-encoded
  sorted list of every legal word the solver found on that board.
  We persist both because the C library has no "recompute words for
  a known board" function — game review and scoring read from the
  DB, never call back into the solver.
- Last-game config per club: derive at write time by reading the most
  recent `games` row with matching `club_id`; no separate config table.

## Wire protocol

- **One WebSocket per club:** `/ws/clubs/:id`. Chat, presence, game
  lifecycle, and guess submission all flow over this socket.
- **Solo gameplay is HTTP only** — single player, no chat, no presence.
- **Server-authoritative timer.** Timer state lives on the server; clients
  render the countdown from `ends_at`. The server broadcasts `gameEnded`
  when the timer expires.
- **No optimistic submit.** Boggle guess submission is atomic (a few per
  minute, not per keystroke). Client sends `guess`, server replies with
  accept/reject.

Wire types live in `wsboggle/shared.py` as Pydantic models, hand-mirrored as
TypeScript interfaces in `client/src/shared.ts`. A small contract test
asserts a representative sample of each message round-trips through both.
We accept hand-mirror sync risk as the cost of two languages.

### Message catalog (sketch — formalize when implementing)

**Client → server:**
- `hello` — handshake on connect; identifies the user.
- `chat` — `{text}`. Server stamps timestamp.
- `guess` — `{word}`. Server validates + scores.
- `newGame` — `{config}`. Any member can initiate; rejected if not all
  members online.
- `endGame` — explicit end (only meaningful when the game has no timer).
  Any member can send.

**Server → client:**
- `clubState` — full snapshot on connect: members, chat history, current
  game (if any), recent game-history summary.
- `chatMessage` — incremental chat.
- `memberPresence` — `{user_id, online}`.
- `gameStarted` — `{game_id, board, ends_at (nullable), config}`.
- `guessAccepted` — to the guesser only — `{word, points}`. Used in
  **competitive** mode.
- `guessSubmitted` — broadcast to all — `{user_id, word, points}`. Used
  in **collaborative** mode. (Same accept-decision logic as
  `guessAccepted`; only the broadcast scope differs.)
- `guessRejected` — to the guesser only — `{word, reason}`. The
  `reason` mirrors `games.GuessOutcome.result` — `"not_in_dictionary"`,
  `"already_submitted"`, or `"game_inactive"` (the last fires when
  the WS hands a guess to `submit_guess` after the server-side timer
  has already expired; the WS handler should also broadcast
  `gameEnded` if that hasn't happened yet).
- `gameEnded` — `{results}` (per-player word lists with the configured
  scoring rules applied — dupes-cancel respected per `config`).
- `feedback` — `{id, text, level}` for short toasts.

## Game rules

- **Scoring:** competitive Boggle. Each player's word list is private
  during the timer. At end-of-game, lists are revealed side-by-side. Words
  found by ≥ 2 players score 0 for everyone (Boggle classic). Multiple
  scoring ladders are ported from tboggle's `chooser.py`; v1 defaults to
  *Basic: 1-11* with no UI for picking.
- **Single scoring engine.** `games._score_players` is the one
  implementation of "group legal guesses by user, identify shared
  words, apply dupes-cancel" — consumed by both `build_result`
  (end-of-game payload) and `list_club_games` (history summaries).
  Any future live-leaderboard work over WS should use it too rather
  than reimplement the dupes-cancel logic.
- **Timer:** server-owned. Hidden-until-end scoring means no per-tick
  score broadcasts — just a `gameEnded` at zero.
- **Disconnect mid-game:** other members continue; timer doesn't pause.
  The disconnecter rejoining sees the in-progress board with remaining
  time and can submit guesses for the rest.
- **Starting a new game:** all club members must currently be connected
  to the club's socket. New-game button is disabled otherwise, with a
  nudge to play solo or in another club.
- **Last-config recall:** the new-game dialog pre-fills from the most
  recent game in this club. UX has *Play again* (one click, last config)
  and *New game…* (open the config dialog).

## Mobile and input

- **First iteration is keyboard-only.** Type the word, hit Enter — the
  tboggle model. Works everywhere there's a hardware keyboard. Touch
  users can play in v1 via the device's on-screen keyboard; awkward but
  functional. Getting the core flow right comes before the input UX.
- **Trace-input is deferred** but planned for v1.x. Adding it later
  doesn't disturb the backend — the wire is already
  `{type: "guess", word}`, input-agnostic by construction. Trace is a
  client-only feature: the UI enforces adjacency, the server only
  checks word ∈ `legal.words`. Since `legal.words` is computed from the
  actual board, every word the server accepts is traceable by
  definition; the two checks can't disagree.
- **Layout is responsive** — board on the left + word list on the
  right on wide viewports; stacked on narrow. The board is the
  dominant element on mobile regardless of input mode.

## Auth + invites

- Cookie-keyed server-side sessions. `wsboggle_session` cookie, HTTP-only,
  Lax, Secure in prod. Sliding 30d expiry.
- Passwords via `hashlib.scrypt`.
- Registration gated by invite codes from the admin-curated `invite_codes`
  table. The registration form has a code field; the server validates
  case-insensitively against the table. No tracking of who used which
  code, no expiry, no URL-based invite links. Codes are reusable until
  the admin deletes them.
- **Club membership is unilateral.** Adding a registered user to a club is
  done by handle and requires no acceptance.
- **Handles are case-preserved for display, case-insensitive for lookup**
  (same as crossplay).

## Client conventions worth carrying over from crossplay

The same friend group plays both apps; UI muscle memory transfers. When
in doubt, do what crossplay does.

- **Chat is a draggable, resizable popup**, not an inline panel. Use
  `react-rnd`. Position and size persist in localStorage; clamp the
  saved rect against the viewport on mount and on resize. See
  crossplay's `ChatPanel.tsx` + `useDraggablePanel` for the shared
  rect/persist/clamp logic — port wholesale.
- **`/` opens / focuses the chat input** (when focus is not already in
  an input). See crossplay's `PuzzleView.tsx` keystroke handler.
- **Identity is fixed at session mount** — handle + color from the
  account, no in-session rename.
- **URLs auto-linkify** in chat (port crossplay's `linkify`).
- **`!`-prefixed messages render bold and force-open the recipient's
  chat panel** (the "hey, look at this" mechanic).
- **Presence label is "in club", not "online".** The user's mental
  model is "is moth here with me right now," not "is moth
  authenticated to the server." Applies to the club's member list,
  presence dots, and last-seen labels ("last in club: 2h ago").
- **Routing is hand-rolled** in `client/src/routing.ts`. No
  react-router. The route set is small (`/`, `/c/:id`, `/solo`,
  `/login`, `/register`); a `useLocation` hook + a switch statement
  is less code than the dependency. Server-side SPA fallback at the
  prod static-serve mount makes deep links work.

## Routes

- `/` — home page. Logged-in: list of clubs + Solo button + Invite button.
  Logged-out: login/register.
- `/c/:id` — club page. Main / in-game / post-game states all live on
  this URL; the client tracks which via WS state.
- `/solo` — solo play. HTTP-only.
- `/login`, `/register` — auth. The register form has an invite-code
  field.
- `GET /api/me` — current user + club list. (Home page loads this once;
  no live data needed for v1.)
- `WS /ws/clubs/:id` — the club socket.

## Code style

- Docstrings on every public function. Module-level docstrings on every
  file describing what lives there.
- Type annotations throughout. `mypy --disallow-untyped-defs`, not full
  `--strict` — strict mode triggers a lot of `Any` ceremony around
  untyped libraries that doesn't pay off in clarity.
- **Pydantic** for wire types (validates + serializes + acts as the schema
  source the frontend mirrors). The server never trusts a raw dict from
  the socket.
- **Plain dataclasses** for internal domain types (`Game`, `WordList`,
  etc.) — port tboggle's classes nearly verbatim.
- Prefer simple `dict[str, int]` to complex generic types if both work.
  Don't gold-plate annotations.

## Out of scope (v1)

Two flavors here, kept separate because they imply different things for
the v1 code:

### Deferred features — architecture supports them, UI doesn't (yet)

These are the *Forward compatibility* items above. v1 code paths handle
both branches; v1 UI defaults to one of them. v2 just adds knobs.

- **Collaborative mode.** Server can broadcast `guessSubmitted` in collab
  mode from day one; v1 always picks competitive.
- **Untimed / count-up games.** `games.ends_at` is nullable; v1 always
  populates it.
- **"Good board" constraints.** Solver accepts them; v1 always passes none.
- **Configurable scoring ladder / dupes-cancel / game mode.** Live in
  the typed `games.config` blob; v1 always writes defaults. (Dice set
  *is* exposed in v1 — see Forward compatibility item 3.)
- **Custom scoring ladders via UI.** Ladders are ported; v1 ships
  *Basic: 1-11* with no picker.
- **Trace-input on the board.** Wire is already input-agnostic;
  v1.x adds the trace UI on top of the existing `{guess, word}`
  message.

### Not built and not architected for

- **Member add / remove after club creation.** Clubs are immutable
  membership in the data model. Adding this later means a migration.
- **Cross-club presence** ("moth just came online in puplandia"). Would
  require an app-level WS or polling; v1 only shows presence within the
  club you're currently viewing.
- **Archive / hide clubs.** All clubs you're in show on the home page.
- **Email verification, password reset, OAuth.** Admin handles
  forgotten passwords via DB.
- **Rate limiting, abuse mitigation.** Trusted-friends trust model.
- **Spectating.** No way to watch a game without being a club member.

## Engineering follow-ups (not features, just work we know we want)

These are implementation tasks the team has identified but doesn't
need to act on right now. Distinct from *Out of scope* above: those
are user-facing features deferred by design, these are internal
improvements deferred by triage.

- **Add a board-restoration entry point to `libwords.c`.** Today the
  C library only generates *random* boards via `get_words`. tboggle's
  Python wrapper references a `restore_game(scores, w, h, dice) →
  char**` that doesn't exist in the C source. We work around the gap
  by persisting `games.legal_words` at game creation. Adding the
  function would (1) let us drop that column, (2) make tests use
  deterministic boards instead of "generate random and assert >0
  words," and (3) make game replay / debugging easier. Should be a
  modest C diff — `make_board` + `find_all_words` already exist; the
  new function just wires them together without going through the
  dice-rolling step.

- **Audit `libwords.c`** for correctness, performance, and algorithmic
  improvements. The C code was hand-written by Joel years ago and
  hasn't had a fresh pass since. Specifically:
  - Look for off-by-ones, leaks, and any UB around the
    `search.h`/`tsearch` btree usage and the manual word-array
    growth.
  - **Known leak:** `get_words` returns heap-allocated memory
    (`b->word_array` entries via `strdup`, plus `b->dice_simple`).
    Python keeps no references to free them — every game start
    leaks one full word list + the board string. Invisible at solo
    scale; matters for a long-running multiplayer server. Fix is to
    add a small `free_words(char**, char*)` to the C lib and call
    it after the Python side has copied the data.
  - Profile a hot constraint search (e.g. 6×6 with tight
    `min_words` / `min_longest`) — the rejection sampler currently
    re-runs the full DFS per try; cheap upfront board-shape filters
    might cut iterations dramatically.
  - DAWG layout: confirm it's actually a DAWG vs. a trie, and
    whether the lookup hot path benefits from rearranging nodes
    for cache locality.
  - Look for SIMD / vectorisable hot spots in the adjacency-walk
    loop.

  Don't tackle this until the app is otherwise feature-complete —
  premature C optimisation before the surrounding system exists is
  a way to spend a week on the wrong things.
