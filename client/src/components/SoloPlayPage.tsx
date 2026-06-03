/**
 * Solo play page.
 *
 * Lifecycle:
 *  1. Mount → fetch the game via `GET /api/games/:id`.
 *  2. If the response already has a `result`, render the result panel
 *     (we landed here via review or after the timer expired between
 *     sessions).
 *  3. Otherwise render the play UI (board + timer + word entry +
 *     word list). When the timer hits zero, post `/end` and flip into
 *     the result view.
 *  4. Manual "End game" button does the same.
 *
 * `your_guesses` from the snapshot seeds the in-page word list, so
 * a refresh mid-game (or a timer-expired reload) carries history
 * forward.
 */

import { useCallback, useEffect, useState } from "react";
import { api, ApiError } from "../api";
import { navigate } from "../routing";
import type {
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
import styles from "./SoloPlayPage.module.css";

type Props = {
  /** The game to load. Extracted from `/solo/:id`. */
  gameId: number;
  /** The signed-in user. Currently unused inside this component but
   *  passed in so future per-viewer features (multiplayer scoreboards)
   *  can branch off it without an extra fetch. */
  me: MeResponse;
};

type PageState =
  | { kind: "loading" }
  | { kind: "playing"; snapshot: GameSnapshot; guesses: GuessRecord[] }
  | { kind: "ended"; snapshot: GameSnapshot; result: GameResult }
  | { kind: "error"; message: string };

export function SoloPlayPage({ gameId }: Props) {
  const [state, setState] = useState<PageState>({ kind: "loading" });

  // Load on mount.
  useEffect(() => {
    let cancelled = false;
    api.getGame(gameId)
      .then((view) => {
        if (cancelled) return;
        if (view.result) {
          setState({ kind: "ended", snapshot: view.snapshot, result: view.result });
        } else {
          setState({
            kind: "playing",
            snapshot: view.snapshot,
            guesses: view.snapshot.your_guesses,
          });
        }
      })
      .catch((err) => {
        if (cancelled) return;
        const msg = err instanceof ApiError
          ? (err.status === 404 ? "Game not found" : err.detail)
          : "Network error";
        setState({ kind: "error", message: msg });
      });
    return () => { cancelled = true; };
  }, [gameId]);

  // Word submit — only meaningful while playing.
  const handleSubmit = useCallback(
    async (word: string): Promise<GuessResponse> => {
      const res = await api.guess(gameId, word);
      // Optimistically append to the local list (only for accepted &
      // illegal — duplicates we don't show twice).
      setState((s) => {
        if (s.kind !== "playing") return s;
        if (res.result === "already_submitted") return s;
        return {
          ...s,
          guesses: [...s.guesses, {
            word: res.word,
            is_legal: res.result === "accepted",
            points: res.points,
          }],
        };
      });
      return res;
    },
    [gameId],
  );

  // End-of-game transition (timer expired or user clicked End).
  const handleEnd = useCallback(async () => {
    try {
      const result = await api.endSolo(gameId);
      setState((s) =>
        s.kind === "playing"
          ? { kind: "ended", snapshot: s.snapshot, result }
          : s,
      );
    } catch (err) {
      const msg = err instanceof ApiError ? err.detail : "Network error";
      setState({ kind: "error", message: msg });
    }
  }, [gameId]);

  if (state.kind === "loading") {
    return <div className={styles.loading}>Loading game…</div>;
  }

  if (state.kind === "error") {
    return (
      <div className={styles.wrapper}>
        <h1>Couldn't load game</h1>
        <p>{state.message}</p>
        <button onClick={() => navigate("/")}>← Home</button>
      </div>
    );
  }

  if (state.kind === "ended") {
    return (
      <div className={styles.wrapper}>
        <GameResultPanel result={state.result} />
      </div>
    );
  }

  // Playing.
  const { snapshot, guesses } = state;
  return (
    <div className={styles.wrapper}>
      <header className={styles.header}>
        <h1>Solo game</h1>
        <div className={styles.timer}>
          <Timer
            endsAt={snapshot.ends_at}
            serverNow={snapshot.server_now}
            onExpired={handleEnd}
          />
        </div>
      </header>

      <div className={styles.playArea}>
        <div className={styles.boardColumn}>
          <Board board={snapshot.board} />
          <div className={styles.entry}>
            <WordEntry onSubmit={handleSubmit} />
          </div>
          <div className={styles.endAction}>
            <button className="secondary" onClick={handleEnd}>
              End game
            </button>
          </div>
        </div>

        <WordList guesses={guesses} className={styles.wordList} />
      </div>
    </div>
  );
}
