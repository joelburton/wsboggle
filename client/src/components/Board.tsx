import styles from "./Board.module.css";

type Props = {
  /** Display grid: each cell is already the player-facing string
   *  (e.g. "Qu", "Th", "A"). The server expands special faces. */
  board: string[][];
  /** Smaller cell size — used in the post-game results panel as
   *  visual context for verifying missed-word paths. */
  compact?: boolean;
};

/** Read-only board renderer. Sized by the grid's row count — works
 *  for 4×4, 5×5, 6×6 alike. Click handling for trace input is
 *  out-of-scope for v1 (keyboard-only). */
export function Board({ board, compact = false }: Props) {
  const cols = board[0]?.length ?? 0;
  const cellClass = compact ? styles.cellCompact : styles.cell;
  return (
    <div
      className={styles.board}
      style={{ gridTemplateColumns: `repeat(${cols}, auto)` }}
    >
      {board.flatMap((row, y) =>
        row.map((cell, x) => (
          <div key={`${y}-${x}`} className={cellClass}>
            {cell}
          </div>
        )),
      )}
    </div>
  );
}
