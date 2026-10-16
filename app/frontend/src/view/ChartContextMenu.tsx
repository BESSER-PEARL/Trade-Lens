// Each chart's own year and goods (contract 1.5). The page has no Year, From/To or Goods control of its
// own any more: a chart that follows the page context ($period, $previous, $start, $product in its
// queries) shows one quiet line in its header, "2025, all goods" or "2015 to 2025, machinery". Clicking it
// opens a small row with Year (or From/To for the range charts: the three figures and the crossroads) and
// Goods, only for what the chart follows. A change sends set_panel_context as agent "user", so only that
// chart moves; "Follow the page" clears its own context. An agent's set_context ("Show me 2020") still
// moves every chart without one. The three figures share one menu.

import React from "react";
import { shortName } from "./BarPanel";
import { placeholdersOf, rangeStart, withPanelContext, type PageContext } from "./context";
import { FLOW_RANGE_PANELS, rangePanel } from "./flowRange";
import { useQuery } from "./useQuery";
import { useSpecContext, useViewStore } from "./ViewStore";
import type { Panel, ViewOp, ViewResponse } from "./types";

const FALLBACK_YEARS = Array.from({ length: 27 }, (_, i) => String(1999 + i));

/** The years of the annual data, newest first (/insights/meta). */
export function useYears(): string[] {
  const meta = useQuery<{ periods?: { A?: string[] } }>({ endpoint: "/insights/meta", params: {} });
  return React.useMemo(() => {
    const a = meta.data?.periods?.A;
    return (Array.isArray(a) && a.length ? a : FALLBACK_YEARS).slice().reverse();
  }, [meta.data]);
}

/** "Electrical machinery (HS 85)"; "All goods" for TOTAL. */
export function goodsLabel(code: string, name?: string): string {
  if (code === "TOTAL") return "All goods";
  return `${shortName(name || code, code)} (HS ${code})`;
}

/** The goods a chart can show: all goods and the twelve largest export chapters of a year (one shared query). */
export function useGoodsChoices(year: string, current?: string): Array<{ code: string; label: string }> {
  const q = useQuery<{ items?: Array<{ code: string; name: string }> }>({
    endpoint: "/insights/ranking",
    params: { by: "product", period: year.slice(0, 4), flow: "EXPORT", max_items: 12 },
  });
  return React.useMemo(() => {
    const items = (q.data?.items || []).filter((it) => it && /^\d{2}$/.test(String(it.code)));
    const list = items.map((it) => ({ code: String(it.code), label: goodsLabel(String(it.code), it.name) }));
    if (current && current !== "TOTAL" && !list.some((g) => g.code === current)) list.unshift({ code: current, label: goodsLabel(current) });
    return list;
  }, [q.data, current]);
}

/** The short name of the goods a code stands for, lower case for a sentence: "all goods", "electrical machinery". */
export function useGoodsName(code: string, year: string): string {
  const goods = useGoodsChoices(year, code);
  if (code === "TOTAL") return "all goods";
  const label = goods.find((g) => g.code === code)?.label;
  return label ? label.replace(/ \(HS .*\)$/, "").toLowerCase() : `HS ${code}`;
}

/** What a chart can set: its year (one year, or a From/To range) and its goods, as its queries follow the page. */
export function menuKindOf(panel: Panel | undefined): { year: "year" | "range" | null; goods: boolean } {
  if (!panel || panel.type === "text") return { year: null, goods: false };
  const uses = placeholdersOf(panel);
  const followsYear = uses.has("$period") || uses.has("$previous");
  // The page's own range charts (the three figures, the crossroads) may be made to follow $start.
  const pageOwn = [undefined, null, "baseline", "user"].indexOf(panel.agent as any) >= 0;
  const ranged = uses.has("$start") || (followsYear && pageOwn && FLOW_RANGE_PANELS.has(panel.id));
  return { year: ranged ? "range" : followsYear ? "year" : null, goods: uses.has("$product") };
}

function sameOwn(a: Record<string, any> | undefined, b: Record<string, any> | undefined): boolean {
  return JSON.stringify(a || {}) === JSON.stringify(b || {});
}

function why(r: ViewResponse): string {
  const e = r.errors && r.errors[0];
  if (r.status === 0) return "The page service is not reachable, so nothing changed.";
  if (e && e.code === "pinned") return "It is kept: unpin it first.";
  return (e && e.message) || r.detail || "The page did not take it; try again.";
}

/**
 * The quiet line and its row of selects, for one chart or for charts that share it (the three figures).
 * Renders nothing for a chart that follows nothing of the page.
 */
export const ChartContextMenu: React.FC<{ panels: Panel[]; name?: string; className?: string }> = ({ panels, name, className }) => {
  const { send, fallback } = useViewStore();
  const page = useSpecContext();
  const first = panels[0];
  const kind = menuKindOf(first);
  const ctx: PageContext = withPanelContext(page, first);
  const own = first?.context;
  const hasOwn = panels.some((p) => !!p.context && Object.keys(p.context).length > 0);
  const year = ctx.period;
  const from = rangeStart(ctx);
  const years = useYears();
  const goods = useGoodsChoices(page.period, ctx.product);
  const goodsName = ctx.product === "TOTAL" ? "all goods" : (goods.find((g) => g.code === ctx.product)?.label || `HS ${ctx.product}`).replace(/ \(HS .*\)$/, "").toLowerCase();
  const [open, setOpen] = React.useState(false);
  const [busy, setBusy] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);
  const btn = React.useRef<HTMLButtonElement | null>(null);
  const box = React.useRef<HTMLSpanElement | null>(null);
  const uid = React.useId();

  const close = React.useCallback((refocus: boolean) => {
    setOpen(false);
    setError(null);
    if (refocus) window.setTimeout(() => btn.current?.focus(), 0);
  }, []);

  // Escape, or a click outside, closes the row; focus goes back to the line when it was in the row.
  React.useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.stopPropagation();
        close(true);
      }
    };
    const onDown = (e: MouseEvent | TouchEvent) => {
      if (box.current && !box.current.contains(e.target as Node)) {
        const inside = !!document.activeElement && box.current.contains(document.activeElement);
        close(false);
        if (inside) window.setTimeout(() => {
          if (document.activeElement === document.body) btn.current?.focus();
        }, 0);
      }
    };
    document.addEventListener("keydown", onKey);
    document.addEventListener("mousedown", onDown);
    document.addEventListener("touchstart", onDown);
    return () => {
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("touchstart", onDown);
    };
  }, [open, close]);

  if (!first || (!kind.year && !kind.goods)) return null;

  const yearText = kind.year === "range" && from < year ? `${from} to ${year}` : year;
  const parts = [kind.year ? yearText : "", kind.goods ? goodsName : ""].filter(Boolean);
  let line = parts.join(", ");
  line = line.charAt(0).toUpperCase() + line.slice(1);
  const chart = name || (first.title ? `“${first.title}”` : "this chart");

  /** Sends the new own context of every panel of the menu (and, for a range, the upserts that make them follow $start). */
  const apply = async (next: { period?: string | null; start?: string | null; product?: string | null } | null, message: string) => {
    if (busy) return;
    const ops: ViewOp[] = [];
    panels.forEach((p) => {
      let target: Panel = p;
      const want: Record<string, any> = next ? { ...(p.context || {}), ...next } : {};
      Object.keys(want).forEach((k) => {
        if ((want as any)[k] == null) delete (want as any)[k];
      });
      // a range on a page chart that does not follow $start yet: make it follow it first (as the page's
      // From/To did), in the same batch
      if (want.start && want.period && want.start < want.period && !placeholdersOf(p).has("$start")) {
        target = rangePanel(p);
        ops.push({ op: "upsert_panel", panel: { ...target, context: undefined, provenance: { sources: [] } } } as ViewOp);
      }
      if (want.start && (!want.period || want.start >= want.period)) delete want.start;
      if (!sameOwn(want, target.context) || target !== p) ops.push({ op: "set_panel_context", id: p.id, ...want } as ViewOp);
    });
    if (!ops.length) return;
    setBusy(true);
    setError(null);
    const r = await send(ops, { message, baseVersion: null });
    setBusy(false);
    if (!r.ok) setError(why(r));
  };

  const off = busy || fallback;
  const label = (s: string) => `${chart}: ${s}`;
  const yearOptions = (value: string) => Array.from(new Set([...years, value])).sort().reverse();

  return (
    <span ref={box} className={`cx-ctxmenu${open ? " is-open" : ""}${className ? ` ${className}` : ""}`}>
      <button
        ref={btn}
        type="button"
        className="cx-ctxmenu-btn"
        aria-expanded={open}
        aria-controls={uid}
        disabled={fallback}
        title={hasOwn ? "This chart has its own year or goods. Click to change them." : "This chart follows the page. Click to give it its own year or goods."}
        onClick={() => (open ? close(false) : setOpen(true))}
      >
        {hasOwn && <span className="cx-ctxmenu-dot" aria-hidden="true" />}
        <span>{line}</span>
        <span className="sr-only">{hasOwn ? ", its own setting" : ", as the page"}. Change the {kind.year && kind.goods ? "year and goods" : kind.year ? "year" : "goods"} of {chart}</span>
      </button>
      {open && (
        <span id={uid} className="cx-ctxmenu-row" role="group" aria-label={`The ${kind.year && kind.goods ? "year and goods" : kind.year ? "year" : "goods"} of ${chart}`}>
          {kind.year === "year" && (
            <label className="cx-ctxmenu-field">
              Year
              <select
                className="cx-ctxmenu-select"
                autoFocus
                value={year}
                disabled={off}
                aria-label={label("year")}
                onChange={(e) => apply({ period: e.target.value, start: null }, `${chart} shows ${e.target.value}`)}
              >
                {yearOptions(year).map((y) => (
                  <option key={y} value={y}>
                    {y}
                  </option>
                ))}
              </select>
            </label>
          )}
          {kind.year === "range" &&
            (["start", "end"] as const).map((bound) => (
              <label key={bound} className="cx-ctxmenu-field">
                {bound === "start" ? "From" : "To"}
                <select
                  className="cx-ctxmenu-select"
                  autoFocus={bound === "start"}
                  value={bound === "start" ? from : year}
                  disabled={off}
                  aria-label={label(bound === "start" ? "from year" : "to year")}
                  onChange={(e) => {
                    let start = bound === "start" ? e.target.value : from;
                    let end = bound === "end" ? e.target.value : year;
                    // Moving To before From (or From after To) moves the other bound along: one year.
                    if (start > end) {
                      if (bound === "start") end = start;
                      else start = end;
                    }
                    apply({ period: end, start: start < end ? start : null }, `${chart} shows ${start === end ? end : `${start} to ${end}`}`);
                  }}
                >
                  {yearOptions(bound === "start" ? from : year).map((y) => (
                    <option key={y} value={y}>
                      {y}
                    </option>
                  ))}
                </select>
              </label>
            ))}
          {kind.goods && (
            <label className="cx-ctxmenu-field cx-ctxmenu-field--goods">
              Goods
              <select
                className="cx-ctxmenu-select cx-ctxmenu-select--goods"
                autoFocus={!kind.year}
                value={ctx.product}
                disabled={off}
                aria-label={label("goods")}
                onChange={(e) => apply({ product: e.target.value }, `${chart} shows ${e.target.value === "TOTAL" ? "all goods" : goodsLabel(e.target.value, goods.find((g) => g.code === e.target.value)?.label)}`)}
              >
                <option value="TOTAL">All goods</option>
                {goods.map((g) => (
                  <option key={g.code} value={g.code}>
                    {g.label}
                  </option>
                ))}
              </select>
            </label>
          )}
          {hasOwn && (
            <button type="button" className="cx-linkbtn cx-ctxmenu-follow" disabled={off} onClick={() => apply(null, `${chart} follows the page again`)}>
              Follow the page
            </button>
          )}
          {error && (
            <span className="cx-pctl-error" role="alert">
              {error}
            </span>
          )}
        </span>
      )}
    </span>
  );
};
