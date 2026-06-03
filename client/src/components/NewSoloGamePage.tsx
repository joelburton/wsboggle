import { useState, type FormEvent } from "react";
import { api, ApiError } from "../api";
import { Link, navigate } from "../routing";
import type { GameConfig } from "../shared";
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

/** Build a complete GameConfig from the two knobs we expose, filling
 *  in the same defaults the server would. Sending an explicit full
 *  shape (rather than a partial) keeps the wire payload obvious from
 *  the client side. */
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

export function NewSoloGamePage() {
  const [diceSet, setDiceSet] = useState("4");
  const [timer, setTimer] = useState(180);
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setPending(true);
    try {
      const snap = await api.startSolo(buildConfig(diceSet, timer));
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
            value={timer}
            onChange={(e) => setTimer(Number(e.target.value))}
          >
            {TIMERS.map((t) => (
              <option key={t.seconds} value={t.seconds}>{t.label}</option>
            ))}
          </select>
        </label>

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
