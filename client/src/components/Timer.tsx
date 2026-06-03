import { useEffect, useRef, useState } from "react";

type Props = {
  /** ISO timestamp when the game's timer expires; null = untimed. */
  endsAt: string | null;
  /** Server's clock at the moment the snapshot was generated.
   *  We use it to correct against client-clock skew so the countdown
   *  doesn't lie when the laptop time is off. */
  serverNow: string;
  /** Fires exactly once when the countdown crosses zero. */
  onExpired?: () => void;
};

/** Server-now-aware countdown. Renders "M:SS"; "—" when untimed.
 *
 *  Skew model: we capture `clientNow - serverNow` at mount and use it
 *  to translate the server's `endsAt` into the local clock's frame.
 *  Subsequent ticks compare `Date.now()` (local) against the
 *  translated deadline, so a skewed local clock doesn't drift the
 *  display. */
export function Timer({ endsAt, serverNow, onExpired }: Props) {
  const skewRef = useRef<number>(Date.now() - new Date(serverNow).getTime());
  const firedRef = useRef(false);
  const [, force] = useState(0);

  useEffect(() => {
    if (endsAt === null) return;
    const id = setInterval(() => force((n) => n + 1), 250);
    return () => clearInterval(id);
  }, [endsAt]);

  if (endsAt === null) return <span>—</span>;

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
