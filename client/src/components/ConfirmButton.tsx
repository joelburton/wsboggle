/**
 * Two-click confirmation button.
 *
 * First click "arms" the button — the label flips to ``confirmLabel``
 * (visually warned via ``data-armed``) and a timeout disarms after
 * ``timeoutMs``. A second click within that window fires ``onConfirm``.
 *
 * Used wherever the cost of an accidental click is high enough to want
 * a gate but a modal would be overkill (e.g. "End game"). Keep the
 * confirm label short — it sits inline at the same width as the idle
 * label and shouldn't reflow surrounding layout.
 */

import { useEffect, useRef, useState } from "react";

type Props = {
  /** Fires on the second click within ``timeoutMs``. */
  onConfirm: () => void;
  /** Initial label, before the button is armed. */
  idleLabel: string;
  /** Label shown after the first click; should hint at the consequence
   *  ("Click again to end", "Click again to delete", etc.). */
  confirmLabel: string;
  /** Window in ms before the button reverts to its idle state. */
  timeoutMs?: number;
  className?: string;
};

export function ConfirmButton({
  onConfirm,
  idleLabel,
  confirmLabel,
  timeoutMs = 3000,
  className,
}: Props) {
  const [armed, setArmed] = useState(false);
  const timerRef = useRef<number | null>(null);

  // Clear any pending disarm on unmount so we don't setState on a
  // gone component (e.g. user clicked End game, the result panel
  // mounted, this button unmounted before the timer fired).
  useEffect(() => {
    return () => {
      if (timerRef.current !== null) window.clearTimeout(timerRef.current);
    };
  }, []);

  function handleClick() {
    if (armed) {
      if (timerRef.current !== null) {
        window.clearTimeout(timerRef.current);
        timerRef.current = null;
      }
      setArmed(false);
      onConfirm();
      return;
    }
    setArmed(true);
    timerRef.current = window.setTimeout(() => {
      setArmed(false);
      timerRef.current = null;
    }, timeoutMs);
  }

  return (
    <button
      className={className}
      onClick={handleClick}
      aria-pressed={armed}
      data-armed={armed ? "true" : undefined}
    >
      {armed ? confirmLabel : idleLabel}
    </button>
  );
}
