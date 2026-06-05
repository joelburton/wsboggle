/**
 * The club page (`/c/:id`).
 *
 * Owns one club WebSocket via :func:`useClubSocket` and renders one
 * of three views off the socket state: the main club view (members,
 * new-game form, recent games), the in-progress game (board + entry
 * + word list), or the end-of-game result panel.
 *
 * Chat layout follows the view:
 *
 * - Main: inline chat in the right column. There's room for it and
 *   chat is part of the "what's everyone up to" surface.
 * - Game / result: floating draggable chat (port of crossplay's
 *   `ChatPanel`). The board needs the page width during play, and
 *   chat shouldn't compete for it.
 *
 * The floating panel adds crossplay's behaviors: open/close,
 * persistent rect, `/` opens (or focuses if open), Esc closes,
 * unread badge on the reopen tab, and `!`-prefixed messages
 * force-open the panel on the receiver side.
 */

import {
  useEffect,
  useRef,
  useState,
  type FormEvent,
  type KeyboardEvent,
} from "react";
import { api, ApiError } from "../api";
import { useClubSocket, type LocalGuessRecord } from "../clubSocket";
import { Link } from "../routing";
import type {
  ChatMessage,
  ClubGameSummary,
  ClubSummary,
  GameConfig,
  GameResult,
  GameSnapshot,
  GuessResponse,
  MeResponse,
} from "../shared";
import { BoardStats } from "./BoardStats";
import { RotatableBoard } from "./RotatableBoard";
import { ChatPanel as FloatingChatPanel, ChatIndicator } from "./ChatPanel";
import { GameConstraints, EMPTY_CONSTRAINTS, type Constraints } from "./GameConstraints";
import { GameResultPanel } from "./GameResultPanel";
import { Timer } from "./Timer";
import { WordEntry } from "./WordEntry";
import { WordList } from "./WordList";
import { colorForHandle, OFFLINE_COLOR } from "../colors";
import { linkify } from "../linkify";
import styles from "./ClubPage.module.css";

type Props = {
  clubId: number;
  me: MeResponse;
};

type Feedback = { id: number; text: string; level: "info" | "warn" | "error" };

export function ClubPage({ clubId, me }: Props) {
  const {
    state,
    sendChat,
    sendNewGame,
    sendGuess,
    sendEndGame,
    clearResult,
    dismissFeedback,
  } = useClubSocket(clubId, me.user.id);
  const [history, setHistory] = useState<ClubGameSummary[] | null>(null);
  const [historyError, setHistoryError] = useState<string | null>(null);

  // Floating chat state. Only meaningful when the view is "playing"
  // or "result" (the main club view has inline chat always visible).
  const [chatOpen, setChatOpen] = useState(false);
  // Last chat id the user has "seen" — anything beyond this is
  // counted as unread on the reopen badge. Bumped to the latest id
  // whenever the panel is open and a new line lands, and on open.
  const [seenChatId, setSeenChatId] = useState(0);
  const floatingInputRef = useRef<HTMLTextAreaElement | null>(null);

  // Recent games list is REST-fetched (the WS doesn't carry past-
  // game history). Reload after gameEnded so a fresh result shows
  // up without a manual page reload.
  useEffect(() => {
    let cancelled = false;
    api.listClubGames(clubId)
      .then((rows) => { if (!cancelled) setHistory(rows); })
      .catch((err) => {
        if (cancelled) return;
        if (err instanceof ApiError) setHistoryError(err.detail);
        else setHistoryError("Network error");
      });
    return () => { cancelled = true; };
  }, [clubId, state.gameResult]);

  // Pick the view + whether chat is inline or floating off the
  // socket state.
  const mode: "main" | "playing" | "result" =
    state.gameResult !== null ? "result"
      : state.currentGame !== null ? "playing"
      : "main";
  const chatLayout: "inline" | "floating" = mode === "main" ? "inline" : "floating";

  // Keep `seenChatId` glued to the latest while the panel is open
  // (or chat is inline) so unread-count is always 0 in that case.
  useEffect(() => {
    if (chatLayout === "inline" || chatOpen) {
      const latest = state.chat.length > 0 ? state.chat[state.chat.length - 1].id : 0;
      setSeenChatId((cur) => (latest > cur ? latest : cur));
    }
  }, [chatLayout, chatOpen, state.chat]);

  // `!`-prefixed messages force-open the floating chat on the
  // recipient side — the "hey look at this!" mechanic. Tracked via
  // a ref so the effect can compare against the most recent line
  // it processed without depending on every chat update.
  const lastImportantSeenRef = useRef(0);
  useEffect(() => {
    if (chatLayout !== "floating") return;
    for (const line of state.chat) {
      if (line.id <= lastImportantSeenRef.current) continue;
      lastImportantSeenRef.current = line.id;
      if (line.text.startsWith("!") && line.handle !== me.user.handle) {
        setChatOpen(true);
      }
    }
  }, [state.chat, chatLayout, me.user.handle]);

  // `/` keystroke: focus the chat input from anywhere outside
  // another input/textarea. In floating mode it opens the panel
  // first if closed.
  useEffect(() => {
    function onKey(e: globalThis.KeyboardEvent) {
      if (e.metaKey || e.ctrlKey || e.altKey) return;
      const target = e.target as HTMLElement | null;
      const tag = target?.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA") return;
      if (e.key === "/") {
        e.preventDefault();
        if (chatLayout === "floating") {
          if (!chatOpen) setChatOpen(true);
          // Focus runs once the input mounts; the effect inside
          // FloatingChatPanel handles initial-focus, but for an
          // already-open panel we focus directly here.
          setTimeout(() => floatingInputRef.current?.focus(), 0);
        } else {
          // Inline chat is always visible; just focus its input.
          const el = document.querySelector<HTMLInputElement>(
            `.${styles.chat} input`,
          );
          el?.focus();
        }
      } else if (e.key === "Escape" && chatLayout === "floating" && chatOpen) {
        e.preventDefault();
        setChatOpen(false);
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [chatLayout, chatOpen]);

  // --- Loading / closed states -------------------------------------------

  if (state.closeCode !== null && !state.hydrated) {
    return (
      <ClubError
        clubId={clubId}
        code={state.closeCode}
        myHandle={me.user.handle}
      />
    );
  }
  if (!state.hydrated) {
    return <main style={{ padding: "2rem" }}>Loading…</main>;
  }

  // --- Main view ---------------------------------------------------------

  const allOnline = state.members.every((m) => m.online);
  const unreadMessages = state.chat.filter((l) => l.id > seenChatId);
  const unreadCount = unreadMessages.length;
  // Most-recent unread sender's color drives the indicator's
  // background, telegraphing "who do you owe a reply to."
  const unreadColor =
    unreadCount > 0
      ? colorForHandle(unreadMessages[unreadMessages.length - 1].handle)
      : null;

  return (
    <div className={styles.wrapper}>
      <header className={styles.header}>
        <h1>{state.clubName}</h1>
        <Link to="/">← Home</Link>
      </header>

      {!state.connected && (
        <div className={styles.statusBanner}>
          Reconnecting…  (refresh if this persists)
        </div>
      )}

      <div className={chatLayout === "inline" ? styles.layout : styles.layoutFull}>
        <div>
          {mode === "result" && state.gameResult !== null && (
            <ResultView
              result={state.gameResult}
              viewerUserId={me.user.id}
              onDismiss={clearResult}
            />
          )}
          {mode === "playing" && state.currentGame !== null && (
            <PlayView
              snapshot={state.currentGame}
              guesses={state.yourGuesses}
              myUserId={me.user.id}
              onGuess={sendGuess}
              onEndGame={sendEndGame}
            />
          )}
          {mode === "main" && (
            <ClubMainView
              clubId={clubId}
              me={me}
              members={state.members}
              allOnline={allOnline}
              connected={state.connected}
              history={history}
              historyError={historyError}
              lastConfig={state.lastConfig}
              onStart={sendNewGame}
            />
          )}
        </div>

        {chatLayout === "inline" && (
          <InlineChatPanel
            chat={state.chat}
            feedback={state.feedback}
            onSend={sendChat}
            onDismissFeedback={dismissFeedback}
            disabled={!state.connected}
          />
        )}
      </div>

      {chatLayout === "floating" && (
        <>
          {chatOpen && (
            <FloatingChatPanel
              myHandle={me.user.handle}
              messages={state.chat}
              feedback={state.feedback}
              onSend={sendChat}
              onClose={() => setChatOpen(false)}
              onDismissFeedback={dismissFeedback}
              disabled={!state.connected}
              inputRef={floatingInputRef}
            />
          )}
          {/* Indicator stays visible whether the panel is open or
              closed — clicking it toggles. While open, unreadCount
              is forced to 0 by the seenChatId effect, so the
              indicator naturally goes subtle + no badge. */}
          <ChatIndicator
            unreadCount={unreadCount}
            unreadColor={unreadColor}
            open={chatOpen}
            onToggle={() => setChatOpen((o) => !o)}
          />
        </>
      )}
    </div>
  );
}

// --- Main club view (no active game) ------------------------------------

type ClubMainProps = {
  clubId: number;
  me: MeResponse;
  members: { user_id: number; handle: string; online: boolean }[];
  allOnline: boolean;
  connected: boolean;
  history: ClubGameSummary[] | null;
  historyError: string | null;
  lastConfig: GameConfig | null;
  onStart: (config: GameConfig) => void;
};

function ClubMainView(props: ClubMainProps) {
  const [dialogOpen, setDialogOpen] = useState(false);
  const disabled = !props.allOnline || !props.connected;
  const disabledReason = !props.connected
    ? "Reconnect to start a game."
    : !props.allOnline
    ? "Waiting for everyone to be in the club."
    : null;

  return (
    <>
      <section className={styles.section}>
        <h2>Members</h2>
        <ul className={styles.members}>
          {props.members.map((m) => {
            const color = m.online ? colorForHandle(m.handle) : OFFLINE_COLOR;
            return (
              <li key={m.user_id} className={m.online ? "" : styles.memberOffline}>
                <span
                  className={styles.dot}
                  style={{ background: color }}
                  title={m.online ? "in club" : "not in club"}
                />
                <span className={styles.handle} style={{ color }}>
                  {m.handle}
                </span>
                {m.user_id === props.me.user.id && (
                  <span className={styles.you}>(you)</span>
                )}
              </li>
            );
          })}
        </ul>
      </section>

      <section className={styles.section}>
        <h2>New game</h2>
        <div className={styles.newGameRow}>
          {props.lastConfig !== null && (
            <button
              disabled={disabled}
              onClick={() => props.onStart(props.lastConfig!)}
              title={describeConfig(props.lastConfig)}
            >
              ▶ Play again
            </button>
          )}
          <button
            className={props.lastConfig !== null ? "secondary" : ""}
            disabled={disabled}
            onClick={() => setDialogOpen(true)}
          >
            {props.lastConfig !== null ? "New game…" : "▶ New game…"}
          </button>
          {disabledReason && (
            <span className={styles.disabledNote}>{disabledReason}</span>
          )}
        </div>
        {dialogOpen && (
          <NewGameDialog
            initial={props.lastConfig}
            onCancel={() => setDialogOpen(false)}
            onStart={(cfg) => {
              setDialogOpen(false);
              props.onStart(cfg);
            }}
          />
        )}
      </section>

      <section className={styles.section}>
        <h2>Recent games</h2>
        {props.historyError && <p className={styles.empty}>{props.historyError}</p>}
        {props.history === null && !props.historyError && (
          <p className={styles.empty}>Loading…</p>
        )}
        {props.history !== null && props.history.length === 0 && (
          <p className={styles.empty}>No games yet.</p>
        )}
        {props.history && props.history.length > 0 && (
          <ul className={styles.gameList}>
            {props.history.map((g) => (
              <li key={g.game_id}>
                <div>
                  {g.players
                    .map((p) => `${p.handle} ${p.final_total}`)
                    .join(" · ")}
                </div>
                <div className={styles.gameMeta}>
                  {new Date(g.started_at).toLocaleString()} · {g.dice_set}
                </div>
              </li>
            ))}
          </ul>
        )}
      </section>
    </>
  );
}

// --- New game form ------------------------------------------------------

const DICE_SETS: readonly { name: string; label: string }[] = [
  { name: "4-classic",     label: "4×4 Classic" },
  { name: "4",             label: "4×4 Revised" },
  { name: "5-orig",        label: "5×5 Original" },
  { name: "5-challenge",   label: "5×5 Challenge" },
  { name: "5-big-deluxe",  label: "5×5 Big Deluxe" },
  { name: "5",             label: "5×5 Big 2012" },
  { name: "6-super",       label: "6×6 Super Big" },
  { name: "6",             label: "6×6 Super Big Simple" },
];

/** Unified timer-mode list. Each row is a (seconds, direction)
 *  pair so the dropdown can offer countdown durations and the two
 *  open-ended modes (count-up, untimed) in one place. CLAUDE.md
 *  treats ``timer_direction`` as a display knob — Count up and
 *  Untimed differ only in render; both end on an explicit
 *  ``endGame``. */
type TimerMode = {
  id: string;
  label: string;
  seconds: number | null;
  direction: "down" | "up";
};

const TIMER_MODES: readonly TimerMode[] = [
  { id: "60",      label: "1 minute",    seconds: 60,   direction: "down" },
  { id: "90",      label: "1.5 minutes", seconds: 90,   direction: "down" },
  { id: "120",     label: "2 minutes",   seconds: 120,  direction: "down" },
  { id: "180",     label: "3 minutes",   seconds: 180,  direction: "down" },
  { id: "240",     label: "4 minutes",   seconds: 240,  direction: "down" },
  { id: "300",     label: "5 minutes",   seconds: 300,  direction: "down" },
  { id: "420",     label: "7 minutes",   seconds: 420,  direction: "down" },
  { id: "600",     label: "10 minutes",  seconds: 600,  direction: "down" },
  { id: "countup", label: "Count up",    seconds: null, direction: "up" },
  { id: "untimed", label: "Untimed",     seconds: null, direction: "down" },
];

/** Named scoring ladders mirroring ``wsboggle.scoring.LADDERS``.
 *  Server is the source of truth; the client only needs the names
 *  + display labels. */
const SCORING_LADDERS: readonly { name: string; label: string }[] = [
  { name: "basic", label: "Basic: 1–11" },
  { name: "flat",  label: "Flat: 1" },
  { name: "fib",   label: "Fibonacci: 1–377" },
  { name: "big",   label: "Prefer big: 1–50" },
];

/** Map a GameConfig back to a TIMER_MODES id so the dialog can
 *  pre-fill the dropdown from last_config. Brand-new clubs land
 *  on the 3-minute default. */
function configToModeId(c: GameConfig | null): string {
  if (c === null) return "180";
  if (c.timer_seconds === null) {
    return c.timer_direction === "up" ? "countup" : "untimed";
  }
  return String(c.timer_seconds);
}

/** Build a fresh GameConfig, merging the v1-exposed knobs with
 *  whatever the previous config used for the v2-only knobs (mode,
 *  dupes_cancel, etc.). Passing the last config through preserves
 *  any future knob choices a club picks up. */
function buildConfig(
  diceSet: string,
  mode: TimerMode,
  scoringLadder: string,
  gameMode: "competitive" | "collaborative",
  constraints: Constraints,
  base: GameConfig | null,
): GameConfig {
  return {
    dice_set: diceSet,
    scoring_ladder: scoringLadder,
    min_legal_length: base?.min_legal_length ?? 3,
    mode: gameMode,
    // Dupes-cancel only matters in competitive; collaborative
    // dedups across players before counting, so the flag is moot
    // there. Default true (the Boggle classic rule).
    dupes_cancel: base?.dupes_cancel ?? true,
    timer_seconds: mode.seconds,
    timer_direction: mode.direction,
    min_words: constraints.min_words,
    max_words: constraints.max_words,
    min_score: constraints.min_score,
    max_score: constraints.max_score,
    min_longest: constraints.min_longest,
    max_longest: constraints.max_longest,
  };
}

/** Pull the six constraint values out of an existing config so the
 *  dialog can pre-fill from last_config. Carries nulls through. */
function constraintsFromConfig(c: GameConfig | null): Constraints {
  if (c === null) return EMPTY_CONSTRAINTS;
  return {
    min_words: c.min_words,
    max_words: c.max_words,
    min_score: c.min_score,
    max_score: c.max_score,
    min_longest: c.min_longest,
    max_longest: c.max_longest,
  };
}

/** Short, hover-friendly description of a config for the "Play
 *  again" button title — so the user knows what they're about to
 *  start before they click. */
function describeConfig(c: GameConfig | null): string {
  if (c === null) return "";
  const dice = DICE_SETS.find((d) => d.name === c.dice_set)?.label ?? c.dice_set;
  const mode =
    TIMER_MODES.find(
      (m) => m.seconds === c.timer_seconds && m.direction === c.timer_direction,
    )?.label ??
    (c.timer_seconds === null ? "Untimed" : `${c.timer_seconds}s`);
  return `${dice} · ${mode}`;
}

type NewGameDialogProps = {
  /** Pre-fill the form from this config when provided. */
  initial: GameConfig | null;
  onCancel: () => void;
  onStart: (config: GameConfig) => void;
};

/** Modal dialog with the dice / timer dropdowns. Pre-fills from
 *  ``initial`` (the club's last config) so picking a small variation
 *  on the last game is one dropdown change + Start.
 *
 *  Backdrop click and Esc both cancel; Enter on the form submits.
 *  No portal — the dialog renders inline beneath the club view and
 *  the fixed positioning + z-index takes it visually full-screen.
 */
function NewGameDialog({ initial, onCancel, onStart }: NewGameDialogProps) {
  const [diceSet, setDiceSet] = useState(initial?.dice_set ?? "4");
  const [modeId, setModeId] = useState(configToModeId(initial));
  const [scoringLadder, setScoringLadder] = useState(
    initial?.scoring_ladder ?? "basic",
  );
  const [gameMode, setGameMode] = useState<"competitive" | "collaborative">(
    initial?.mode ?? "competitive",
  );
  const [constraints, setConstraints] = useState<Constraints>(
    constraintsFromConfig(initial),
  );

  useEffect(() => {
    function onKey(e: globalThis.KeyboardEvent) {
      if (e.key === "Escape") {
        e.preventDefault();
        onCancel();
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onCancel]);

  function submit(e: FormEvent) {
    e.preventDefault();
    const mode = TIMER_MODES.find((m) => m.id === modeId) ?? TIMER_MODES[3];
    onStart(
      buildConfig(diceSet, mode, scoringLadder, gameMode, constraints, initial),
    );
  }

  return (
    <div className={styles.dialogBackdrop} onClick={onCancel}>
      <div className={styles.dialog} onClick={(e) => e.stopPropagation()}>
        <h3>New game</h3>
        <form onSubmit={submit}>
          <label className={styles.dialogField}>
            <span>Tileset</span>
            <select
              value={diceSet}
              onChange={(e) => setDiceSet(e.target.value)}
              autoFocus
            >
              {DICE_SETS.map((d) => (
                <option key={d.name} value={d.name}>{d.label}</option>
              ))}
            </select>
          </label>
          <label className={styles.dialogField}>
            <span>Timer</span>
            <select
              value={modeId}
              onChange={(e) => setModeId(e.target.value)}
            >
              {TIMER_MODES.map((m) => (
                <option key={m.id} value={m.id}>{m.label}</option>
              ))}
            </select>
          </label>
          <label className={styles.dialogField}>
            <span>Mode</span>
            <select
              value={gameMode}
              onChange={(e) =>
                setGameMode(e.target.value as "competitive" | "collaborative")
              }
            >
              <option value="competitive">
                Competitive — private lists, dupes cancel
              </option>
              <option value="collaborative">
                Collaborative — shared list, dedup across players
              </option>
            </select>
          </label>
          <label className={styles.dialogField}>
            <span>Scoring</span>
            <select
              value={scoringLadder}
              onChange={(e) => setScoringLadder(e.target.value)}
            >
              {SCORING_LADDERS.map((l) => (
                <option key={l.name} value={l.name}>{l.label}</option>
              ))}
            </select>
          </label>
          <GameConstraints value={constraints} onChange={setConstraints} />
          <div className={styles.dialogActions}>
            <button type="button" className="secondary" onClick={onCancel}>
              Cancel
            </button>
            <button type="submit">▶ Start</button>
          </div>
        </form>
      </div>
    </div>
  );
}

// --- Play view ----------------------------------------------------------

type PlayProps = {
  snapshot: GameSnapshot;
  guesses: LocalGuessRecord[];
  myUserId: number;
  onGuess: (word: string) => Promise<GuessResponse>;
  onEndGame: () => void;
};

function PlayView({ snapshot, guesses, myUserId, onGuess, onEndGame }: PlayProps) {
  return (
    <section className={styles.section}>
      <div className={styles.playHeader}>
        <h2>Game in progress</h2>
        <div className={styles.timer}>
          <Timer
            startedAt={snapshot.started_at}
            endsAt={snapshot.ends_at}
            serverNow={snapshot.server_now}
            direction={snapshot.config.timer_direction}
          />
        </div>
      </div>
      <div className={styles.playArea}>
        <div className={styles.boardColumn}>
          <RotatableBoard board={snapshot.board} />
          <div className={styles.entry}>
            <WordEntry onSubmit={onGuess} />
          </div>
          <div className={styles.endRow}>
            <button className="secondary" onClick={onEndGame}>
              End game
            </button>
          </div>
        </div>
        <div className={styles.rightCol}>
          <BoardStats
            stats={snapshot.board_stats}
            guesses={guesses}
            collaborative={snapshot.config.mode === "collaborative"}
          />
          <WordList
            guesses={guesses}
            myUserId={myUserId}
            className={styles.wordListCol}
          />
        </div>
      </div>
    </section>
  );
}

// --- Result view --------------------------------------------------------

type ResultProps = {
  result: GameResult;
  viewerUserId: number;
  onDismiss: () => void;
};

function ResultView({ result, viewerUserId, onDismiss }: ResultProps) {
  return (
    <section className={styles.section}>
      <GameResultPanel result={result} viewerUserId={viewerUserId} />
      <div className={styles.resultActions}>
        <button onClick={onDismiss}>Return to club</button>
      </div>
    </section>
  );
}

// --- Inline chat panel (main club view only) ----------------------------

type InlineChatProps = {
  chat: ChatMessage[];
  feedback: Feedback[];
  onSend: (text: string) => void;
  onDismissFeedback: (id: number) => void;
  disabled: boolean;
};

/** The chat panel rendered in the main club view's right column. Same lines,
 *  same `!`-bold and URL-linkify behavior as the floating panel —
 *  only the container differs (inline vs draggable). */
function InlineChatPanel({
  chat,
  feedback,
  onSend,
  onDismissFeedback,
  disabled,
}: InlineChatProps) {
  const [draft, setDraft] = useState("");
  const logRef = useRef<HTMLDivElement>(null);

  // Auto-scroll the log to the bottom on every new line. Skipping
  // smooth scroll keeps fast bursts (catch-up replay on reconnect)
  // from animating forever.
  useEffect(() => {
    const el = logRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [chat]);

  function submit(e: FormEvent) {
    e.preventDefault();
    if (!draft.trim()) return;
    onSend(draft);
    setDraft("");
  }

  function onInputKey(e: KeyboardEvent<HTMLInputElement>) {
    // Swallow `/` when the user is typing it into the chat input —
    // otherwise the page-level handler would refocus the same
    // input mid-keystroke. Browsers do call onKeyDown before the
    // global one, but the page handler ignores keystrokes whose
    // target is an input, so this is mostly a doc comment.
    if (e.key === "Escape") (e.target as HTMLInputElement).blur();
  }

  return (
    <section className={`${styles.section} ${styles.chat}`}>
      <h2>Chat</h2>
      {feedback.map((f) => (
        <div
          key={f.id}
          className={styles.feedback}
          onClick={() => onDismissFeedback(f.id)}
          title="dismiss"
        >
          {f.text}
        </div>
      ))}
      <div className={styles.chatLog} ref={logRef}>
        {chat.length === 0 ? (
          <p className={styles.empty}>No messages yet.</p>
        ) : (
          chat.map((line) => {
            const important = line.text.startsWith("!");
            const body = important ? line.text.slice(1) : line.text;
            return (
              <div key={line.id} className={styles.chatLine}>
                <span
                  className={styles.chatHandle}
                  style={{ color: colorForHandle(line.handle) }}
                >
                  {line.handle}
                </span>
                <span style={{ fontWeight: important ? 700 : 400 }}>
                  {linkify(body)}
                </span>
              </div>
            );
          })
        )}
      </div>
      <form className={styles.chatInputRow} onSubmit={submit}>
        <input
          type="text"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={onInputKey}
          placeholder={disabled ? "(disconnected)" : "Say something…"}
          disabled={disabled}
          maxLength={2000}
        />
        <button type="submit" disabled={disabled || !draft.trim()}>
          Send
        </button>
      </form>
    </section>
  );
}

// --- Error pane ---------------------------------------------------------

type ClubErrorProps = {
  clubId: number;
  code: number;
  myHandle: string;
};

function ClubError({ clubId, code, myHandle }: ClubErrorProps) {
  // For 4403 (not a member), pull the public summary so we can
  // show the actual club name + member list — the visitor needs to
  // know *who* to ask for an invite. 4404 doesn't need a fetch
  // (there's nothing to display). 4401 means we already lost our
  // session, so don't bother either.
  const [summary, setSummary] = useState<ClubSummary | null>(null);
  useEffect(() => {
    if (code !== 4403) return;
    let cancelled = false;
    api.getClub(clubId)
      .then((s) => { if (!cancelled) setSummary(s); })
      .catch(() => { /* fall back to the generic message */ });
    return () => { cancelled = true; };
  }, [code, clubId]);

  if (code === 4403) {
    return (
      <main style={{ padding: "2rem", maxWidth: 480, margin: "0 auto" }}>
        <h1>{summary ? `Not in ${summary.name}` : "Not in this club"}</h1>
        <p>
          You're signed in as <strong>@{myHandle}</strong>, but you're
          not a member of this club.
        </p>
        {summary && summary.member_handles.length > 0 && (
          <>
            <p>Ask one of these members to add you:</p>
            <ul>
              {summary.member_handles.map((h) => (
                <li key={h} style={{ color: colorForHandle(h) }}>
                  @{h}
                </li>
              ))}
            </ul>
          </>
        )}
        <p><Link to="/">← Home</Link></p>
      </main>
    );
  }

  let message: string;
  switch (code) {
    case 4401:
      message = "Your session expired. Sign in again.";
      break;
    case 4404:
      message = "This club doesn't exist.";
      break;
    default:
      message = `Connection closed (code ${code}).`;
  }
  return (
    <main style={{ padding: "2rem" }}>
      <h1>Can't open club</h1>
      <p>{message}</p>
      <p><Link to="/">← Home</Link></p>
    </main>
  );
}
