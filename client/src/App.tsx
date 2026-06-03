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

  // Authed routes.
  switch (route.kind) {
    case "home":
      return <HomePage me={auth.me} onLogout={onLogout} />;
    case "solo-new":
      return <NewSoloGamePage />;
    case "solo-play":
      return <SoloPlayPage gameId={route.gameId} me={auth.me} />;
    case "login":
    case "register":
      return null; // redirect effect handles this
    case "club":
    case "review":
    case "not-found":
    default:
      return (
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
  }
}
