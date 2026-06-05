/**
 * Board + a small rotate button, with the rotation state held
 * locally on each viewer.
 *
 * Rotation is a per-viewer visual preference — the data layout
 * stays the same (paths are isomorphic under 90° rotation, so the
 * game logic doesn't care), and each player can rotate their own
 * view independently. The implementation rotates the underlying
 * `string[][]` rather than CSS-transforming the element, so the
 * letters stay upright and clickable.
 */

import { useState } from "react";
import { Board } from "./Board";
import styles from "./RotatableBoard.module.css";

type Props = {
  board: string[][];
};

/** Rotate a board 90° clockwise. For square boards (the only
 *  shape we ship) the result has the same dimensions. */
function rotate90Cw(board: string[][]): string[][] {
  const rows = board.length;
  const cols = board[0]?.length ?? 0;
  const out: string[][] = [];
  for (let y = 0; y < cols; y++) {
    const newRow: string[] = [];
    for (let x = 0; x < rows; x++) {
      newRow.push(board[rows - 1 - x][y]);
    }
    out.push(newRow);
  }
  return out;
}

function rotated(board: string[][], turns: number): string[][] {
  let b = board;
  for (let i = 0; i < (turns % 4 + 4) % 4; i++) {
    b = rotate90Cw(b);
  }
  return b;
}

export function RotatableBoard({ board }: Props) {
  const [turns, setTurns] = useState(0);
  return (
    <div className={styles.wrap}>
      <Board board={rotated(board, turns)} />
      <button
        type="button"
        className={styles.rotateBtn}
        onClick={() => setTurns((t) => t + 1)}
        title="Rotate board 90°"
        aria-label="Rotate board 90 degrees"
      >
        ↻
      </button>
    </div>
  );
}
