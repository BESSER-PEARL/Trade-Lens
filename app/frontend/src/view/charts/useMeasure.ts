// Container width for responsive SVG: a chart draws into the width it is given, never into a
// fixed one. Without ResizeObserver (older browsers, tests) it reports the first width once.

import { useEffect, useRef, useState } from "react";

export function useMeasure<T extends HTMLElement = HTMLDivElement>(fallbackWidth = 640) {
  const ref = useRef<T | null>(null);
  const [width, setWidth] = useState(fallbackWidth);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const read = () => {
      const w = el.getBoundingClientRect().width;
      if (w > 0) setWidth(Math.round(w));
    };
    read();
    if (typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(read);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  return { ref, width };
}

/** True when the user asked for less motion. */
export function prefersReducedMotion(): boolean {
  return typeof window !== "undefined" && !!window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}
