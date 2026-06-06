# wsboggle build targets.
#
# `make lib`   — compile data/libwords.so from c/libwords.c.
# `make dev`   — run backend + Vite dev server together; Ctrl-C kills both.
# `make clean` — remove built artifacts.
#
# The game-data files (data/words.dat, data/all.sqlite3, c/libwords.c)
# are vendored in the repo — `git clone` is enough to get them.

# Honour DATA_DIR if exported; default to ./data for source checkouts.
DATA_DIR ?= data

.PHONY: all lib dev clean

all: lib

lib: $(DATA_DIR)/libwords.so

# libwords.c is plain C (no Python C API) — ctypes loads it directly,
# so no Python include or linker glue is needed.
$(DATA_DIR)/libwords.so: c/libwords.c
	@mkdir -p $(DATA_DIR)
	$(CC) -O2 -shared -fPIC -o $@ $<

# One-shot dev launcher. The `trap "kill 0"` reaps both children when
# bash exits — Ctrl-C, either server crashing, etc. — so you never end
# up with an orphan uvicorn or Vite on a port. Output streams interleave.
dev: lib
	@bash -c 'trap "kill 0" EXIT INT TERM; \
	  WSBOGGLE_DEV=1 .venv/bin/python -m wsboggle & \
	  ( cd client && npm run dev ) & \
	  wait'

clean:
	rm -f $(DATA_DIR)/libwords.so
