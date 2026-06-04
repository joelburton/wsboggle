import { useEffect, useState } from "react";
import { colorForHandle } from "../colors";
import type { LocalGuessRecord } from "../clubSocket";

type Props = {
  /** The list to render. In competitive games this is the viewer's
   *  own submissions; in collaborative games it's the shared team
   *  list with ``added_by_*`` per entry. Submission order is
   *  preserved; the component reverses for display so newest sits
   *  on top. */
  guesses: LocalGuessRecord[];
  /** The viewer's user id, so we can avoid highlighting the user's
   *  own contributions (you don't need a "look at this!" cue on
   *  your own word). */
  myUserId: number;
  /** Optional class on the wrapper, for layout integration. */
  className?: string;
  /** Optional class on each list item, for layout integration. */
  itemClassName?: string;
};

/** Highlight duration for collaborative arrivals (ms). After this
 *  the word fades to plain and is anonymous in the list. Matches
 *  the spec's "after 5 seconds, the color and underlining goes
 *  away." */
const HIGHLIGHT_MS = 5_000;

/** The viewer's word list during play. Submission order, most
 *  recent at top so the growing list pushes downward visually.
 *  Illegal guesses appear too, struck through. In collaborative
 *  mode teammates' arrivals carry a 5-second underline + color
 *  highlight in the adder's player color. */
export function WordList({ guesses, myUserId, className, itemClassName }: Props) {
  const legalCount = guesses.filter((g) => g.is_legal).length;

  // Trigger re-renders so highlights can fade out. Only ticks while
  // there's at least one entry inside the highlight window — the
  // common case (nothing recent) has no setInterval at all.
  const [, tick] = useState(0);
  const now = Date.now();
  const hasRecent = guesses.some(
    (g) =>
      g.addedAt !== undefined &&
      now - g.addedAt < HIGHLIGHT_MS &&
      g.added_by_user_id !== null &&
      g.added_by_user_id !== myUserId,
  );
  useEffect(() => {
    if (!hasRecent) return;
    const id = setInterval(() => tick((n) => n + 1), 250);
    return () => clearInterval(id);
  }, [hasRecent]);

  return (
    <div className={className}>
      <h3 style={{ marginTop: 0 }}>Words · {legalCount}</h3>
      <ul style={{ listStyle: "none", padding: 0, margin: 0 }}>
        {[...guesses].reverse().map((g, i) => {
          const isRecentFromOther =
            g.addedAt !== undefined &&
            now - g.addedAt < HIGHLIGHT_MS &&
            g.added_by_user_id !== null &&
            g.added_by_user_id !== myUserId &&
            g.added_by_handle !== null;
          const accentColor = isRecentFromOther
            ? colorForHandle(g.added_by_handle!)
            : null;
          return (
            <li
              key={i}
              className={itemClassName}
              style={{
                padding: "0.25rem 0",
                textDecoration: g.is_legal
                  ? isRecentFromOther
                    ? `underline ${accentColor}`
                    : "none"
                  : "line-through",
                color: !g.is_legal
                  ? "#9ca3af"
                  : accentColor ?? "#1f2937",
                display: "flex",
                justifyContent: "space-between",
              }}
            >
              <span style={{ textTransform: "uppercase" }}>{g.word}</span>
              <span style={{ color: "#6b7280" }}>
                {g.is_legal ? `+${g.points}` : ""}
              </span>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
