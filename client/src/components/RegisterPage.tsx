import { useState, type FormEvent } from "react";
import { api, ApiError } from "../api";
import { Link } from "../routing";
import type { MeResponse } from "../shared";
import styles from "./AuthForm.module.css";

type Props = {
  /** Called after a successful registration (which also signs the
   *  user in). The parent uses the resulting `MeResponse` to flip
   *  into the authenticated state. */
  onSuccess: (me: MeResponse) => void;
};

/** Register form. Same shape as Login plus an invite code field.
 *  The server gates registration on this code (admin-curated table). */
export function RegisterPage({ onSuccess }: Props) {
  const [handle, setHandle] = useState("");
  const [password, setPassword] = useState("");
  const [inviteCode, setInviteCode] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setPending(true);
    try {
      const me = await api.register({ handle, password, invite_code: inviteCode });
      onSuccess(me);
    } catch (err) {
      if (err instanceof ApiError) setError(err.detail);
      else setError("Network error");
      setPending(false);
    }
  }

  return (
    <div className={styles.wrapper}>
      <form className={styles.card} onSubmit={submit}>
        <h1>Create an account</h1>
        {error && <div className={styles.error}>{error}</div>}

        <label className={styles.field}>
          <span>Handle</span>
          <input
            autoFocus
            autoComplete="username"
            value={handle}
            onChange={(e) => setHandle(e.target.value)}
            required
          />
        </label>

        <label className={styles.field}>
          <span>Password</span>
          <input
            type="password"
            autoComplete="new-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
          />
        </label>

        <label className={styles.field}>
          <span>Invite code</span>
          <input
            value={inviteCode}
            onChange={(e) => setInviteCode(e.target.value)}
            required
          />
        </label>

        <div className={styles.actions}>
          <Link to="/login" className={styles.altLink}>
            Already have an account
          </Link>
          <button type="submit" disabled={pending}>
            {pending ? "Creating…" : "Create account"}
          </button>
        </div>
      </form>
    </div>
  );
}
