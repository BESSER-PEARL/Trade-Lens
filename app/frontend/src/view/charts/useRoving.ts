// One roving tab stop over a chart's data marks: Tab reaches the chart once, the arrow
// keys move between marks (Home and End jump), Enter or Space activates, Escape hides the
// readout. Hover and focus share one readout state, placed in the wrapper's pixel space.

import React, { useCallback, useRef, useState } from "react";
import { elementPoint, localPoint } from "./ChartTip";

export interface RovingHover {
  i: number;
  x: number;
  y: number;
}

export interface Roving<E extends Element> {
  tab: number;
  hover: RovingHover | null;
  wrap: React.MutableRefObject<HTMLDivElement | null>;
  /** ref callback for mark i. */
  register: (i: number) => (el: E | null) => void;
  onKeyDown: (e: React.KeyboardEvent) => void;
  /** Props for mark i: tab stop, focus and hover handlers. */
  itemProps: (i: number) => {
    tabIndex: number;
    onFocus: (e: React.FocusEvent<E>) => void;
    onBlur: () => void;
    onMouseMove: (e: React.MouseEvent<E>) => void;
    onClick?: () => void;
  };
  setHover: (h: RovingHover | null) => void;
  /** Shows the readout of mark i at a pointer position (charts with their own hit test). */
  hoverAt: (i: number, e: { clientX: number; clientY: number }) => void;
}

export function useRoving<E extends Element = SVGElement>(
  count: number,
  opts: { onActivate?: (i: number) => void; grid?: { cols: number } } = {},
): Roving<E> {
  const [tab, setTab] = useState(0);
  const [hover, setHover] = useState<RovingHover | null>(null);
  const wrap = useRef<HTMLDivElement | null>(null);
  const nodes = useRef<Array<E | null>>([]);
  const cur = Math.min(Math.max(0, tab), Math.max(0, count - 1));

  const focusAt = useCallback((i: number) => {
    setTab(i);
    const el = nodes.current[i] as any;
    if (el && typeof el.focus === "function") el.focus();
  }, []);

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (!count) return;
    const cols = opts.grid?.cols || 0;
    let next = cur;
    if (e.key === "ArrowRight") next = Math.min(count - 1, cur + 1);
    else if (e.key === "ArrowLeft") next = Math.max(0, cur - 1);
    else if (e.key === "ArrowDown") next = cols ? Math.min(count - 1, cur + cols) : Math.min(count - 1, cur + 1);
    else if (e.key === "ArrowUp") next = cols ? Math.max(0, cur - cols) : Math.max(0, cur - 1);
    else if (e.key === "Home") next = 0;
    else if (e.key === "End") next = count - 1;
    else if ((e.key === "Enter" || e.key === " ") && opts.onActivate) {
      e.preventDefault();
      opts.onActivate(cur);
      return;
    } else if (e.key === "Escape") {
      setHover(null);
      return;
    } else return;
    e.preventDefault();
    focusAt(next);
  };

  const itemProps = (i: number) => ({
    tabIndex: i === cur ? 0 : -1,
    onFocus: (e: React.FocusEvent<E>) => {
      setTab(i);
      const p = elementPoint(e.currentTarget as unknown as Element, wrap.current);
      setHover({ i, x: p.x, y: p.y });
    },
    onBlur: () => setHover(null),
    onMouseMove: (e: React.MouseEvent<E>) => {
      const p = localPoint(e, wrap.current);
      setHover({ i, x: p.x, y: p.y });
    },
    onClick: opts.onActivate
      ? () => {
          setTab(i);
          opts.onActivate!(i);
        }
      : undefined,
  });

  const register = (i: number) => (el: E | null) => {
    nodes.current[i] = el;
  };

  const hoverAt = (i: number, e: { clientX: number; clientY: number }) => {
    const p = localPoint(e, wrap.current);
    setHover({ i, x: p.x, y: p.y });
  };

  return { tab: cur, hover, wrap, register, onKeyDown, itemProps, setHover, hoverAt };
}
