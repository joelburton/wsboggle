/**
 * Wire-protocol types, hand-mirrored from ``src/wsboggle/shared.py``.
 *
 * The Python side is the source of truth — when these drift, a
 * contract test (TBD) will fail. Keep this file dependency-free (no
 * React, no DOM); both sides import only types from it.
 */

export type GameMode = "competitive" | "collaborative";
export type TimerDirection = "down" | "up";

/** All per-game knobs. Stored as JSON on `games.config`. */
export type GameConfig = {
  dice_set: string;
  scoring_ladder: string;
  min_legal_length: number;
  mode: GameMode;
  dupes_cancel: boolean;
  timer_seconds: number | null;
  timer_direction: TimerDirection;
  min_words: number | null;
  max_words: number | null;
  min_score: number | null;
  max_score: number | null;
  min_longest: number | null;
  max_longest: number | null;
};

// --- HTTP response shapes ------------------------------------------------

export type PublicUser = {
  id: number;
  handle: string;
};

/** A club as it appears on the home page. */
export type ClubSummary = {
  id: number;
  name: string;
  /** All members (including the requesting user), sorted by handle_lower. */
  member_handles: string[];
  game_count: number;
  /** ISO timestamp of the most recent game, or null if none yet. */
  last_played_at: string | null;
  /** Set when there's something happening *right now*: an active
   *  game (`playing`), a pending proposal (`proposing`), or an
   *  end-of-game results panel awaiting Done clicks (`reviewing`).
   *  The home page uses this to surface the club state and to
   *  confirm before starting a solo game. `null` when nothing is
   *  in flight. */
  in_flight: "playing" | "proposing" | "reviewing" | null;
};

/** Response shape for GET /api/me — the home page's single load. */
export type MeResponse = {
  user: PublicUser;
  clubs: ClubSummary[];
};

// --- HTTP request shapes -------------------------------------------------

export type RegisterRequest = {
  handle: string;
  password: string;
  invite_code: string;
};

export type LoginRequest = {
  handle: string;
  password: string;
};

export type CreateClubRequest = {
  name: string;
  /** Other members' handles; the creator is added implicitly. */
  member_handles: string[];
};

// --- Game lifecycle shapes -----------------------------------------------
// Used for solo HTTP routes and (eventually) the multiplayer WS messages.

export type GameStartRequest = {
  config: GameConfig;
};

export type GuessRecord = {
  word: string;
  is_legal: boolean;
  points: number;
  /** Filled in collaborative mode; null in competitive (where the
   *  list is always the viewer's own) and on historical entries. */
  added_by_user_id: number | null;
  added_by_handle: string | null;
};

export type BoardStats = {
  /** Total findable legal words on this board. */
  total_words: number;
  /** Sum of all legal words' scores under the game's ladder. */
  total_points: number;
  /** Length of the longest legal word. */
  longest_word: number;
};

export type GameSnapshot = {
  game_id: number;
  board: string[][];        // display grid; "Qu"/"In" already expanded
  config: GameConfig;
  started_at: string;
  ends_at: string | null;
  ended_at: string | null;
  server_now: string;       // client uses this for clock-skew correction
  your_guesses: GuessRecord[];
  board_stats: BoardStats;
};

export type GuessRequest = {
  word: string;
};

export type GuessResult =
  | "accepted"
  | "too_short"        // real word but below the game's min length
  | "not_on_board"     // real word but no path on this board
  | "not_in_word_list" // real word, traceable, but not in our DAWG
  | "not_a_word"       // not in the dictionary at all
  | "already_submitted"
  | "game_inactive";

export type GuessResponse = {
  word: string;
  result: GuessResult;
  points: number;
};

export type PlayerWordEntry = {
  word: string;
  points: number;           // final, post-dupes-cancel
  shared_with: number[];    // other user_ids who also got it
};

export type PlayerResult = {
  user_id: number;
  handle: string;
  words: PlayerWordEntry[]; // in submission order
  raw_total: number;        // before dupes-cancel
  final_total: number;      // after dupes-cancel — the leaderboard number
};

export type MissedWord = {
  word: string;
  points: number;
};

export type GameResult = {
  game_id: number;
  config: GameConfig;
  board: string[][];
  started_at: string;
  ended_at: string;
  duration_seconds: number;
  players: PlayerResult[];  // sorted by final_total desc
  missed_words: MissedWord[];
};

/**
 * Returned by GET /api/games/:id. Snapshot is always populated;
 * result is non-null when the game has ended.
 */
export type GameView = {
  snapshot: GameSnapshot;
  result: GameResult | null;
};

// --- History summaries ---------------------------------------------------

export type SoloGameSummary = {
  game_id: number;
  started_at: string;
  ended_at: string | null;
  dice_set: string;
  timer_seconds: number | null;
  your_score: number;
  your_word_count: number;
};

export type PlayerScoreSummary = {
  handle: string;
  final_total: number;
};

export type ClubGameSummary = {
  game_id: number;
  started_at: string;
  ended_at: string;
  dice_set: string;
  players: PlayerScoreSummary[];
};

export type DefineResponse = {
  word: string;
  definition: string | null;
};

// --- WS message envelopes ------------------------------------------------
// Discriminated unions on the `type` field; same shape as the Pydantic
// models in ``src/wsboggle/shared.py``. Game-flow messages
// (gameStarted / guess* / gameEnded etc.) get added when the
// multiplayer game loop lands.

// Club-state payload pieces

export type ClubMember = {
  user_id: number;
  handle: string;
  /** Currently connected to this club's socket. */
  online: boolean;
};

export type ChatMessage = {
  id: number;
  user_id: number;
  handle: string;
  text: string;
  ts: string;
};

// Client → server

export type CHello = { type: "hello" };
export type CChat = { type: "chat"; text: string };
export type CNewGame = { type: "newGame"; config: GameConfig };
export type CGuess = { type: "guess"; word: string };
/** End the current game now. Any member can send (per CLAUDE.md). */
export type CEndGame = { type: "endGame" };
/** Confirm readiness for the pending proposal. No-op if no proposal. */
export type CGameReady = { type: "gameReady" };
/** Cancel the pending proposal. Any connected member can send. */
export type CCancelProposal = { type: "cancelProposal" };
/** Mark the sender as done reviewing the most-recently-ended game. */
export type CReviewDone = { type: "reviewDone" };
/** Claim the new-game config dialog so no other member can open
 *  their own dialog or click Play again until released. */
export type COpenNewGameDialog = { type: "openNewGameDialog" };
/** Release the dialog claim (Cancel / Esc / backdrop). */
export type CCloseNewGameDialog = { type: "closeNewGameDialog" };
export type ClientMessage =
  | CHello
  | CChat
  | CNewGame
  | CGuess
  | CEndGame
  | CGameReady
  | CCancelProposal
  | CReviewDone
  | COpenNewGameDialog
  | CCloseNewGameDialog;

// Server → client

export type SClubState = {
  type: "clubState";
  club_id: number;
  name: string;
  members: ClubMember[];
  chat: ChatMessage[];
  /** Populated when a game is currently active; per-viewer
   *  `your_guesses` for the connecting user. */
  current_game: GameSnapshot | null;
  /** Config of the club's most recently-started game, or null for
   *  a brand-new club. The club view pre-fills the new-game dialog
   *  from this and shows a one-click "Play again". */
  last_config: GameConfig | null;
  /** Set when a game has been proposed but not yet started — the
   *  connecting client should render the Ready prompt. */
  pending_proposal: PendingProposal | null;
  /** Set when the most-recently-ended game is being reviewed and
   *  no new game can start until every member has clicked Done. */
  pending_review: PendingReview | null;
  /** User id of the member currently holding the new-game-dialog
   *  claim, or null. While set, other members see a non-dismissable
   *  "X is choosing options" overlay. */
  new_game_dialog_opener_id: number | null;
};

/** A proposed game waiting for member readiness. Carried on the
 *  initial `clubState` snapshot and on `proposalUpdate` deltas. */
export type PendingProposal = {
  config: GameConfig;
  initiator_id: number;
  /** Always includes `initiator_id`. Treat as a set; order isn't
   *  significant. */
  ready_user_ids: number[];
};

/** The most-recently-ended game is being reviewed. New games are
 *  gated until every member's user_id is in `done_user_ids`. */
export type PendingReview = {
  done_user_ids: number[];
};

export type SChatMessage = {
  type: "chatMessage";
  message: ChatMessage;
};

export type SMemberPresence = {
  type: "memberPresence";
  user_id: number;
  online: boolean;
};

export type SFeedback = {
  type: "feedback";
  text: string;
  level: "info" | "warn" | "error";
};

export type SGameStarted = {
  type: "gameStarted";
  snapshot: GameSnapshot;
};

export type SGuessAccepted = {
  type: "guessAccepted";
  word: string;
  points: number;
  /** Same classification as the solo HTTP `GuessResponse`. Derive
   *  `is_legal` as `result === "accepted"` when needed. */
  result:
    | "accepted"
    | "too_short"
    | "not_on_board"
    | "not_in_word_list"
    | "not_a_word";
};

export type SGuessSubmitted = {
  type: "guessSubmitted";
  word: string;
  points: number;
  user_id: number;
  handle: string;
};

export type SGuessRejected = {
  type: "guessRejected";
  word: string;
  reason: "already_submitted" | "game_inactive";
};

export type SGameEnded = {
  type: "gameEnded";
  result: GameResult;
};

/** The pending-proposal state changed. `proposal=null` means the
 *  proposal was either resolved (a `gameStarted` will follow) or
 *  cancelled. */
export type SProposalUpdate = {
  type: "proposalUpdate";
  proposal: PendingProposal | null;
};

/** The pending-review state changed. `review=null` means every
 *  member has clicked Done. */
export type SReviewUpdate = {
  type: "reviewUpdate";
  review: PendingReview | null;
};

/** Who currently holds the new-game-dialog claim, or null. */
export type SNewGameDialogUpdate = {
  type: "newGameDialogUpdate";
  opener_id: number | null;
};

export type ServerMessage =
  | SClubState
  | SChatMessage
  | SMemberPresence
  | SFeedback
  | SGameStarted
  | SGuessAccepted
  | SGuessSubmitted
  | SGuessRejected
  | SGameEnded
  | SProposalUpdate
  | SReviewUpdate
  | SNewGameDialogUpdate;
