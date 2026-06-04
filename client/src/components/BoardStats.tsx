/**
 * "You vs. board" stats panel — ported from tboggle's StatusArea.
 *
 * Three rows: Words / Long / Score, each with the board's total
 * findable max and the player's current take. Same layout shape
 * as tboggle so muscle memory carries over.
 *
 * "You" values are derived from the player's own guess list
 * (legal only). The max values come from the server-supplied
 * `BoardStats` — constant for the life of the game, so the panel
 * is read-only after mount aside from the per-guess updates.
 */

import type { BoardStats as BoardStatsType, GuessRecord } from "../shared";
import styles from "./BoardStats.module.css";

type Props = {
  stats: BoardStatsType;
  guesses: GuessRecord[];
  className?: string;
};

export function BoardStats({ stats, guesses, className }: Props) {
  // Derive the player's running totals from their legal guesses.
  // Illegal entries don't count toward any column — they're already
  // greyed out in the word list.
  let words = 0;
  let points = 0;
  let longest = 0;
  for (const g of guesses) {
    if (!g.is_legal) continue;
    words += 1;
    points += g.points;
    if (g.word.length > longest) longest = g.word.length;
  }

  return (
    <div className={`${styles.panel} ${className ?? ""}`}>
      <div className={styles.head}>
        <span />
        <span className={styles.colHead}>You</span>
        <span className={styles.colHead}>Board</span>
      </div>
      <StatRow label="Words" you={words} max={stats.total_words} />
      <StatRow label="Score" you={points} max={stats.total_points} />
      <StatRow label="Long"  you={longest} max={stats.longest_word} />
    </div>
  );
}

function StatRow({ label, you, max }: { label: string; you: number; max: number }) {
  return (
    <div className={styles.row}>
      <span className={styles.label}>{label}</span>
      <span className={styles.you}>{you}</span>
      <span className={styles.max}>{max}</span>
    </div>
  );
}
