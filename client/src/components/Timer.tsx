import { useEffect, useRef, useState } from "react";

type Props = {
  /** ISO timestamp of game start; used as the count-up baseline. */
  startedAt: string;
  /** ISO timestamp of the game's deadline. null = no auto-end. */
  endsAt: string | null;
  /** Server's clock at the moment the snapshot was generated.
   *  We use it to correct against client-clock skew so the display
   *  doesn't lie when the laptop time is off. */
  serverNow: string;
  /** "down" counts toward endsAt (or shows untimed when endsAt is
   *  null); "up" counts elapsed time from startedAt with no
   *  upper bound. CLAUDE.md treats this as a display knob — the
   *  server's auto-end decision lives entirely with `timer_seconds`. */
  direction: "down" | "up";
  /** Fires exactly once when the countdown crosses zero. Only
   *  relevant in "down" + finite-endsAt mode; the solo path uses it
   *  to auto-call /end, multiplayer ignores it (the server fires
   *  gameEnded). */
  onExpired?: () => void;
};

/** Server-now-aware timer. Three display modes:
 *
 *  - "down" + endsAt → countdown ("M:SS") to the deadline.
 *  - "down" + endsAt null → "—" (untimed; manual end is the only
 *    way out).
 *  - "up" → elapsed time since startedAt, no cap.
 *
 *  Skew model: we capture `clientNow - serverNow` at mount and
 *  translate the server's timestamps into the local clock's frame.
 *  Subsequent ticks compare `Date.now()` against the translated
 *  reference, so a skewed local clock doesn't drift the display.
 */
export function Timer({
  startedAt,
  endsAt,
  serverNow,
  direction,
  onExpired,
}: Props) {
  const skewRef = useRef<number>(Date.now() - new Date(serverNow).getTime());
  const firedRef = useRef(false);
  const [, force] = useState(0);

  // We only need to tick when there's a moving display: count-up
  // always moves; countdown moves while endsAt is set; untimed
  // doesn't move at all.
  const ticking = direction === "up" || (direction === "down" && endsAt !== null);
  useEffect(() => {
    if (!ticking) return;
    const id = setInterval(() => force((n) => n + 1), 250);
    return () => clearInterval(id);
  }, [ticking]);

  if (direction === "up") {
    const baseLocal = new Date(startedAt).getTime() + skewRef.current;
    const elapsedMs = Math.max(0, Date.now() - baseLocal);
    const total = Math.floor(elapsedMs / 1000);
    const m = Math.floor(total / 60);
    const s = total % 60;
    return <span>{m}:{String(s).padStart(2, "0")}</span>;
  }

  // direction === "down"
  if (endsAt === null) {
    return <span title="Untimed — end the game when you're done">—</span>;
  }

  const deadlineLocal = new Date(endsAt).getTime() + skewRef.current;
  const remainingMs = Math.max(0, deadlineLocal - Date.now());

  if (remainingMs === 0 && !firedRef.current) {
    firedRef.current = true;
    // Fire after this render so parents can update state safely.
    queueMicrotask(() => onExpired?.());
  }

  const totalSeconds = Math.ceil(remainingMs / 1000);
  const m = Math.floor(totalSeconds / 60);
  const s = totalSeconds % 60;
  return <span>{m}:{String(s).padStart(2, "0")}</span>;
}
