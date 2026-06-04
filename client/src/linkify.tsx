/**
 * Wrap bare http(s) URLs in a chat message with anchor tags.
 *
 * Ported verbatim from crossplay so chat behaves identically across
 * the two apps. Trailing punctuation (`.`, `,`, `)`, etc.) is split
 * off the URL and rendered as plain text, so "see https://example.com."
 * doesn't link the period.
 *
 * Returns a `ReactNode` — typically an array of strings and `<a>`
 * elements, or the bare empty string when the input is empty. React
 * renders either shape correctly. Pure — exported for direct unit
 * testing.
 */

import type { ReactNode } from "react";

const URL_RE = /https?:\/\/\S+/g;
const TRAIL_RE = /[.,!?;:)\]}>]+$/;

export function linkify(text: string): ReactNode {
  const parts: ReactNode[] = [];
  let last = 0;
  let key = 0;
  for (const m of text.matchAll(URL_RE)) {
    const start = m.index!;
    if (start > last) parts.push(text.slice(last, start));
    let url = m[0];
    let trail = "";
    const tm = url.match(TRAIL_RE);
    if (tm) {
      trail = tm[0];
      url = url.slice(0, -trail.length);
    }
    parts.push(
      <a key={key++} href={url} target="_blank" rel="noopener noreferrer">
        {url}
      </a>,
    );
    if (trail) parts.push(trail);
    last = start + m[0].length;
  }
  if (last < text.length) parts.push(text.slice(last));
  return parts.length === 0 ? text : parts;
}
