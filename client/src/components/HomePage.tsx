import { useState } from "react";
import { api } from "../api";
import { Link, navigate } from "../routing";
import type { ClubSummary, MeResponse } from "../shared";
import styles from "./HomePage.module.css";

type Props = {
  /** Current user + their clubs, loaded once at session start. */
  me: MeResponse;
  /** Fired after a successful POST /api/auth/logout so the parent
   *  can clear its authed state and re-render. */
  onLogout: () => void;
};

/** Logged-in home page. Solo button is the primary action; the clubs
 *  list links to each club page. Clubs with in-flight state (active
 *  game, pending proposal, pending review) show a small badge and
 *  cause the Solo CTA to confirm before continuing — protects against
 *  accidentally abandoning a club mid-game by clicking Solo. */
export function HomePage({ me, onLogout }: Props) {
  const [soloConfirm, setSoloConfirm] = useState(false);

  async function logout() {
    try {
      await api.logout();
    } finally {
      onLogout();
    }
  }

  const inFlightClubs = me.clubs.filter((c) => c.in_flight !== null);

  function onSoloClick() {
    if (inFlightClubs.length > 0) {
      setSoloConfirm(true);
      return;
    }
    navigate("/solo");
  }

  return (
    <div className={styles.wrapper}>
      <header className={styles.header}>
        <h1>
          <img src="/favicon.svg" alt="" />
          Mothtiles
        </h1>
        <div className={styles.identity}>
          <strong>@{me.user.handle}</strong>{" "}
          <button className="secondary" onClick={logout}>
            Sign out
          </button>
        </div>
      </header>

      <p>
        <button className={styles.soloCta} onClick={onSoloClick}>
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
                  {c.in_flight !== null && (
                    <span className={styles.inFlightBadge}>
                      {inFlightLabel(c.in_flight)}
                    </span>
                  )}
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

      {soloConfirm && (
        <SoloConfirmDialog
          inFlightClubs={inFlightClubs}
          onCancel={() => setSoloConfirm(false)}
          onContinue={() => {
            setSoloConfirm(false);
            navigate("/solo");
          }}
        />
      )}
    </div>
  );
}

function inFlightLabel(phase: NonNullable<ClubSummary["in_flight"]>): string {
  switch (phase) {
    case "playing":   return "game in progress";
    case "proposing": return "proposal pending";
    case "reviewing": return "reviewing";
  }
}

type SoloConfirmProps = {
  inFlightClubs: ClubSummary[];
  onCancel: () => void;
  onContinue: () => void;
};

/** "Play solo anyway?" gate when the user owes something to a club.
 *  Names the club(s) so the user can recognize what they'd be leaving
 *  behind. */
function SoloConfirmDialog({ inFlightClubs, onCancel, onContinue }: SoloConfirmProps) {
  const names = inFlightClubs.map((c) => `${c.name} (${inFlightLabel(c.in_flight!)})`);
  return (
    <div className={styles.confirmBackdrop} onClick={onCancel}>
      <div className={styles.confirmDialog} onClick={(e) => e.stopPropagation()}>
        <h3>Start a solo game?</h3>
        <p>
          You have something going on in{" "}
          {names.length === 1 ? "your club " : "your clubs: "}
          <strong>{names.join(", ")}</strong>. Solo play won't touch that,
          but the club won't know you've stepped away.
        </p>
        <div className={styles.confirmActions}>
          <button type="button" className="secondary" onClick={onCancel} autoFocus>
            Cancel
          </button>
          <button type="button" onClick={onContinue}>
            Play solo anyway
          </button>
        </div>
      </div>
    </div>
  );
}
