/**
 * Drag / resize / persist logic for floating UI panels.
 *
 * Ported nearly verbatim from crossplay's `draggablePanel.ts`. Each
 * panel needs the same four things: an initial :class:`Rect` (from
 * localStorage if present, else a sensible default), clamping against
 * the current viewport (on mount and on window resize), and a save
 * callback for `Rnd`'s `onDragStop` / `onResizeStop`.
 *
 * Currently only :file:`ChatPanel` uses this — but new floating
 * dialogs (rules, definition popovers) can share the hook without
 * re-inventing the persistence + clamping dance.
 */

import { useEffect, useState } from "react";

export type Rect = { x: number; y: number; width: number; height: number };

export type DraggablePanelOptions = {
  storageKey: string;
  minWidth: number;
  minHeight: number;
  viewportPad: number;
  defaultRect: () => Rect;
};

export type DraggablePanelApi = {
  rect: Rect;
  onDragStop: (x: number, y: number) => void;
  onResizeStop: (x: number, y: number, width: number, height: number) => void;
};

function clampToViewport(r: Rect, opts: DraggablePanelOptions): Rect {
  const { minWidth, minHeight, viewportPad: pad } = opts;
  const width = Math.max(minWidth, Math.min(r.width, window.innerWidth - pad * 2));
  const height = Math.max(minHeight, Math.min(r.height, window.innerHeight - pad * 2));
  const x = Math.max(pad, Math.min(r.x, window.innerWidth - width - pad));
  const y = Math.max(pad, Math.min(r.y, window.innerHeight - height - pad));
  return { x, y, width, height };
}

function loadRect(opts: DraggablePanelOptions): Rect {
  try {
    const raw = localStorage.getItem(opts.storageKey);
    if (!raw) return opts.defaultRect();
    const r = JSON.parse(raw) as Partial<Rect>;
    if (
      typeof r.x === "number" &&
      typeof r.y === "number" &&
      typeof r.width === "number" &&
      typeof r.height === "number"
    ) {
      return clampToViewport(r as Rect, opts);
    }
  } catch {
    // fall through to default
  }
  return opts.defaultRect();
}

function saveRect(storageKey: string, r: Rect): void {
  try {
    localStorage.setItem(storageKey, JSON.stringify(r));
  } catch {
    // localStorage may be disabled (private mode, quota); the panel
    // still works in-memory for the session.
  }
}

export function useDraggablePanel(opts: DraggablePanelOptions): DraggablePanelApi {
  const [rect, setRect] = useState<Rect>(() => loadRect(opts));

  useEffect(() => {
    function onResize() {
      setRect((cur) => {
        const next = clampToViewport(cur, opts);
        if (
          next.x === cur.x &&
          next.y === cur.y &&
          next.width === cur.width &&
          next.height === cur.height
        ) return cur;
        saveRect(opts.storageKey, next);
        return next;
      });
    }
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
    // opts is captured by closure; callers pass a stable object
    // literal (the panel components define the constants at module
    // scope).
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return {
    rect,
    onDragStop(x, y) {
      const next = { ...rect, x, y };
      setRect(next);
      saveRect(opts.storageKey, next);
    },
    onResizeStop(x, y, width, height) {
      const next = { x, y, width, height };
      setRect(next);
      saveRect(opts.storageKey, next);
    },
  };
}
