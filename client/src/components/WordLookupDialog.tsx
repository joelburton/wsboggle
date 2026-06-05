/**
 * Global "?" dictionary lookup dialog.
 *
 * Press ``?`` anywhere (when not already typing into an input)
 * to open. Type a word, hit Enter, see the definition. Useful
 * for confirming the base of a word that's *not* on this board
 * (which the in-game word-list popovers won't tell you about).
 *
 * Esc closes. The dialog stays mounted across multiple lookups so
 * the user can flip through several words without re-opening it.
 */

import {
  useEffect,
  useRef,
  useState,
  type FormEvent,
  type KeyboardEvent,
} from "react";
import { api, ApiError } from "../api";
import styles from "./WordLookupDialog.module.css";

type Props = {
  onClose: () => void;
};

type LookupState =
  | { kind: "idle" }
  | { kind: "loading"; word: string }
  | { kind: "found"; word: string; definition: string }
  | { kind: "missing"; word: string }
  | { kind: "error"; message: string };

export function WordLookupDialog({ onClose }: Props) {
  const [input, setInput] = useState("");
  const [state, setState] = useState<LookupState>({ kind: "idle" });
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    inputRef.current?.focus();
  }, []);

  // Global Esc — only fires when the input doesn't already
  // consume it (the input's onKeyDown gets first dibs and clears
  // the field instead of closing the dialog).
  useEffect(() => {
    function onKey(e: globalThis.KeyboardEvent) {
      if (e.key === "Escape") {
        e.preventDefault();
        onClose();
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  async function submit(e: FormEvent) {
    e.preventDefault();
    const word = input.trim();
    if (!word) return;
    setState({ kind: "loading", word });
    try {
      const res = await api.define(word);
      if (res.definition === null) {
        setState({ kind: "missing", word });
      } else {
        setState({ kind: "found", word, definition: res.definition });
      }
    } catch (err) {
      setState({
        kind: "error",
        message: err instanceof ApiError ? err.detail : "Network error",
      });
    }
  }

  function onInputKey(e: KeyboardEvent<HTMLInputElement>) {
    if (e.key === "Escape" && input !== "") {
      // Esc inside the input clears the value rather than closing
      // the dialog. Press Esc again on an empty input to close.
      e.preventDefault();
      e.stopPropagation();
      setInput("");
      setState({ kind: "idle" });
    }
  }

  return (
    <div className={styles.backdrop} onClick={onClose}>
      <div
        className={styles.dialog}
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-label="Look up a word"
      >
        <h3>Look up a word</h3>
        <form onSubmit={submit}>
          <input
            ref={inputRef}
            type="text"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={onInputKey}
            placeholder="Type a word and press Enter"
            autoComplete="off"
            spellCheck={false}
            className={styles.input}
          />
        </form>
        <div className={styles.result}>
          {state.kind === "idle" && (
            <p className={styles.hint}>
              Looks up dictionary entries — handy for confirming a
              word's base form when the in-game popovers don't have
              it.
            </p>
          )}
          {state.kind === "loading" && (
            <p className={styles.hint}>looking up <em>{state.word}</em>…</p>
          )}
          {state.kind === "missing" && (
            <p className={styles.hint}>
              <strong>{state.word}</strong>: no definition found.
            </p>
          )}
          {state.kind === "found" && (
            <p>
              <strong className={styles.foundWord}>
                {state.word.toUpperCase()}
              </strong>
              : {state.definition}
            </p>
          )}
          {state.kind === "error" && (
            <p className={styles.error}>{state.message}</p>
          )}
        </div>
      </div>
    </div>
  );
}
