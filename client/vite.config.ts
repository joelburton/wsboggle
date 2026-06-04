/**
 * Vite config — dev server that proxies /api and /ws through to the
 * FastAPI backend, so the client can pretend everything is same-origin.
 *
 * Honours PORT (Vite bind) and API_PORT (proxy target) from the env.
 */
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

const API_PORT = process.env.API_PORT ?? "3001";

export default defineConfig({
  plugins: [react()],
  // `react-draggable@4.6+` (transitive via `react-rnd`) references
  // `process.env.DRAGGABLE_DEBUG` directly with no guard, which
  // explodes in the browser as `ReferenceError: process is not
  // defined`. Vite does literal-token replacement, so substituting
  // the whole `process.env` access with `{}` turns the dead-debug
  // check into `({}).DRAGGABLE_DEBUG === undefined`. (4.5.0 had
  // the guard; 4.6.0 dropped it. We can't pin without overrides,
  // which is heavier than the one-line shim.)
  define: {
    "process.env": "{}",
  },
  server: {
    port: Number(process.env.PORT ?? 5173),
    // Localhost is always allowed; this lets Cloudflare quick-tunnels
    // (`https://*.trycloudflare.com`) reach the dev server too. Same
    // entry crossplay uses.
    allowedHosts: [".trycloudflare.com"],
    proxy: {
      "/api": { target: `http://127.0.0.1:${API_PORT}` },
      "/ws":  { target: `ws://127.0.0.1:${API_PORT}`, ws: true },
    },
  },
});
