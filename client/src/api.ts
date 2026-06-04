/**
 * Typed fetch wrappers — one function per backend endpoint.
 *
 * All bodies and responses are JSON. Responses that aren't 2xx throw
 * an `ApiError` carrying the status and the server's response text;
 * callers branch on `err.status` (401 = not logged in, 400 = bad
 * input, etc.).
 *
 * Cookies for the session ride along automatically because the
 * dev-server proxy and the prod single-port setup are both same-origin
 * from the browser's POV; we never need to set `credentials: include`.
 */

import type {
  ClubGameSummary,
  ClubSummary,
  CreateClubRequest,
  DefineResponse,
  GameConfig,
  GameResult,
  GameSnapshot,
  GameView,
  GuessResponse,
  LoginRequest,
  MeResponse,
  RegisterRequest,
  SoloGameSummary,
} from "./shared";

export class ApiError extends Error {
  constructor(public status: number, public detail: string) {
    super(`${status}: ${detail}`);
  }
}

async function request<T>(url: string, opts: RequestInit = {}): Promise<T> {
  const res = await fetch(url, {
    ...opts,
    headers: { "content-type": "application/json", ...opts.headers },
  });
  if (!res.ok) {
    // Server may have returned a {detail: "..."} envelope; fall back
    // to plain text if the body isn't JSON.
    let detail = res.statusText;
    try {
      const body = await res.json();
      if (body && typeof body.detail === "string") detail = body.detail;
    } catch {
      // not JSON; keep statusText
    }
    throw new ApiError(res.status, detail);
  }
  // 204 / empty body
  if (res.status === 204) return undefined as unknown as T;
  return (await res.json()) as T;
}

const post = <T>(url: string, body?: unknown) =>
  request<T>(url, { method: "POST", body: body === undefined ? undefined : JSON.stringify(body) });

const get = <T>(url: string) => request<T>(url);

export const api = {
  // Auth
  me:       ()                    => get<MeResponse>("/api/me"),
  login:    (body: LoginRequest)  => post<MeResponse>("/api/auth/login", body),
  register: (body: RegisterRequest) => post<MeResponse>("/api/auth/register", body),
  logout:   ()                    => post<{ ok: boolean }>("/api/auth/logout"),

  // Clubs
  createClub:    (body: CreateClubRequest) => post<ClubSummary>("/api/clubs", body),
  getClub:       (clubId: number) => get<ClubSummary>(`/api/clubs/${clubId}`),
  listClubGames: (clubId: number) =>
    get<ClubGameSummary[]>(`/api/clubs/${clubId}/games`),

  // Solo
  startSolo: (config: GameConfig) => post<GameSnapshot>("/api/solo/games", { config }),
  guess:     (gameId: number, word: string) =>
    post<GuessResponse>(`/api/solo/games/${gameId}/guess`, { word }),
  endSolo:   (gameId: number) => post<GameResult>(`/api/solo/games/${gameId}/end`),
  listSolo:  ()               => get<SoloGameSummary[]>("/api/solo/games"),

  // Games (universal load)
  getGame: (gameId: number) => get<GameView>(`/api/games/${gameId}`),

  // Dictionary
  define: (word: string) =>
    get<DefineResponse>(`/api/define?word=${encodeURIComponent(word)}`),
};
