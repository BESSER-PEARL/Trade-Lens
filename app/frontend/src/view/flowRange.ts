import { contextOf, rangeStart } from "./context";
import type { Panel, ViewOp, ViewSpec } from "./types";

// The From/To range of the first diagram (contract 1.4), as flow_range_ops in agents/kit/view.py.
// The range lives in the page context: period is the To year and start the From year, so every
// panel, "about 2025" tag and dimming follows one page period, and an agent's set_context
// {period} moves the range controls too. A single year is From == To.

export const FLOW_RANGE_PANELS = new Set(["headline", "kpi_imports", "kpi_balance", "kpi_exports", "crossroads"]);
const RANGED_ENDPOINTS = new Set(["/insights/ranking", "/insights/ladder", "/insights/ranking/records"]);
const RANGED = { period: "$period", start: "$start", end: "$period" };

/** The years the page's totals cover: From and To. Null while there is no spec. */
export function shownFlowRange(spec: ViewSpec | null): { start: string; end: string } | null {
  if (!spec) return null;
  const ctx = contextOf(spec);
  const end = ctx.period.slice(0, 4);
  return { start: rangeStart({ ...ctx, period: end }), end };
}

/** A copy of a page panel whose ranking and ladder queries follow $start and $period. */
export function rangePanel(original: Panel): Panel {
  const panel: Panel = JSON.parse(JSON.stringify(original));
  panel.queries = panel.queries.map((q) => {
    if (!RANGED_ENDPOINTS.has(q.endpoint)) return q;
    const { freq, ...params } = (q.params || {}) as Record<string, any>;
    return { ...q, params: { ...params, ...RANGED } };
  });
  if (panel.facts) {
    const facts: Record<string, any> = {};
    Object.entries(panel.facts).forEach(([k, f]: [string, any]) => {
      if (f && f.endpoint && ["/insights/ranking", "/insights/ladder"].includes(String(f.endpoint))) {
        const { freq, ...params } = f.params || {};
        facts[k] = { ...f, params: { ...params, ...RANGED } };
      } else if (f && f.query && f.path === "period" && f.format === "period") facts[k] = { ...f, path: "span", format: "text" };
      else facts[k] = f;
    });
    panel.facts = facts;
  }
  return panel;
}

/** One set_context (period = To, start = From), plus, once, the page's own range panels made to follow it. */
export function flowRangeOps(spec: ViewSpec, start: string, end: string): ViewOp[] {
  if (!/^\d{4}$/.test(start) || !/^\d{4}$/.test(end) || start > end) return [];
  const ops: ViewOp[] = [{ op: "set_context", period: end, start } as ViewOp];
  spec.panels
    .filter((p) => FLOW_RANGE_PANELS.has(p.id) && [undefined, null, "baseline", "user"].includes(p.agent as any))
    .forEach((p) => {
      const next = rangePanel(p);
      if (JSON.stringify(next) !== JSON.stringify(p)) ops.push({ op: "upsert_panel", panel: { ...next, provenance: { sources: [] } } } as ViewOp);
    });
  return ops;
}
