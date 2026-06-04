/**
 * Create-club form.
 *
 * Membership is fixed at creation (no add/remove UX in v1) so the
 * form collects all members up front:
 *
 * - Name: free text. For 2-person clubs we suggest a default like
 *   ``"alice + bob"`` once the other handle is filled in; the user
 *   can edit. For 3+ members we don't auto-name.
 * - Members: one handle per row, dynamic add/remove. The current
 *   user is implicit — they're added server-side and shouldn't be
 *   listed here.
 *
 * The server validates handles + existence and we surface its
 * 400-detail in the same error slot we use elsewhere.
 */

import { useState, type FormEvent } from "react";
import { api, ApiError } from "../api";
import { Link, navigate } from "../routing";
import type { MeResponse } from "../shared";
import styles from "./AuthForm.module.css";

type Props = {
  /** Current user — used to suggest the default 2-person club name. */
  me: MeResponse;
};

export function NewClubPage({ me }: Props) {
  const [name, setName] = useState("");
  // Start with one input row; the user adds more for 3+ person clubs.
  const [handles, setHandles] = useState<string[]>([""]);
  const [nameTouched, setNameTouched] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  /** Suggested default name when there's exactly one other handle.
   *  Stops suggesting as soon as the user types in the name field
   *  (so we don't stomp their edit). */
  function suggestedName(others: string[]): string {
    const filled = others.map((h) => h.trim()).filter(Boolean);
    if (filled.length !== 1) return "";
    // alphabetic by lowercased handle, matching the server's display sort.
    const pair = [me.user.handle, filled[0]].sort((a, b) =>
      a.toLowerCase().localeCompare(b.toLowerCase()),
    );
    return `${pair[0]} + ${pair[1]}`;
  }

  function updateHandle(idx: number, value: string) {
    const next = handles.slice();
    next[idx] = value;
    setHandles(next);
    if (!nameTouched) setName(suggestedName(next));
  }

  function addRow() {
    setHandles([...handles, ""]);
    if (!nameTouched) setName("");  // 3+ members → no auto-name
  }

  function removeRow(idx: number) {
    if (handles.length === 1) return;
    const next = handles.filter((_, i) => i !== idx);
    setHandles(next);
    if (!nameTouched) setName(suggestedName(next));
  }

  async function submit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    const cleanedHandles = handles.map((h) => h.trim()).filter(Boolean);
    if (cleanedHandles.length === 0) {
      setError("add at least one other member");
      return;
    }
    setPending(true);
    try {
      const club = await api.createClub({
        name: name.trim(),
        member_handles: cleanedHandles,
      });
      navigate(`/c/${club.id}`);
    } catch (err) {
      if (err instanceof ApiError) setError(err.detail);
      else setError("Network error");
      setPending(false);
    }
  }

  return (
    <div className={styles.wrapper}>
      <form className={styles.card} onSubmit={submit}>
        <h1>New club</h1>
        {error && <div className={styles.error}>{error}</div>}

        <div className={styles.field}>
          <label>Members</label>
          {handles.map((h, idx) => (
            <div key={idx} style={{ display: "flex", gap: "0.5rem", marginBottom: "0.25rem" }}>
              <input
                type="text"
                value={h}
                placeholder="handle"
                autoCapitalize="none"
                autoCorrect="off"
                onChange={(e) => updateHandle(idx, e.target.value)}
                style={{ flex: 1 }}
              />
              <button
                type="button"
                className="secondary"
                onClick={() => removeRow(idx)}
                disabled={handles.length === 1}
                title="Remove this member"
              >
                ×
              </button>
            </div>
          ))}
          <button type="button" className="secondary" onClick={addRow}>
            + Add member
          </button>
        </div>

        <label className={styles.field}>
          <span>Name</span>
          <input
            type="text"
            value={name}
            placeholder="Club name"
            onChange={(e) => {
              setName(e.target.value);
              setNameTouched(true);
            }}
          />
        </label>

        <div className={styles.actions}>
          <Link to="/" className={styles.altLink}>← Home</Link>
          <button type="submit" disabled={pending}>
            {pending ? "Creating…" : "Create club"}
          </button>
        </div>
      </form>
    </div>
  );
}
