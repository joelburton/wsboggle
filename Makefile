# wsboggle build targets.
#
# `make lib`   — compile data/libwords.so from c/libwords.c.
# `make clean` — remove built artifacts.
#
# The game-data files (data/words.dat, data/all.sqlite3, c/libwords.c)
# are vendored in the repo — `git clone` is enough to get them.

# Honour DATA_DIR if exported; default to ./data for source checkouts.
DATA_DIR ?= data

.PHONY: all lib clean

all: lib

lib: $(DATA_DIR)/libwords.so

# libwords.c is plain C (no Python C API) — ctypes loads it directly,
# so no Python include or linker glue is needed.
$(DATA_DIR)/libwords.so: c/libwords.c
	@mkdir -p $(DATA_DIR)
	$(CC) -O2 -shared -fPIC -o $@ $<

clean:
	rm -f $(DATA_DIR)/libwords.so
