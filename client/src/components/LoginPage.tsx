import { useState, type FormEvent } from "react";
import { api, ApiError } from "../api";
import { Link } from "../routing";
import type { MeResponse } from "../shared";
import styles from "./AuthForm.module.css";

type Props = {
  /** Called after a successful login. The parent uses the resulting
   *  `MeResponse` to flip into the authenticated state. */
  onSuccess: (me: MeResponse) => void;
};

/** Login form. Submits handle + password; on success, hands the
 *  resulting `MeResponse` to the parent (which becomes the
 *  authed-state seed). On 401 we show the server's "invalid handle
 *  or password" message — same for both rejection reasons. */
export function LoginPage({ onSuccess }: Props) {
  const [handle, setHandle] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setPending(true);
    try {
      const me = await api.login({ handle, password });
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
        <h1>Sign in to Mothtiles</h1>
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
            autoComplete="current-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
          />
        </label>

        <div className={styles.actions}>
          <Link to="/register" className={styles.altLink}>
            Create an account
          </Link>
          <button type="submit" disabled={pending}>
            {pending ? "Signing in…" : "Sign in"}
          </button>
        </div>
      </form>
    </div>
  );
}
