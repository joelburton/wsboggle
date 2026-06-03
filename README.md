# wsboggle

A collaborative web-based Boggle game. Friends form a *club* and play
timed boards together over WebSocket. See `CLAUDE.md` for the full
design.

## Status

Pre-implementation. This README documents how the project is *intended*
to run; pieces are still being built. Track progress in `CLAUDE.md`.

## Quick start (development)

Requires Python 3.12+, a C compiler (`cc`), and Node 22+.

```sh
# One-time bootstrap (copies words.dat / all.sqlite3 / libwords.c
# from a sibling ../tboggle checkout).
make bootstrap

# Build the libwords shared library.
make lib

# Install Python deps in a venv.
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

# Install client deps.
( cd client && npm install )

# Run server and client in two terminals.
python -m wsboggle              # Uvicorn on :3001
( cd client && npm run dev )    # Vite on :5173, proxies /api + /ws → :3001
```

Open `http://localhost:5173`.

## Environment variables

Server:
- `HOST` — bind address (default `127.0.0.1`)
- `PORT` — bind port (default `3001`)
- `DB_PATH` — sqlite path (default `data/wsboggle.db`, created on first boot)
- `DATA_DIR` — directory for `words.dat` / `all.sqlite3` / `libwords.so`
  (default `data`)
- `CLIENT_DIST` — in production, path to the built client
- `WSBOGGLE_DEV=1` — enable `--reload` for uvicorn and skip the
  `Secure` flag on session cookies (so cookies work over plain HTTP
  in local dev)

Client (dev):
- `PORT` — Vite bind port (default `5173`)
- `API_PORT` — proxy target (default `3001`)

## Production

```sh
make lib
( cd client && npm install && npm run build )
pip install -e .
CLIENT_DIST=client/dist python -m wsboggle
```

The server serves the built client and proxies SPA routes to
`index.html`. Single port; intended to sit behind nginx.

## Administration

There is no admin UI. Invite codes are added by direct SQL:

```sql
INSERT INTO invite_codes (code, label, created_at)
VALUES ('friends-2026', 'wave 1', datetime('now'));
```

Forgotten password? Reset the `password_hash` column to a known scrypt
hash, or delete the user row and let them re-register.
