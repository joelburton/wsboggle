import { useEffect, useState } from "react";
import { api } from "../api";
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
 *  Every word in the panel (each player's list + missed list) is a
 *  click target that pops a definition next to it. Definitions are
 *  cached per component-instance so re-clicking is instant; we lean
 *  on `api.define` to do the dictionary lookup. Click outside or
 *  press Esc to dismiss; clicking the same word toggles it closed.
 *
 *  The board is shown compact at the top so you can verify the path
 *  of any word — particularly useful when scanning the "missed" list
 *  to confirm a word you didn't see was actually reachable. */
export function GameResultPanel({ result, viewerUserId }: Props) {
  const collaborative = result.config.mode === "collaborative";

  // Viewer first; others keep server's leaderboard order
  // (final_total desc). For solo this collapses to a single block.
  // Collaborative results are already a single "team" entry so no
  // re-ordering is needed (and the viewer-pin doesn't make sense).
  const players = [...result.players];
  if (viewerUserId !== undefined && !collaborative) {
    const idx = players.findIndex((p) => p.user_id === viewerUserId);
    if (idx > 0) {
      const [viewer] = players.splice(idx, 1);
      players.unshift(viewer);
    }
  }

  // --- Definition lookup -----------------------------------------------

  // Currently-selected word (the popover anchors here). Lowercase
  // since that's the canonical form on the server.
  const [selected, setSelected] = useState<string | null>(null);
  // Per-word lookup state. ``undefined`` = never asked; ``"loading"``
  // = request in flight; ``string`` = found; ``null`` = not in dict.
  const [defs, setDefs] = useState<Record<string, "loading" | string | null>>({});

  function onWordClick(rawWord: string) {
    const word = rawWord.toLowerCase();
    if (selected === word) {
      setSelected(null);
      return;
    }
    setSelected(word);
    if (defs[word] === undefined) {
      setDefs((d) => ({ ...d, [word]: "loading" }));
      api.define(word).then(
        (res) => setDefs((d) => ({ ...d, [word]: res.definition })),
        () => setDefs((d) => ({ ...d, [word]: null })),
      );
    }
  }

  // Esc clears the popover, regardless of focus.
  useEffect(() => {
    if (selected === null) return;
    function onKey(e: globalThis.KeyboardEvent) {
      if (e.key === "Escape") {
        e.preventDefault();
        setSelected(null);
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [selected]);

  return (
    <div className={styles.panel} onClick={() => setSelected(null)}>
      <h2>Game ended</h2>

      <div className={styles.boardSlot}>
        <Board board={result.board} compact />
      </div>

      {players.map((p) => {
        const isViewer = !collaborative && p.user_id === viewerUserId;
        const color = collaborative ? "#374151" : colorForHandle(p.handle);
        return (
          <div
            key={p.user_id}
            className={styles.playerCard}
            style={{
              // Left accent in the player's color so the block is
              // visually claimed even before you read the handle.
              // In collaborative mode the "player" is the whole
              // team — we use a neutral gray rather than a
              // confusing player-shade.
              borderLeft: `4px solid ${color}`,
              ...(isViewer ? { background: "#f9fafb" } : {}),
            }}
          >
            <div className={styles.scoreLine}>
              <strong>{p.final_total}</strong> point{p.final_total === 1 ? "" : "s"}
              {" · "}
              {p.words.length} word{p.words.length === 1 ? "" : "s"}
              {!collaborative && (
                <>
                  {" · "}
                  <span className={styles.playerHandle} style={{ color }}>
                    {p.handle}
                  </span>
                  {isViewer && <span className={styles.youTag}> (you)</span>}
                </>
              )}
            </div>

            {p.words.length === 0 ? (
              <p className={styles.missed}>(none)</p>
            ) : (
              <div className={styles.wordGrid}>
                {p.words.map((w, i) => (
                  <WordChip
                    key={i}
                    word={w.word}
                    points={w.points}
                    shared={w.shared_with.length > 0}
                    selected={selected === w.word.toLowerCase()}
                    def={defs[w.word.toLowerCase()]}
                    onClick={onWordClick}
                  />
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
              <WordChip
                key={i}
                word={m.word}
                missed
                selected={selected === m.word.toLowerCase()}
                def={defs[m.word.toLowerCase()]}
                onClick={onWordClick}
              />
            ))}
          </div>
        )}
      </div>

    </div>
  );
}

// --- Word chip with attached definition popover --------------------------

type WordChipProps = {
  word: string;
  /** Score for this word in this player's list. Omitted for missed
   *  words (no per-player score there). */
  points?: number;
  /** Shared with another player (competitive dupes-cancel) — render
   *  the word dim and skip the "+N" so the zero score is obvious. */
  shared?: boolean;
  /** A word in the "missed" section: muted color, no points. */
  missed?: boolean;
  selected: boolean;
  def: "loading" | string | null | undefined;
  onClick: (word: string) => void;
};

function WordChip({
  word, points, shared, missed, selected, def, onClick,
}: WordChipProps) {
  const baseColor = missed ? "#6b7280" : shared ? "#9ca3af" : "#1f2937";
  return (
    <span
      className={styles.wordWrap}
      onClick={(e) => e.stopPropagation()}
      /* Stops the click from bubbling to the panel-level "close on
         outside click" handler — we want clicking another word to
         open *that* word's def, not close the current one before
         opening. */
    >
      <button
        type="button"
        className={`${styles.wordBtn} ${selected ? styles.wordBtnSelected : ""}`}
        style={{ color: baseColor }}
        onClick={() => onClick(word)}
        title={shared ? "shared with another player — 0 pts" : undefined}
      >
        {word}
        {points !== undefined && !shared && (
          <em className={styles.wordPoints}>+{points}</em>
        )}
      </button>
      {selected && (
        <DefinitionPopover word={word} def={def} />
      )}
    </span>
  );
}

function DefinitionPopover({
  word,
  def,
}: {
  word: string;
  def: "loading" | string | null | undefined;
}) {
  let body: React.ReactNode;
  if (def === undefined || def === "loading") {
    body = <span className={styles.popoverMuted}>looking up…</span>;
  } else if (def === null) {
    body = <span className={styles.popoverMuted}>(no definition)</span>;
  } else {
    body = def;
  }
  return (
    <div className={styles.popover} role="tooltip">
      <div className={styles.popoverHead}>{word.toUpperCase()}</div>
      <div className={styles.popoverBody}>{body}</div>
    </div>
  );
}
