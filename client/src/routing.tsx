/**
 * Hand-rolled routing. No react-router.
 *
 * Routes (matching the backend's URL space):
 *   /              → home
 *   /login         → login
 *   /register      → register
 *   /solo          → new solo game (config dialog)
 *   /solo/:id      → solo play (in-game + results)
 *   /clubs/new     → create-club form
 *   /c/:id         → club page (main view / in-game / result)
 *   /games/:id     → game review (not built in v1 milestone)
 *
 * `useRoute` subscribes to popstate so back/forward and our own
 * `navigate()` calls all flow through one source of truth. `<Link>`
 * preserves middle-click / cmd-click for opening in a new tab.
 */

import { useEffect, useState, type ReactNode, type MouseEvent } from "react";

export type Route =
  | { kind: "home" }
  | { kind: "login" }
  | { kind: "register" }
  | { kind: "solo-new" }
  | { kind: "solo-play"; gameId: number }
  | { kind: "club-new" }
  | { kind: "club"; clubId: number }
  | { kind: "review"; gameId: number }
  | { kind: "not-found" };

export function parsePath(path: string): Route {
  if (path === "/" || path === "")  return { kind: "home" };
  if (path === "/login")            return { kind: "login" };
  if (path === "/register")         return { kind: "register" };
  if (path === "/solo")             return { kind: "solo-new" };
  if (path === "/clubs/new")        return { kind: "club-new" };

  let m: RegExpMatchArray | null;
  if ((m = path.match(/^\/solo\/(\d+)$/)))   return { kind: "solo-play", gameId: Number(m[1]) };
  if ((m = path.match(/^\/c\/(\d+)$/)))      return { kind: "club", clubId: Number(m[1]) };
  if ((m = path.match(/^\/games\/(\d+)$/)))  return { kind: "review", gameId: Number(m[1]) };

  return { kind: "not-found" };
}

export function useRoute(): Route {
  const [path, setPath] = useState<string>(window.location.pathname);
  useEffect(() => {
    const onPop = () => setPath(window.location.pathname);
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, []);
  return parsePath(path);
}

/** Push a new URL and tell the route hook to re-read.
 *  Uses a synthetic popstate so subscribers fire — the browser
 *  doesn't fire popstate for pushState. */
export function navigate(path: string): void {
  if (window.location.pathname === path) return;
  window.history.pushState(null, "", path);
  window.dispatchEvent(new PopStateEvent("popstate"));
}

type LinkProps = {
  to: string;
  children: ReactNode;
  className?: string;
  onClick?: (e: MouseEvent<HTMLAnchorElement>) => void;
};

/** Internal anchor that uses pushState on plain click but lets the
 *  browser handle modifier-clicks (open in new tab, etc.).
 *
 *  ``onClick`` fires *before* navigation; if the handler calls
 *  ``e.preventDefault()`` the navigation is skipped. Used by gates
 *  that want to show a confirm modal before leaving the page. */
export function Link({ to, children, className, onClick }: LinkProps) {
  return (
    <a
      href={to}
      className={className}
      onClick={(e) => {
        if (e.ctrlKey || e.metaKey || e.shiftKey || e.button !== 0) return;
        onClick?.(e);
        if (e.defaultPrevented) return;
        e.preventDefault();
        navigate(to);
      }}
    >
      {children}
    </a>
  );
}
