/**
 * Tiny WebSocket client for the club socket.
 *
 * Exposes a hook that owns one connection's lifecycle: opens on
 * mount, dispatches server messages into a reducer, exposes a
 * stable `send` function, closes on unmount.
 *
 * Reconnect is intentionally out of scope for this milestone — a
 * dropped socket shows as `connected: false` and the user can
 * reload. (When the game flow lands we'll want auto-reconnect with
 * a fresh `clubState` on resume; doing that well needs careful
 * thinking about in-progress game state, so we defer.)
 *
 * The hook returns a single `state` snapshot rather than several
 * pieces so React batches re-renders cleanly and consumers can
 * destructure exactly what they need.
 */

import { useEffect, useReducer, useRef } from "react";
import type {
  ChatMessage,
  ClientMessage,
  ClubMember,
  GameConfig,
  GameResult,
  GameSnapshot,
  GuessRecord,
  GuessResponse,
  ServerMessage,
} from "./shared";

export type ClubSocketState = {
  /** True while the underlying WebSocket is OPEN. */
  connected: boolean;
  /** True once the first `clubState` has been received. */
  hydrated: boolean;
  /** Optional close code if the socket closed (4401/4403/4404 etc). */
  closeCode: number | null;

  // Snapshot fields (populated from `clubState` and patched by deltas).
  clubName: string;
  members: ClubMember[];
  chat: ChatMessage[];

  /** Active game's snapshot (board, timer, config), or null when
   *  the club is in the lobby. Updated by clubState (on connect),
   *  gameStarted (new game), and cleared on gameEnded. */
  currentGame: GameSnapshot | null;
  /** Your own guesses for the active game. Seeded from
   *  `clubState.current_game.your_guesses` and patched on each
   *  guessAccepted. */
  yourGuesses: GuessRecord[];
  /** End-of-game payload, populated on `gameEnded`. The consumer
   *  shows it and then clears it (via `clearResult`) when the
   *  user opts back into the lobby. */
  gameResult: GameResult | null;
  /** Config of the club's most recently-started game. Used by the
   *  lobby to pre-fill the new-game dialog and surface a one-click
   *  "Play again". Null for clubs that have never played. Updates
   *  on clubState (initial) and gameStarted (when a fresh game
   *  becomes the "last"). */
  lastConfig: GameConfig | null;

  /** Transient toasts queued by server `feedback`; consumer is
   *  expected to render + clear. */
  feedback: { id: number; text: string; level: "info" | "warn" | "error" }[];
};

type Action =
  | { kind: "open" }
  | { kind: "close"; code: number | null }
  | { kind: "message"; msg: ServerMessage }
  | { kind: "dismissFeedback"; id: number }
  | { kind: "clearResult" };

const initialState: ClubSocketState = {
  connected: false,
  hydrated: false,
  closeCode: null,
  clubName: "",
  members: [],
  chat: [],
  currentGame: null,
  yourGuesses: [],
  gameResult: null,
  lastConfig: null,
  feedback: [],
};

let _feedbackSeq = 0;

function reducer(state: ClubSocketState, action: Action): ClubSocketState {
  switch (action.kind) {
    case "open":
      return { ...state, connected: true, closeCode: null };
    case "close":
      return { ...state, connected: false, closeCode: action.code };
    case "dismissFeedback":
      return {
        ...state,
        feedback: state.feedback.filter((f) => f.id !== action.id),
      };
    case "clearResult":
      return { ...state, gameResult: null };
    case "message": {
      const msg = action.msg;
      switch (msg.type) {
        case "clubState":
          return {
            ...state,
            hydrated: true,
            clubName: msg.name,
            members: msg.members,
            chat: msg.chat,
            currentGame: msg.current_game,
            yourGuesses: msg.current_game?.your_guesses ?? [],
            gameResult: null,
            lastConfig: msg.last_config,
          };
        case "chatMessage":
          return { ...state, chat: [...state.chat, msg.message] };
        case "memberPresence":
          return {
            ...state,
            members: state.members.map((m) =>
              m.user_id === msg.user_id ? { ...m, online: msg.online } : m,
            ),
          };
        case "feedback":
          return {
            ...state,
            feedback: [
              ...state.feedback,
              { id: ++_feedbackSeq, text: msg.text, level: msg.level },
            ],
          };
        case "gameStarted":
          return {
            ...state,
            currentGame: msg.snapshot,
            yourGuesses: msg.snapshot.your_guesses,
            gameResult: null,
            lastConfig: msg.snapshot.config,
          };
        case "guessAccepted":
          // Append to the word list — both legal and the three
          // illegal-but-recorded cases. Duplicates / inactive
          // wouldn't have come through here (they'd be
          // guessRejected and not added).
          return {
            ...state,
            yourGuesses: [
              ...state.yourGuesses,
              {
                word: msg.word,
                is_legal: msg.result === "accepted",
                points: msg.points,
              },
            ],
          };
        case "guessRejected":
          // No yourGuesses mutation — duplicates / inactive don't
          // join the list.
          return state;
        case "gameEnded":
          return {
            ...state,
            currentGame: null,
            yourGuesses: [],
            gameResult: msg.result,
          };
      }
    }
  }
}

function wsUrl(clubId: number): string {
  const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
  return `${proto}//${window.location.host}/ws/clubs/${clubId}`;
}

export type ClubSocketHandle = {
  state: ClubSocketState;
  sendChat: (text: string) => void;
  sendNewGame: (config: GameConfig) => void;
  /** Submit a guess; resolves with the server's verdict translated
   *  into the same `GuessResponse` shape the solo HTTP path uses,
   *  so the `WordEntry` component plugs in unchanged. */
  sendGuess: (word: string) => Promise<GuessResponse>;
  clearResult: () => void;
  dismissFeedback: (id: number) => void;
};

export function useClubSocket(clubId: number): ClubSocketHandle {
  const [state, dispatch] = useReducer(reducer, initialState);
  // The WebSocket itself lives in a ref so the send-helpers are
  // stable across renders; consumers can put them in event handlers
  // without re-binding.
  const wsRef = useRef<WebSocket | null>(null);

  // Pending guess promises, keyed by the *normalized* (trimmed +
  // lowercased) word — same key the server uses for dedupe. When a
  // guessAccepted / guessRejected arrives, we look up by word and
  // resolve. If the server burped and never replies, the promise
  // never settles; a timeout could be added if it matters in real
  // use, but Boggle's submit cadence is slow enough that a manual
  // re-type is fine.
  const pendingRef = useRef<Map<string, (r: GuessResponse) => void>>(new Map());

  useEffect(() => {
    const ws = new WebSocket(wsUrl(clubId));
    wsRef.current = ws;
    const pending = pendingRef.current;

    ws.onopen = () => {
      dispatch({ kind: "open" });
      send(ws, { type: "hello" });
    };
    ws.onmessage = (ev) => {
      let msg: ServerMessage;
      try {
        msg = JSON.parse(ev.data) as ServerMessage;
      } catch {
        return;
      }
      dispatch({ kind: "message", msg });

      // Resolve any pending guess promise this message answers.
      if (msg.type === "guessAccepted") {
        const resolve = pending.get(msg.word);
        if (resolve) {
          pending.delete(msg.word);
          resolve({
            word: msg.word,
            result: msg.result,
            points: msg.points,
          });
        }
      } else if (msg.type === "guessRejected") {
        const resolve = pending.get(msg.word);
        if (resolve) {
          pending.delete(msg.word);
          resolve({
            word: msg.word,
            result: msg.reason,
            points: 0,
          });
        }
      }
    };
    ws.onclose = (ev) => {
      dispatch({ kind: "close", code: ev.code });
      // Any outstanding guesses can't be answered now — resolve them
      // as game_inactive so the UI moves on.
      for (const [word, resolve] of pending) {
        resolve({ word, result: "game_inactive", points: 0 });
      }
      pending.clear();
    };
    ws.onerror = () => {
      // The close event will follow with the real code.
    };

    return () => {
      ws.close();
      wsRef.current = null;
      pending.clear();
    };
  }, [clubId]);

  function sendChat(text: string) {
    const trimmed = text.trim();
    if (!trimmed) return;
    const ws = wsRef.current;
    if (!ws || ws.readyState !== WebSocket.OPEN) return;
    send(ws, { type: "chat", text: trimmed });
  }

  function sendNewGame(config: GameConfig) {
    const ws = wsRef.current;
    if (!ws || ws.readyState !== WebSocket.OPEN) return;
    send(ws, { type: "newGame", config });
  }

  function sendGuess(word: string): Promise<GuessResponse> {
    const normalized = word.trim().toLowerCase();
    const ws = wsRef.current;
    if (!ws || ws.readyState !== WebSocket.OPEN) {
      return Promise.resolve({
        word: normalized,
        result: "game_inactive",
        points: 0,
      });
    }
    return new Promise<GuessResponse>((resolve) => {
      // Last writer wins if the same word is sent twice in a row
      // before the first reply arrives. Boggle has no rapid-burst
      // legitimate use case for that.
      pendingRef.current.set(normalized, resolve);
      send(ws, { type: "guess", word });
    });
  }

  function clearResult() {
    dispatch({ kind: "clearResult" });
  }

  function dismissFeedback(id: number) {
    dispatch({ kind: "dismissFeedback", id });
  }

  return { state, sendChat, sendNewGame, sendGuess, clearResult, dismissFeedback };
}

function send(ws: WebSocket, msg: ClientMessage): void {
  ws.send(JSON.stringify(msg));
}
