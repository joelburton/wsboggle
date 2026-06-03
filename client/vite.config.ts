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
  server: {
    port: Number(process.env.PORT ?? 5173),
    proxy: {
      "/api": { target: `http://127.0.0.1:${API_PORT}` },
      "/ws":  { target: `ws://127.0.0.1:${API_PORT}`, ws: true },
    },
  },
});
