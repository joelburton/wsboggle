import type { GuessRecord } from "../shared";

type Props = {
  /** The viewer's submission history. Submission order is preserved;
   *  this component reverses for display so newest appears first. */
  guesses: GuessRecord[];
  /** Optional class on the wrapper, for layout integration. */
  className?: string;
  /** Optional class on each list item, for layout integration. */
  itemClassName?: string;
};

/** The viewer's own word list during play. Submission order, most
 *  recent at top so the growing list pushes downward visually.
 *  Illegal guesses appear too, struck through. */
export function WordList({ guesses, className, itemClassName }: Props) {
  const legalCount = guesses.filter((g) => g.is_legal).length;
  return (
    <div className={className}>
      <h3 style={{ marginTop: 0 }}>Your words · {legalCount}</h3>
      <ul style={{ listStyle: "none", padding: 0, margin: 0 }}>
        {[...guesses].reverse().map((g, i) => (
          <li
            key={i}
            className={itemClassName}
            style={{
              padding: "0.25rem 0",
              textDecoration: g.is_legal ? "none" : "line-through",
              color: g.is_legal ? "#1f2937" : "#9ca3af",
              display: "flex",
              justifyContent: "space-between",
            }}
          >
            <span style={{ textTransform: "uppercase" }}>{g.word}</span>
            <span style={{ color: "#6b7280" }}>
              {g.is_legal ? `+${g.points}` : ""}
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}
