# Notes: biased dice shuffling for constraint-tight searches

A research note, not a plan. Captured from a 2026-06 conversation about
the C library audit so we can pick it up later if super-tight constraint
searches ever become a felt problem.

## The opportunity

Today `libwords` treats the dice shuffle and the constraint check as
fully independent: shuffle dice, run a full DFS, accept or reject the
whole board, repeat. For constraint-tight searches the rejection
sampler does hundreds of full DFS runs per accepted board. Most of
those DFS runs fail not because the search came up short, but because
the dice roll *could not have produced* a passing board in the first
place — and that's often detectable from the letter multiset alone, no
DFS needed.

Measured baselines (post-audit, on the `brutal` profile: 4×4,
min_words=140, min_longest=10): **~394 retries/board, ~12 ms/board.**
A pre-DFS necessary-condition filter could plausibly drop that to
~10–30 retries / ~1–2 ms.

This is bigger than any remaining C-level optimization on the ranking,
because it cuts work rather than constant factors. But it changes
sampling semantics, so we want to think carefully.

## The trap (samey-samey boards)

Naive approach: favor "good letters." Pick more vowels, prefer RSTLNEA,
include common suffix triggers (`-ING`, `-ED`, `-ER`). It works — long
words show up faster — but every board ends up looking the same. The
player notices within a few games.

NYT Spelling Bee has the same failure mode. Greedy "pick the 7 letters
with the most possible words" tilts hard toward pangrams with `-ING`,
`-ED`, `-AT`, etc., and the puzzle set becomes monotonous. (Worth
revisiting this doc if/when the Spelling Bee project starts; the
plumbing is shared.)

## The way out: necessary conditions, not preferences

Reject shuffles that **can't** meet the constraint. Don't favor
shuffles that **probably can**. The distinction is everything: a
necessary-condition filter preserves the natural distribution
conditioned on "possible." A preference filter distorts the
distribution toward whatever the heuristic likes.

For `min_longest=11`, the necessary conditions are all cheap to check
on the dice roll before any DFS:

- ≥ 4 vowels somewhere on the board (no 11-letter English word has
  fewer).
- A path of 11 mutually-adjacent tiles must exist at all. This is a
  property of board geometry (which tiles are reachable from which),
  independent of the letters — pre-computable per `(width, height,
  min_longest)` triple at startup.
- The DAWG has at least one 11-letter word whose letter multiset is a
  subset of the dice's letter multiset. Implementable as a 26-letter
  bitmask per long word, AND with the board's letter mask. Cheap to
  check against the whole list of 11-letter words at once.

None of these picks "better" letters. They reject obviously-doomed
shuffles and let everything else through unchanged. Boards that pass
are still drawn from the natural distribution, just with the dead
branches pruned at the cheap end of the pipeline rather than after a
full DFS.

## Soundness contract

The pre-filter has a strong asymmetric requirement:

- **False positives are fine.** ("This board *might* be solvable.")
  Worst case: we run a DFS that turns out to fail. Same cost as today.
- **False negatives are catastrophic.** ("This board *can't* be
  solvable" — but actually it could.) Now we're silently excluding
  valid boards from the sample. The output is biased, and we can't
  tell from the outside.

So the filter has to be **provably conservative**: every condition it
checks must be necessary for the constraint, not just suggestive.
"Has 4 vowels" is necessary for 11-letter words; "has at least one
common letter" is not.

## What this would look like in libwords

The current `fill_board` loop:

```c
while (count++ < max_tries) {
    make_dice(board);
    if (find_all_words(board)) break;
}
```

Becomes:

```c
while (count++ < max_tries) {
    make_dice(board);
    if (!letter_set_supports_constraints(board)) continue;
    if (find_all_words(board)) break;
}
```

`letter_set_supports_constraints` runs in microseconds (vs. ms for a
full DFS). The set of conditions it checks is driven by which
constraints are active — `min_longest` enables the vowel-count and
DAWG-subset checks, `min_words` would have a different set (probably
"the board's letter mask covers at least K of the most common 1000
words"), etc.

A startup hook pre-computes per-`(min_longest)` data: the list of
11-letter words and their letter masks, the adjacency reachability
table per board size.

## When to actually do this

Not yet. Defer until either:

- A player asks for super-tight constraint searches and the wait is
  felt. On default 4×4 3-minute games the existing 0.08 ms/board is
  imperceptible; this only matters at the `brutal`-style end.
- The Spelling Bee project starts and could share the
  necessary-condition framework.

If we do it, the test surface is the same fingerprint check we use
today — the *set of boards generated for a given seed sequence* will
change (we'll skip some that DFS would have rejected anyway), but
every board that does pass should still produce the same DFS output.
We'd need a separate test asserting "no board the filter rejects
would have passed the DFS" — random sampling against the unfiltered
path would catch a soundness regression.
