/**
 * "Good board" constraint inputs — min/max for words, score, and
 * longest word. Used by both the multiplayer New game dialog and
 * the solo new-game page.
 *
 * Mirrors tboggle's collapsible "Min/max options for board
 * finding" panel (chooser.py). Each constraint is a `min` + `max`
 * pair; empty input means "no constraint" and ships as null on
 * the wire. The server passes these through to libwords'
 * rejection sampler.
 *
 * Controlled component — the parent owns the state and decides
 * how (and whether) to fold it into the submitted `GameConfig`.
 */

import { useState, type ChangeEvent } from "react";
import styles from "./GameConstraints.module.css";

/** Six raw input values; null = "no constraint" for that bound. */
export type Constraints = {
  min_words: number | null;
  max_words: number | null;
  min_score: number | null;
  max_score: number | null;
  min_longest: number | null;
  max_longest: number | null;
};

export const EMPTY_CONSTRAINTS: Constraints = {
  min_words: null,
  max_words: null,
  min_score: null,
  max_score: null,
  min_longest: null,
  max_longest: null,
};

/** True iff any bound is set — used to decide whether to start the
 *  panel expanded (so the user sees the values they're carrying). */
export function constraintsActive(c: Constraints): boolean {
  return (
    c.min_words !== null ||
    c.max_words !== null ||
    c.min_score !== null ||
    c.max_score !== null ||
    c.min_longest !== null ||
    c.max_longest !== null
  );
}

type Props = {
  value: Constraints;
  onChange: (next: Constraints) => void;
};

const ROWS: readonly { key: "words" | "score" | "longest"; label: string }[] = [
  { key: "words",   label: "Words" },
  { key: "score",   label: "Score" },
  { key: "longest", label: "Longest" },
];

export function GameConstraints({ value, onChange }: Props) {
  // Default-open if anything is already set; the user obviously
  // wants to see what they've got. Otherwise collapsed to keep the
  // dialog compact.
  const [open, setOpen] = useState(constraintsActive(value));

  function update(key: keyof Constraints, raw: string) {
    const next: Constraints = { ...value };
    const trimmed = raw.trim();
    next[key] = trimmed === "" ? null : Number(trimmed);
    onChange(next);
  }

  return (
    <div className={styles.wrapper}>
      <button
        type="button"
        className={styles.header}
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
      >
        <span className={styles.disclosure}>{open ? "▾" : "▸"}</span>
        Board constraints
        {!open && constraintsActive(value) && (
          <span className={styles.active}>active</span>
        )}
      </button>
      {open && (
        <div className={styles.grid}>
          <span /> {/* corner */}
          <span className={styles.colHead}>min</span>
          <span className={styles.colHead}>max</span>
          {ROWS.map((row) => (
            <ConstraintRow
              key={row.key}
              label={row.label}
              minKey={`min_${row.key}` as keyof Constraints}
              maxKey={`max_${row.key}` as keyof Constraints}
              value={value}
              onUpdate={update}
            />
          ))}
        </div>
      )}
    </div>
  );
}

type RowProps = {
  label: string;
  minKey: keyof Constraints;
  maxKey: keyof Constraints;
  value: Constraints;
  onUpdate: (key: keyof Constraints, raw: string) => void;
};

function ConstraintRow({ label, minKey, maxKey, value, onUpdate }: RowProps) {
  function display(v: number | null): string {
    return v === null ? "" : String(v);
  }
  function handle(key: keyof Constraints) {
    return (e: ChangeEvent<HTMLInputElement>) => onUpdate(key, e.target.value);
  }
  return (
    <>
      <span className={styles.label}>{label}</span>
      <input
        type="number"
        min={0}
        inputMode="numeric"
        className={styles.numInput}
        value={display(value[minKey])}
        onChange={handle(minKey)}
        placeholder="—"
      />
      <input
        type="number"
        min={0}
        inputMode="numeric"
        className={styles.numInput}
        value={display(value[maxKey])}
        onChange={handle(maxKey)}
        placeholder="—"
      />
    </>
  );
}
