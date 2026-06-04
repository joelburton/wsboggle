"""Microbenchmark for the C solver, exercised through
``wsboggle.libwords.generate_board``.

The point is to track libwords' performance as we improve the C
code (per-board arena, hash-table dedup, etc.). Same seeds across
runs = same boards generated = apples-to-apples timing.

Three constraint profiles:

- ``loose``: no min_* constraints. Single solver pass per board.
  Measures the *forward* cost of the DFS + tsearch insert path.
- ``medium``: min_words / min_score / min_longest set to values
  that boards from the 4×4 Revised set hit on roughly half the
  attempts. Exercises the rejection sampler at moderate volume.
- ``tight``: same constraints cranked higher so most attempts
  reject. The retry loop dominates wall time, so this is the
  profile that should move the most under arena allocation.

Default: run all three with 50 boards each.

Usage::

    python bench/bench_libwords.py                # all profiles, 50 boards each
    python bench/bench_libwords.py --n 200        # 200 boards each
    python bench/bench_libwords.py --profile tight
"""

from __future__ import annotations

import argparse
import hashlib
import statistics
import sys
import time
from dataclasses import dataclass

# Make the in-tree wsboggle importable when run from the repo root.
sys.path.insert(0, "src")

from wsboggle import dice, libwords  # noqa: E402


# Seeds are fixed so the *boards* generated are identical across
# runs of the script. Two improvements that produce the same set of
# boards but in different wall times = an honest comparison.
BASE_SEED = 0xB066_1E00


@dataclass(frozen=True)
class Profile:
    name: str
    dice_set: str
    min_words: int | None = None
    max_words: int | None = None
    min_score: int | None = None
    max_score: int | None = None
    min_longest: int | None = None
    max_longest: int | None = None
    description: str = ""


PROFILES = {
    "loose": Profile(
        name="loose",
        dice_set="4",
        description="4×4, no constraints; single DFS per board",
    ),
    "medium": Profile(
        name="medium",
        dice_set="4",
        min_words=100,
        min_score=200,
        min_longest=7,
        description="4×4, moderate rejection rate",
    ),
    "tight": Profile(
        name="tight",
        dice_set="4",
        min_words=140,
        min_score=300,
        min_longest=8,
        description="4×4, rejection-heavy",
    ),
    "extreme": Profile(
        name="extreme",
        dice_set="4",
        min_words=170,
        min_score=400,
        min_longest=9,
        description="4×4, near the upper bound of what's findable",
    ),
    "5x5-loose": Profile(
        name="5x5-loose",
        dice_set="5",
        description="5×5, no constraints; bigger work per attempt",
    ),
    "5x5-tight": Profile(
        name="5x5-tight",
        dice_set="5",
        min_words=400,
        min_score=900,
        min_longest=10,
        description="5×5, rejection-heavy; bigger payload + many retries",
    ),
}


@dataclass
class Result:
    boards: int
    total_seconds: float
    per_board_ms: list[float]
    tries: list[int]

    @property
    def total_tries(self) -> int:
        return sum(self.tries)

    @property
    def mean_ms(self) -> float:
        return statistics.mean(self.per_board_ms)

    @property
    def median_ms(self) -> float:
        return statistics.median(self.per_board_ms)

    @property
    def p95_ms(self) -> float:
        return _percentile(self.per_board_ms, 95)

    @property
    def boards_per_sec(self) -> float:
        return self.boards / self.total_seconds if self.total_seconds else float("inf")


def _percentile(xs: list[float], pct: float) -> float:
    if not xs:
        return 0.0
    s = sorted(xs)
    k = (len(s) - 1) * (pct / 100)
    lo = int(k)
    hi = min(lo + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


def run_profile(profile: Profile, n: int, *, max_tries: int = 50_000) -> Result:
    """Run one profile for ``n`` boards. Deterministic seed stream
    so repeated invocations exercise the same boards."""
    dset = dice.get_by_name(profile.dice_set)
    per_board_ms: list[float] = []
    tries_log: list[int] = []

    t0 = time.perf_counter()
    for i in range(n):
        seed = BASE_SEED + i
        t_start = time.perf_counter()
        gb = libwords.generate_board(
            dset,
            min_legal_length=3,
            min_words=profile.min_words,
            max_words=profile.max_words,
            min_score=profile.min_score,
            max_score=profile.max_score,
            min_longest=profile.min_longest,
            max_longest=profile.max_longest,
            max_tries=max_tries,
            random_seed=seed,
        )
        per_board_ms.append((time.perf_counter() - t_start) * 1000)
        tries_log.append(gb.tries)
    total = time.perf_counter() - t0

    return Result(
        boards=n,
        total_seconds=total,
        per_board_ms=per_board_ms,
        tries=tries_log,
    )


# --- Determinism fingerprint --------------------------------------------


# Seeds used for the determinism check. Fixed to 10 so the digest is
# stable regardless of bench --n; the timing run uses --n boards
# starting from the same BASE_SEED, so the first 10 there are the
# same boards fingerprinted here.
FINGERPRINT_N = 10


def fingerprint(profile: Profile) -> str:
    """SHA-256 over the first :data:`FINGERPRINT_N` boards of one
    profile.

    Hashes ``(raw_board || sorted-words-csv)`` for each board, then
    folds them together. Two libwords builds that produce the same
    boards from the same seeds will agree on this digest; any
    semantic change (different shuffles, dropped words, wrong
    score-ordering) will not. Pure-perf refactors that touch
    allocation, dedup data structure, etc. should leave the digest
    untouched.
    """
    dset = dice.get_by_name(profile.dice_set)
    h = hashlib.sha256()
    for i in range(FINGERPRINT_N):
        gb = libwords.generate_board(
            dset,
            min_legal_length=3,
            min_words=profile.min_words,
            max_words=profile.max_words,
            min_score=profile.min_score,
            max_score=profile.max_score,
            min_longest=profile.min_longest,
            max_longest=profile.max_longest,
            random_seed=BASE_SEED + i,
        )
        # legal_words is already lowercase + sorted by generate_board;
        # raw_board is the dice string with multiface chars encoded.
        h.update(gb.raw_board.encode("utf-8"))
        h.update(b"|")
        h.update(",".join(gb.legal_words).encode("utf-8"))
        h.update(b"\n")
    return h.hexdigest()[:16]  # short prefix is plenty for visual diff


def print_result(profile: Profile, r: Result, fp: str) -> None:
    print(f"\n=== {profile.name} ({profile.description}) ===")
    print(f"  dice set       : {profile.dice_set}")
    bounds = []
    for k in ("min_words", "max_words", "min_score", "max_score",
              "min_longest", "max_longest"):
        v = getattr(profile, k)
        if v is not None:
            bounds.append(f"{k}={v}")
    print(f"  constraints    : {', '.join(bounds) or '(none)'}")
    print(f"  boards         : {r.boards}")
    print(f"  total wall     : {r.total_seconds:.3f} s")
    print(f"  boards / sec   : {r.boards_per_sec:.1f}")
    print(f"  per-board mean : {r.mean_ms:.2f} ms")
    print(f"  per-board p50  : {r.median_ms:.2f} ms")
    print(f"  per-board p95  : {r.p95_ms:.2f} ms")
    print(f"  total retries  : {r.total_tries}")
    print(f"  retries / board: {r.total_tries / r.boards:.1f}")
    print(f"  fingerprint    : {fp}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="libwords timing benchmark",
    )
    parser.add_argument(
        "--n", type=int, default=50,
        help="boards to generate per profile (default 50)",
    )
    parser.add_argument(
        "--profile",
        choices=list(PROFILES) + ["all"],
        default="all",
        help="which profile to run (default: all)",
    )
    parser.add_argument(
        "--max-tries", type=int, default=50_000,
        help="solver retry cap per board (default 50000)",
    )
    args = parser.parse_args()

    profiles = (
        list(PROFILES.values())
        if args.profile == "all"
        else [PROFILES[args.profile]]
    )

    # One untimed warmup call so DAWG load + library dlopen don't
    # land in the loose-profile total.
    libwords.generate_board(
        dice.get_by_name("4"),
        max_tries=10,
        random_seed=1,
    )

    for p in profiles:
        r = run_profile(p, args.n, max_tries=args.max_tries)
        fp = fingerprint(p)
        print_result(p, r, fp)


if __name__ == "__main__":
    main()
