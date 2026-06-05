/**
 * Top-level routing dispatch and auth gate.
 *
 * - On mount, fetch `/api/me`. 200 → we have a session; 401 → anon.
 * - Anon users get the login/register pages; everything else
 *   redirects to login.
 * - Authed users see the routed page. Visiting /login or /register
 *   while authed bounces home.
 *
 * `me` lives at the top so any page that needs it (or wants to
 * invalidate after login/logout) can receive it as a prop without a
 * context, given the very flat component tree.
 */

import { useEffect, useState } from "react";
import { api, ApiError } from "./api";
import { navigate, useRoute } from "./routing";
import type { MeResponse } from "./shared";
import { LoginPage } from "./components/LoginPage";
import { RegisterPage } from "./components/RegisterPage";
import { HomePage } from "./components/HomePage";
import { NewSoloGamePage } from "./components/NewSoloGamePage";
import { SoloPlayPage } from "./components/SoloPlayPage";
import { NewClubPage } from "./components/NewClubPage";
import { ClubPage } from "./components/ClubPage";
import { WordLookupDialog } from "./components/WordLookupDialog";

type AuthState =
  | { kind: "loading" }
  | { kind: "anon" }
  | { kind: "authed"; me: MeResponse };

export function App() {
  const route = useRoute();
  const [auth, setAuth] = useState<AuthState>({ kind: "loading" });

  useEffect(() => {
    api.me()
      .then((me) => setAuth({ kind: "authed", me }))
      .catch((e) => {
        if (e instanceof ApiError && e.status === 401) {
          setAuth({ kind: "anon" });
        } else {
          // Network failure, etc. — treat as anon; user can retry by
          // logging in.
          setAuth({ kind: "anon" });
        }
      });
  }, []);

  // Redirect authed users away from auth pages.
  useEffect(() => {
    if (auth.kind === "authed" && (route.kind === "login" || route.kind === "register")) {
      navigate("/");
    }
  }, [auth.kind, route.kind]);

  if (auth.kind === "loading") {
    return <main style={{ padding: "2rem" }}>Loading…</main>;
  }

  const onAuthSuccess = (me: MeResponse) => setAuth({ kind: "authed", me });
  const onLogout = () => setAuth({ kind: "anon" });

  if (auth.kind === "anon") {
    if (route.kind === "register") return <RegisterPage onSuccess={onAuthSuccess} />;
    return <LoginPage onSuccess={onAuthSuccess} />;
  }

  return <AuthedApp me={auth.me} route={route} onLogout={onLogout} />;
}

type AuthedProps = {
  me: MeResponse;
  route: ReturnType<typeof useRoute>;
  onLogout: () => void;
};

/** Authed-only shell: the route's page plus app-wide overlays
 *  (currently just the "?" word-lookup dialog). Lives in its own
 *  component so the global "?" listener mounts only after auth and
 *  doesn't compete with the login form. */
function AuthedApp({ me, route, onLogout }: AuthedProps) {
  const [lookupOpen, setLookupOpen] = useState(false);

  useEffect(() => {
    function onKey(e: globalThis.KeyboardEvent) {
      if (lookupOpen) return;
      if (e.metaKey || e.ctrlKey || e.altKey) return;
      const target = e.target as HTMLElement | null;
      const tag = target?.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA") return;
      // "?" arrives as Shift + "/"; checking e.key gives us "?"
      // directly on US layouts; non-US users with a different
      // location for ? would need a remap, but we don't have any
      // yet.
      if (e.key === "?") {
        e.preventDefault();
        setLookupOpen(true);
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [lookupOpen]);

  let page: React.ReactNode;
  switch (route.kind) {
    case "home":
      page = <HomePage me={me} onLogout={onLogout} />;
      break;
    case "solo-new":
      page = <NewSoloGamePage />;
      break;
    case "solo-play":
      page = <SoloPlayPage gameId={route.gameId} me={me} />;
      break;
    case "club-new":
      page = <NewClubPage me={me} />;
      break;
    case "club":
      page = <ClubPage clubId={route.clubId} me={me} />;
      break;
    case "login":
    case "register":
      page = null; // redirect effect handles this
      break;
    case "review":
    case "not-found":
    default:
      page = (
        <main style={{ padding: "2rem" }}>
          <h1>Not yet</h1>
          <p>This view isn't built in the v1 milestone.</p>
          <p>
            <a href="/" onClick={(e) => { e.preventDefault(); navigate("/"); }}>
              ← Home
            </a>
          </p>
        </main>
      );
      break;
  }

  return (
    <>
      {page}
      {lookupOpen && <WordLookupDialog onClose={() => setLookupOpen(false)} />}
    </>
  );
}
