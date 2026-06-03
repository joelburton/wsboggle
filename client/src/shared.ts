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
};

export type GuessRequest = {
  word: string;
};

export type GuessResult =
  | "accepted"
  | "not_in_dictionary"
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

// --- WS message envelopes (placeholders) ---------------------------------

export type ClientMessage = { type: string };
export type ServerMessage = { type: string };
