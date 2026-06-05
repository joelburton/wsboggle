import { useRef, useState, type FormEvent, type KeyboardEvent } from "react";
import type { GuessResponse } from "../shared";

type Props = {
  /** Called when the user submits a word. The parent owns the
   *  network call and is responsible for the actual side effects
   *  (appending to its own word list, etc.); we just translate the
   *  outcome into a feedback message under the input. */
  onSubmit: (word: string) => Promise<GuessResponse>;
  /** When true, the input is greyed out and the placeholder switches
   *  to "Time's up". Used by the play page once the timer expires. */
  disabled?: boolean;
  /** Optional class on the form wrapper, for layout integration. */
  className?: string;
  /** Optional class on the feedback line, for layout integration. */
  feedbackClassName?: string;
};

/** Word entry field — type, Enter, optimistically clears, shows
 *  last-guess feedback below the input.
 *
 *  Arrow-key history (shell-style): up recalls the previous
 *  submission for editing; down moves forward; down past the end
 *  clears the input. All submissions are kept — including illegal /
 *  duplicate — because you often want to edit a typo'd attempt
 *  ("RANGE" after "RANG") or extend a stem ("RANGED" after "RANGE").
 *  Matches tboggle's `WordInput` behavior. */
export function WordEntry({ onSubmit, disabled, className, feedbackClassName }: Props) {
  const [word, setWord] = useState("");
  const [feedback, setFeedback] = useState<{ text: string; tone: "good" | "bad" | "warn" } | null>(null);
  const [history, setHistory] = useState<string[]>([]);
  // `historyAt` is the *cursor* into `history`. ``historyAt === history.length``
  // means "not browsing — input reflects whatever the user is typing fresh."
  const [historyAt, setHistoryAt] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);

  function moveCursorToEnd() {
    queueMicrotask(() => {
      const el = inputRef.current;
      if (el) {
        const n = el.value.length;
        el.setSelectionRange(n, n);
      }
    });
  }

  function onKeyDown(e: KeyboardEvent<HTMLInputElement>) {
    if (e.key === "ArrowUp") {
      if (historyAt > 0) {
        e.preventDefault();
        const next = historyAt - 1;
        setHistoryAt(next);
        setWord(history[next]);
        setFeedback(null);
        moveCursorToEnd();
      }
    } else if (e.key === "ArrowDown") {
      if (historyAt < history.length - 1) {
        e.preventDefault();
        const next = historyAt + 1;
        setHistoryAt(next);
        setWord(history[next]);
        setFeedback(null);
        moveCursorToEnd();
      } else if (historyAt < history.length) {
        e.preventDefault();
        setHistoryAt(history.length);
        setWord("");
        setFeedback(null);
      }
    }
  }

  async function submit(e: FormEvent) {
    e.preventDefault();
    const w = word.trim();
    if (!w) return;

    const nextHistory = [...history, w];
    setHistory(nextHistory);
    setHistoryAt(nextHistory.length);
    setWord("");

    try {
      const res = await onSubmit(w);
      switch (res.result) {
        case "accepted":
          setFeedback({ text: `${w}: +${res.points}`, tone: "good" });
          break;
        case "too_short":
          setFeedback({ text: `${w}: too short`, tone: "bad" });
          break;
        case "not_on_board":
          setFeedback({ text: `${w}: not on board`, tone: "bad" });
          break;
        case "not_in_word_list":
          setFeedback({ text: `${w}: not in our word list`, tone: "bad" });
          break;
        case "not_a_word":
          setFeedback({ text: `${w}: not a word`, tone: "bad" });
          break;
        case "already_submitted":
          setFeedback({ text: `${w}: already submitted`, tone: "warn" });
          break;
        case "game_inactive":
          setFeedback({ text: "game's over", tone: "warn" });
          break;
      }
    } catch {
      setFeedback({ text: "error submitting", tone: "bad" });
    }
  }

  const color =
    feedback?.tone === "good" ? "#15803d" :
    feedback?.tone === "warn" ? "#a16207" :
    feedback?.tone === "bad"  ? "#b91c1c" : "transparent";

  return (
    <form className={className} onSubmit={submit}>
      <input
        ref={inputRef}
        autoFocus
        autoComplete="off"
        spellCheck={false}
        value={word}
        onChange={(e) => {
          setWord(e.target.value);
          if (feedback) setFeedback(null);
        }}
        onKeyDown={onKeyDown}
        disabled={disabled}
        placeholder={disabled ? "Time's up" : "Type a word…"}
      />
      <div
        className={feedbackClassName}
        style={{ color, minHeight: "1.2em", fontSize: "0.9rem" }}
      >
        {feedback?.text ?? " "}
      </div>
    </form>
  );
}
