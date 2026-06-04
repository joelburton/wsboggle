/**
 * Floating chat panel — used during a game and on the result view
 * where the board / word list occupy the page width.
 *
 * Ported from crossplay's :file:`ChatPanel`, adapted for wsboggle's
 * data shape (server-stamped `ChatMessage` rather than the in-memory
 * `ChatLine`; handles get their color via :func:`colorForHandle`
 * rather than a per-line `color` field).
 *
 * Behaviors carried over:
 *
 * - Position and size persist in localStorage; clamped against the
 *   viewport on mount and on window resize (via
 *   :func:`useDraggablePanel`).
 * - Identity (the chatting user's name + color) is fixed at session
 *   mount; no in-session rename.
 * - URLs in messages auto-linkify.
 * - Messages whose first character is `!` render bold (the
 *   "important — look!" mechanic). Auto-open on receive is handled
 *   by the parent (:class:`ClubPage`) since it owns the open state.
 * - Esc closes the panel from inside the input; the parent also
 *   binds Esc + `/` as a global shortcut.
 *
 * Used together with :class:`InlineChatPanel` in :file:`ClubPage`,
 * which picks one or the other based on whether the main club view has room
 * for an inline column.
 */

import { useEffect, useRef, useState, type MutableRefObject } from "react";
import { Rnd } from "react-rnd";
import type { ChatMessage } from "../shared";
import { colorForHandle } from "../colors";
import { useDraggablePanel, type Rect } from "../draggablePanel";
import { linkify } from "../linkify";
import styles from "./ChatPanel.module.css";

type Feedback = { id: number; text: string; level: "info" | "warn" | "error" };

type Props = {
  myHandle: string;
  messages: ChatMessage[];
  feedback: Feedback[];
  onSend: (text: string) => void;
  onClose: () => void;
  onDismissFeedback: (id: number) => void;
  disabled: boolean;
  /** Optional ref the parent can use to focus the input (e.g.
   *  from a `/` keystroke handler). */
  inputRef?: MutableRefObject<HTMLTextAreaElement | null>;
};

const PANEL_OPTS = {
  storageKey: "wsboggle.chatRect",
  minWidth: 240,
  minHeight: 200,
  viewportPad: 8,
  defaultRect(): Rect {
    const w = Math.min(340, window.innerWidth - 16);
    const h = Math.min(460, window.innerHeight - 60);
    return {
      x: Math.max(8, window.innerWidth - w - 8),
      y: 44,
      width: w,
      height: h,
    };
  },
};

export function ChatPanel({
  myHandle,
  messages,
  feedback,
  onSend,
  onClose,
  onDismissFeedback,
  disabled,
  inputRef,
}: Props) {
  const [draft, setDraft] = useState("");
  const { rect, onDragStop, onResizeStop } = useDraggablePanel(PANEL_OPTS);
  const localInputRef = useRef<HTMLTextAreaElement | null>(null);
  const tref = inputRef ?? localInputRef;
  const listRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    tref.current?.focus();
  }, [tref]);

  useEffect(() => {
    const el = listRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [messages.length]);

  function submit() {
    const text = draft.trim();
    if (!text) return;
    onSend(text);
    setDraft("");
  }

  function onInputKey(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Escape") {
      e.preventDefault();
      onClose();
      return;
    }
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      submit();
    }
  }

  const myColor = colorForHandle(myHandle);

  return (
    <Rnd
      className={styles.rnd}
      size={{ width: rect.width, height: rect.height }}
      position={{ x: rect.x, y: rect.y }}
      minWidth={PANEL_OPTS.minWidth}
      minHeight={PANEL_OPTS.minHeight}
      bounds="window"
      dragHandleClassName={styles.dragHandle}
      onDragStop={(_e, d) => onDragStop(d.x, d.y)}
      onResizeStop={(_e, _dir, refEl, _delta, position) =>
        onResizeStop(position.x, position.y, refEl.offsetWidth, refEl.offsetHeight)
      }
    >
      <aside className={styles.panel} aria-label="Chat">
        <header className={`${styles.header} ${styles.dragHandle}`}>
          <span className={styles.title}>
            Chat as{" "}
            <span style={{ color: myColor, fontWeight: 700 }}>{myHandle}</span>
          </span>
          <button
            type="button"
            className={styles.close}
            onClick={onClose}
            aria-label="Close chat"
          >
            ×
          </button>
        </header>
        {feedback.map((f) => (
          <div
            key={f.id}
            className={styles.feedback}
            onClick={() => onDismissFeedback(f.id)}
            title="dismiss"
          >
            {f.text}
          </div>
        ))}
        <div ref={listRef} className={styles.list}>
          {messages.length === 0 ? (
            <div className={styles.empty}>No messages yet.</div>
          ) : (
            messages.map((m) => {
              const important = m.text.startsWith("!");
              const body = important ? m.text.slice(1) : m.text;
              return (
                <div key={m.id} className={styles.line}>
                  <span
                    className={styles.name}
                    style={{ color: colorForHandle(m.handle) }}
                  >
                    {m.handle}
                  </span>
                  <span
                    className={`${styles.text} ${important ? styles.important : ""}`}
                  >
                    {linkify(body)}
                  </span>
                </div>
              );
            })
          )}
        </div>
        <textarea
          ref={tref}
          className={styles.input}
          rows={2}
          value={draft}
          placeholder={
            disabled ? "(disconnected)" : "Message (Enter to send, Shift+Enter for newline)"
          }
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={onInputKey}
          maxLength={2000}
          disabled={disabled}
        />
      </aside>
    </Rnd>
  );
}

// --- Closed-state reopen tab --------------------------------------------

type ReopenProps = {
  unreadCount: number;
  onOpen: () => void;
};

/** Bottom-right "💬 Chat" pill shown when the floating panel is
 *  closed. An unread badge appears when chat lines have arrived
 *  since the last open. Pressing "/" anywhere outside an input also
 *  opens the panel (see :class:`ClubPage`). */
export function ChatReopenTab({ unreadCount, onOpen }: ReopenProps) {
  return (
    <button type="button" className={styles.reopen} onClick={onOpen}>
      💬 Chat
      {unreadCount > 0 && (
        <span className={styles.reopenBadge}>{unreadCount}</span>
      )}
    </button>
  );
}
