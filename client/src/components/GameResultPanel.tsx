import { navigate } from "../routing";
import type { GameResult } from "../shared";
import { Board } from "./Board";
import { colorForHandle } from "../colors";
import styles from "./GameResultPanel.module.css";

type Props = {
  /** End-of-game payload from `POST /api/solo/games/:id/end` or
   *  `GET /api/games/:id`. Always populated when this component
   *  renders. */
  result: GameResult;
  /** The current viewer's user id. When set, the viewer's block
   *  is pinned to the top of the players list (regardless of
   *  score) and labeled "You"; other players keep server's
   *  leaderboard order below. */
  viewerUserId?: number;
};

/** End-of-game result panel. For solo, `players` has one entry —
 *  the creator. Multiplayer puts the viewer's block first and
 *  shows every other player's list below.
 *
 *  The board is shown compact at the top so you can verify the path
 *  of any word — particularly useful when scanning the "missed" list
 *  to confirm a word you didn't see was actually reachable. */
export function GameResultPanel({ result, viewerUserId }: Props) {
  // Viewer first; others keep server's leaderboard order
  // (final_total desc). For solo this collapses to a single block.
  const players = [...result.players];
  if (viewerUserId !== undefined) {
    const idx = players.findIndex((p) => p.user_id === viewerUserId);
    if (idx > 0) {
      const [viewer] = players.splice(idx, 1);
      players.unshift(viewer);
    }
  }

  return (
    <div className={styles.panel}>
      <h2>Game ended</h2>

      <div className={styles.boardSlot}>
        <Board board={result.board} compact />
      </div>

      {players.map((p) => {
        const isViewer = p.user_id === viewerUserId;
        const color = colorForHandle(p.handle);
        return (
          <div
            key={p.user_id}
            className={styles.playerCard}
            style={{
              // Left accent in the player's color so the block is
              // visually claimed even before you read the handle.
              borderLeft: `4px solid ${color}`,
              ...(isViewer ? { background: "#f9fafb" } : {}),
            }}
          >
            <div className={styles.scoreLine}>
              <strong>{p.final_total}</strong> point{p.final_total === 1 ? "" : "s"}
              {" · "}
              {p.words.length} word{p.words.length === 1 ? "" : "s"}
              {" · "}
              <span className={styles.playerHandle} style={{ color }}>
                {p.handle}
              </span>
              {isViewer && <span className={styles.youTag}> (you)</span>}
            </div>

            {p.words.length === 0 ? (
              <p className={styles.missed}>(none)</p>
            ) : (
              <div className={styles.wordGrid}>
                {p.words.map((w, i) => (
                  <span
                    key={i}
                    style={{
                      color: w.shared_with.length > 0 ? "#9ca3af" : "#1f2937",
                    }}
                    title={w.shared_with.length > 0 ? "shared with another player — 0 pts" : ""}
                  >
                    {w.word} <em style={{ color: "#6b7280" }}>+{w.points}</em>
                  </span>
                ))}
              </div>
            )}
          </div>
        );
      })}

      <div className={styles.section}>
        <h3 className={styles.missed}>
          Missed ({result.missed_words.length})
        </h3>
        {result.missed_words.length === 0 ? (
          <p className={styles.missed}>(none — you got them all!)</p>
        ) : (
          <div className={styles.wordGrid}>
            {result.missed_words.map((m, i) => (
              <span key={i} className={styles.missed}>
                {m.word}
              </span>
            ))}
          </div>
        )}
      </div>

      <div className={styles.actions}>
        <button onClick={() => navigate("/solo")}>New game</button>
        <button className="secondary" onClick={() => navigate("/")}>
          Home
        </button>
      </div>
    </div>
  );
}
