# wsboggle build targets.
#
# `make bootstrap` — one-time copy of game-data files from a sibling
#                    ../tboggle checkout (libwords.c, words.dat,
#                    all.sqlite3). After this you can build, run, and
#                    ship without depending on tboggle.
# `make lib`       — compile data/libwords.so from c/libwords.c.
# `make clean`     — remove built artifacts (keeps bootstrapped data).

TBOGGLE_SRC := ../tboggle/src/tboggle
TBOGGLE_BUILD_C := ../tboggle/build/lib.macosx-15.0-arm64-cpython-313/tboggle/libwords.c

# Honour DATA_DIR if exported; default to ./data for source checkouts.
DATA_DIR ?= data

.PHONY: all bootstrap lib clean

all: lib

bootstrap: $(DATA_DIR)/words.dat $(DATA_DIR)/all.sqlite3 c/libwords.c

lib: $(DATA_DIR)/libwords.so

# libwords.c is plain C (no Python C API) — ctypes loads it directly,
# so no Python include or linker glue is needed.
$(DATA_DIR)/libwords.so: c/libwords.c
	@mkdir -p $(DATA_DIR)
	$(CC) -O2 -shared -fPIC -o $@ $<

$(DATA_DIR)/words.dat:
	@mkdir -p $(DATA_DIR)
	@if [ -f $(TBOGGLE_SRC)/words.dat ]; then \
	  cp $(TBOGGLE_SRC)/words.dat $@; \
	  echo "copied words.dat from $(TBOGGLE_SRC)"; \
	else \
	  echo "ERROR: $(TBOGGLE_SRC)/words.dat not found."; \
	  echo "Place words.dat into $(DATA_DIR)/ manually."; \
	  exit 1; \
	fi

$(DATA_DIR)/all.sqlite3:
	@mkdir -p $(DATA_DIR)
	@if [ -f $(TBOGGLE_SRC)/all.sqlite3 ]; then \
	  cp $(TBOGGLE_SRC)/all.sqlite3 $@; \
	  echo "copied all.sqlite3 from $(TBOGGLE_SRC)"; \
	else \
	  echo "ERROR: $(TBOGGLE_SRC)/all.sqlite3 not found."; \
	  echo "Place all.sqlite3 into $(DATA_DIR)/ manually."; \
	  exit 1; \
	fi

c/libwords.c:
	@mkdir -p c
	@if [ -f $(TBOGGLE_BUILD_C) ]; then \
	  cp $(TBOGGLE_BUILD_C) $@; \
	  echo "copied libwords.c from $(TBOGGLE_BUILD_C)"; \
	elif [ -f $(TBOGGLE_SRC)/libwords.c ]; then \
	  cp $(TBOGGLE_SRC)/libwords.c $@; \
	  echo "copied libwords.c from $(TBOGGLE_SRC)"; \
	else \
	  echo "ERROR: libwords.c not found in tboggle."; \
	  echo "Place libwords.c into c/ manually."; \
	  exit 1; \
	fi

clean:
	rm -f $(DATA_DIR)/libwords.so
