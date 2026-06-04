/**
 * The club page (`/c/:id`).
 *
 * Owns one club WebSocket via :func:`useClubSocket` and renders the
 * lobby view: members + presence dots, inline chat panel, recent
 * games (REST), and a placeholder "New game" button.
 *
 * Game flow over the WS isn't wired yet — the button explains that
 * and disabled-state covers the "not all members present" rule
 * we'll need anyway.
 *
 * The draggable chat popup that crossplay uses is deferred — we
 * start with an inline panel beside the member list. CLAUDE.md
 * tags this as a port target (`useDraggablePanel`); it'll come
 * with the rest of the chat polish.
 */

import { useEffect, useRef, useState, type FormEvent } from "react";
import { api, ApiError } from "../api";
import { useClubSocket } from "../clubSocket";
import { Link } from "../routing";
import type {
  ClubGameSummary,
  GameConfig,
  GameResult,
  GameSnapshot,
  GuessRecord,
  GuessResponse,
  MeResponse,
} from "../shared";
import { Board } from "./Board";
import { GameResultPanel } from "./GameResultPanel";
import { Timer } from "./Timer";
import { WordEntry } from "./WordEntry";
import { WordList } from "./WordList";
import { colorForHandle, OFFLINE_COLOR } from "../colors";
import styles from "./ClubPage.module.css";

type Props = {
  clubId: number;
  me: MeResponse;
};

export function ClubPage({ clubId, me }: Props) {
  const {
    state,
    sendChat,
    sendNewGame,
    sendGuess,
    clearResult,
    dismissFeedback,
  } = useClubSocket(clubId);
  const [history, setHistory] = useState<ClubGameSummary[] | null>(null);
  const [historyError, setHistoryError] = useState<string | null>(null);

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

  // --- Loading / closed states -------------------------------------------

  if (state.closeCode !== null && !state.hydrated) {
    return (
      <ClubError
        code={state.closeCode}
        myHandle={me.user.handle}
      />
    );
  }
  if (!state.hydrated) {
    return <main style={{ padding: "2rem" }}>Loading…</main>;
  }

  // --- Main view ----------------------------------------------------------

  const allOnline = state.members.every((m) => m.online);

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

      <div className={styles.layout}>
        <div>
          {state.gameResult !== null ? (
            <ResultView
              result={state.gameResult}
              viewerUserId={me.user.id}
              onDismiss={clearResult}
            />
          ) : state.currentGame !== null ? (
            <PlayView
              snapshot={state.currentGame}
              guesses={state.yourGuesses}
              onGuess={sendGuess}
            />
          ) : (
            <LobbyView
              clubId={clubId}
              me={me}
              members={state.members}
              allOnline={allOnline}
              connected={state.connected}
              history={history}
              historyError={historyError}
              onStart={sendNewGame}
            />
          )}
        </div>

        <ChatPanel
          chat={state.chat}
          feedback={state.feedback}
          onSend={sendChat}
          onDismissFeedback={dismissFeedback}
          disabled={!state.connected}
        />
      </div>
    </div>
  );
}

// --- Lobby --------------------------------------------------------------

type LobbyProps = {
  clubId: number;
  me: MeResponse;
  members: { user_id: number; handle: string; online: boolean }[];
  allOnline: boolean;
  connected: boolean;
  history: ClubGameSummary[] | null;
  historyError: string | null;
  onStart: (config: GameConfig) => void;
};

function LobbyView(props: LobbyProps) {
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
        <NewGameForm
          disabled={!props.allOnline || !props.connected}
          disabledReason={
            !props.connected
              ? "Reconnect to start a game."
              : !props.allOnline
              ? "Waiting for everyone to be in the club."
              : null
          }
          onStart={props.onStart}
        />
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

const TIMERS: readonly { seconds: number; label: string }[] = [
  { seconds: 60,  label: "1 minute" },
  { seconds: 90,  label: "1.5 minutes" },
  { seconds: 120, label: "2 minutes" },
  { seconds: 180, label: "3 minutes" },
  { seconds: 240, label: "4 minutes" },
  { seconds: 300, label: "5 minutes" },
  { seconds: 420, label: "7 minutes" },
  { seconds: 600, label: "10 minutes" },
];

function buildConfig(diceSet: string, timerSeconds: number): GameConfig {
  return {
    dice_set: diceSet,
    scoring_ladder: "basic",
    min_legal_length: 3,
    mode: "competitive",
    dupes_cancel: true,
    timer_seconds: timerSeconds,
    timer_direction: "down",
    min_words: null,
    max_words: null,
    min_score: null,
    max_score: null,
    min_longest: null,
    max_longest: null,
  };
}

type NewGameFormProps = {
  disabled: boolean;
  disabledReason: string | null;
  onStart: (config: GameConfig) => void;
};

function NewGameForm({ disabled, disabledReason, onStart }: NewGameFormProps) {
  const [diceSet, setDiceSet] = useState("4");
  const [timer, setTimer] = useState(180);

  function submit(e: FormEvent) {
    e.preventDefault();
    if (disabled) return;
    onStart(buildConfig(diceSet, timer));
  }

  return (
    <form className={styles.newGameForm} onSubmit={submit}>
      <select
        value={diceSet}
        onChange={(e) => setDiceSet(e.target.value)}
        disabled={disabled}
      >
        {DICE_SETS.map((d) => (
          <option key={d.name} value={d.name}>{d.label}</option>
        ))}
      </select>
      <select
        value={timer}
        onChange={(e) => setTimer(Number(e.target.value))}
        disabled={disabled}
      >
        {TIMERS.map((t) => (
          <option key={t.seconds} value={t.seconds}>{t.label}</option>
        ))}
      </select>
      <button type="submit" disabled={disabled}>
        ▶ Start game
      </button>
      {disabledReason && (
        <span className={styles.disabledNote}>{disabledReason}</span>
      )}
    </form>
  );
}

// --- Play view ----------------------------------------------------------

type PlayProps = {
  snapshot: GameSnapshot;
  guesses: GuessRecord[];
  onGuess: (word: string) => Promise<GuessResponse>;
};

function PlayView({ snapshot, guesses, onGuess }: PlayProps) {
  return (
    <section className={styles.section}>
      <div className={styles.playHeader}>
        <h2>Game in progress</h2>
        <div className={styles.timer}>
          <Timer endsAt={snapshot.ends_at} serverNow={snapshot.server_now} />
        </div>
      </div>
      <div className={styles.playArea}>
        <div className={styles.boardColumn}>
          <Board board={snapshot.board} />
          <div className={styles.entry}>
            <WordEntry onSubmit={onGuess} />
          </div>
        </div>
        <WordList guesses={guesses} className={styles.wordListCol} />
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
        <button onClick={onDismiss}>Back to lobby</button>
      </div>
    </section>
  );
}

// --- Chat ---------------------------------------------------------------

type ChatPanelProps = {
  chat: { id: number; handle: string; text: string; ts: string }[];
  feedback: { id: number; text: string; level: string }[];
  onSend: (text: string) => void;
  onDismissFeedback: (id: number) => void;
  disabled: boolean;
};

function ChatPanel({ chat, feedback, onSend, onDismissFeedback, disabled }: ChatPanelProps) {
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
          chat.map((line) => (
            <div key={line.id} className={styles.chatLine}>
              <span
                className={styles.chatHandle}
                style={{ color: colorForHandle(line.handle) }}
              >
                {line.handle}
              </span>
              {line.text}
            </div>
          ))
        )}
      </div>
      <form className={styles.chatInputRow} onSubmit={submit}>
        <input
          type="text"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
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

function ClubError({ code }: { code: number; myHandle: string }) {
  let message: string;
  switch (code) {
    case 4401:
      message = "Your session expired. Sign in again.";
      break;
    case 4403:
      message = "You're not a member of this club.";
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
