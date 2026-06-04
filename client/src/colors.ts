/**
 * Per-player color helper.
 *
 * Ported from crossplay's `chatIdentity.ts` (the same friend group
 * plays both apps; identical color logic = identical visual identity
 * across the two). Every handle deterministically hashes to one of
 * eight palette entries — no server coordination needed, every
 * client agrees that "moth" is the same shade everywhere.
 *
 * Used for: the club member list (dot + handle color when in-club),
 * chat handles, and per-player blocks in the end-of-game result
 * panel. Future use: when collaborative mode lands, the same color
 * marks which player added each word to the shared list.
 *
 * The palette is the same eight high-saturation shades crossplay
 * uses, picked across the hue wheel so adjacent players are easy to
 * tell apart on white.
 */

export const PLAYER_PALETTE = [
  "#ef4444", // red
  "#f97316", // orange
  "#ca8a04", // amber
  "#22c55e", // green
  "#06b6d4", // cyan
  "#3b82f6", // blue
  "#8b5cf6", // violet
  "#ec4899", // pink
];

/** Hash a handle to a stable palette index. Plain 31-multiplier
 *  string hash; same algorithm crossplay uses so the two apps agree
 *  on each user's color. */
export function colorForHandle(handle: string): string {
  let h = 0;
  for (let i = 0; i < handle.length; i++) {
    h = (h * 31 + handle.charCodeAt(i)) >>> 0;
  }
  return PLAYER_PALETTE[h % PLAYER_PALETTE.length]!;
}

/** Color for an offline member — same value used by the "not in
 *  club" presence dot and the dimmed handle text. Centralized so a
 *  future tweak (darker dot, lighter text, etc.) only changes one
 *  spot. */
export const OFFLINE_COLOR = "#9ca3af";
