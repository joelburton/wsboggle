import { useState, type FormEvent } from "react";
import { api, ApiError } from "../api";
import { Link, navigate } from "../routing";
import type { GameConfig } from "../shared";
import { GameConstraints, EMPTY_CONSTRAINTS, type Constraints } from "./GameConstraints";
import styles from "./AuthForm.module.css";

// All eight dice sets, in display order. Mirrors the registry in
// `src/wsboggle/dice.py`. v1 hardcodes these on the client — a
// `/api/dice-sets` endpoint would be a one-liner if this list ever
// starts changing.
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

/** Same timer-mode list the multiplayer New game dialog uses —
 *  countdown durations plus the two open-ended modes that end on
 *  an explicit click. Solo's "End game" button is the only end path
 *  for Count up / Untimed. */
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

/** Named scoring ladders mirroring ``wsboggle.scoring.LADDERS``. */
const SCORING_LADDERS: readonly { name: string; label: string }[] = [
  { name: "basic", label: "Basic: 1–11" },
  { name: "flat",  label: "Flat: 1" },
  { name: "fib",   label: "Fibonacci: 1–377" },
  { name: "big",   label: "Prefer big: 1–50" },
];

/** Build a complete GameConfig from the knobs we expose, filling
 *  in the same defaults the server would. Sending an explicit full
 *  shape (rather than a partial) keeps the wire payload obvious from
 *  the client side. */
function buildConfig(
  diceSet: string,
  mode: TimerMode,
  scoringLadder: string,
  constraints: Constraints,
): GameConfig {
  return {
    dice_set: diceSet,
    scoring_ladder: scoringLadder,
    min_legal_length: 3,
    mode: "competitive",
    dupes_cancel: true,
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

export function NewSoloGamePage() {
  const [diceSet, setDiceSet] = useState("4");
  const [modeId, setModeId] = useState("180");
  const [scoringLadder, setScoringLadder] = useState("basic");
  const [constraints, setConstraints] = useState<Constraints>(EMPTY_CONSTRAINTS);
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setPending(true);
    try {
      const mode = TIMER_MODES.find((m) => m.id === modeId) ?? TIMER_MODES[3];
      const snap = await api.startSolo(
        buildConfig(diceSet, mode, scoringLadder, constraints),
      );
      navigate(`/solo/${snap.game_id}`);
    } catch (err) {
      if (err instanceof ApiError) setError(err.detail);
      else setError("Network error");
      setPending(false);
    }
  }

  return (
    <div className={styles.wrapper}>
      <form className={styles.card} onSubmit={submit}>
        <h1>New solo game</h1>
        {error && <div className={styles.error}>{error}</div>}

        <label className={styles.field}>
          <span>Tileset</span>
          <select value={diceSet} onChange={(e) => setDiceSet(e.target.value)}>
            {DICE_SETS.map((d) => (
              <option key={d.name} value={d.name}>{d.label}</option>
            ))}
          </select>
        </label>

        <label className={styles.field}>
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

        <label className={styles.field}>
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

        <div className={styles.actions}>
          <Link to="/" className={styles.altLink}>← Home</Link>
          <button type="submit" disabled={pending}>
            {pending ? "Starting…" : "Start game"}
          </button>
        </div>
      </form>
    </div>
  );
}
