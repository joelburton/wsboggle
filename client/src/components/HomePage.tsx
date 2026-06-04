import { api } from "../api";
import { Link, navigate } from "../routing";
import type { MeResponse } from "../shared";
import styles from "./HomePage.module.css";

type Props = {
  /** Current user + their clubs, loaded once at session start. */
  me: MeResponse;
  /** Fired after a successful POST /api/auth/logout so the parent
   *  can clear its authed state and re-render. */
  onLogout: () => void;
};

/** Logged-in home page. Solo button is the primary action for the
 *  v1 milestone; the clubs section renders the user's clubs (if
 *  any) but the club page itself isn't built yet. */
export function HomePage({ me, onLogout }: Props) {
  async function logout() {
    try {
      await api.logout();
    } finally {
      onLogout();
    }
  }

  return (
    <div className={styles.wrapper}>
      <header className={styles.header}>
        <h1>wsboggle</h1>
        <div className={styles.identity}>
          <strong>@{me.user.handle}</strong>{" "}
          <button className="secondary" onClick={logout}>
            Sign out
          </button>
        </div>
      </header>

      <p>
        <button
          className={styles.soloCta}
          onClick={() => navigate("/solo")}
        >
          ▶ Play Solo
        </button>
      </p>

      <section className={styles.clubs}>
        <div className={styles.clubsHeader}>
          <h2>Your clubs</h2>
          <button className="secondary" onClick={() => navigate("/clubs/new")}>
            + New club
          </button>
        </div>
        {me.clubs.length === 0 ? (
          <p className={styles.empty}>
            No clubs yet — make one above to play with friends.
          </p>
        ) : (
          <ul className={styles.clubList}>
            {me.clubs.map((c) => (
              <li key={c.id}>
                <Link to={`/c/${c.id}`} className={styles.clubLink}>
                  <strong>{c.name}</strong>
                  <span className={styles.clubMeta}>
                    {c.member_handles.join(", ")} · {c.game_count} game
                    {c.game_count === 1 ? "" : "s"}
                  </span>
                </Link>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}
