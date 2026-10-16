// Table panels: the rows of the first query, with the columns in
// options.columns (or the scalar fields of the first row). A highlight mark
// rules the rows whose code it names, in the agent's pen (dimmed when it is
// about another period than the page shows). Clicking a row highlights it as
// the person's own mark. Queries follow the page context ("$period" ...).

import React from "react";
import { FactText, NumberCircle, problemCard } from "./common";
import { formatValue, keyed, useFacts } from "./facts";
import { FONT_UI, NUM_STYLE, TOKENS } from "./tokens";
import { firstProblem, useQueries } from "./useQuery";
import { OFF_CONTEXT_OPACITY, markOffContext, substituteQueries, usePageContext, useUserHighlight } from "./ViewStore";
import type { DrawnMark } from "./marks";
import type { Panel } from "./types";
import { itemLabel } from "./charts/format";
import { useShowCodes } from "./codes";

function rowsOf(data: any): any[] {
  if (Array.isArray(data)) return data;
  if (data && Array.isArray(data.items)) return data.items;
  if (data && Array.isArray(data.rows)) return data.rows;
  return [];
}

const LABELS: Record<string, string> = {
  code: "Code",
  name: "Name",
  value: "Value",
  share: "Share",
  growth: "Growth",
  rca: "RCA",
  rank: "Rank",
  period: "Period",
  base_value: "Base value",
  cum_share: "Cumulative share",
  slope_per_year: "Slope per year",
  // contract 1.8: a ranking read with measure=all
  weight: "Weight",
  units: "Quantity",
  eur_per_tonne: "€ per tonne",
  eur_per_unit: "€ per unit",
  quantity_note: "Note",
};

/** Quantity columns: a missing one reads "not published", never blank or 0. */
const QUANTITY_COLUMNS = ["weight", "units", "eur_per_tonne", "eur_per_unit"];
const NOT_PUBLISHED = "not published";

function columnLabel(c: string): string {
  if (LABELS[c]) return LABELS[c];
  const s = c.replace(/_/g, " ");
  return s.charAt(0).toUpperCase() + s.slice(1);
}

/** Format of a column: options.format for the y column, euros for values, fractions for shares from /ranking. */
function columnFormat(c: string, yField: string, format: string | undefined, sample: any, row?: any, unit?: string): string {
  if (c === "weight") return "tonnes";
  // what is counted: the panel's word for a number of items ("cars"), else the line's own unit ("litres")
  if (c === "units") return unit && (!row?.unit || row.unit === "items") ? `qty:${unit}` : row && row.unit ? `qty:${row.unit}` : "qty";
  if (c === "eur_per_tonne" || c === "eur_per_unit") return "eur";
  if (c === yField && format) return format;
  if (c === "value" || c === "base_value") return "eur";
  if ((c === "share" || c === "cum_share") && typeof sample === "number" && sample <= 1) return "pct";
  if (c === "share" || c === "growth") return "percent";
  if (c === "rank") return "int";
  return "number";
}

/** The first highlight of each code. */
function hlBy0(marks: DrawnMark[]): Record<string, DrawnMark> {
  const out: Record<string, DrawnMark> = {};
  marks
    .filter((d) => d.mark.kind === "highlight")
    .forEach((d) => (d.mark.params?.codes || []).forEach((c: string) => (out[c] = out[c] || d)));
  return out;
}

export const TablePanel: React.FC<{ panel: Panel; marks?: DrawnMark[] }> = ({ panel, marks = [] }) => {
  const showCodes = useShowCodes(); // the reader's profile may hide HS codes (contract 1.4)
  const o = panel.options || {};
  const ctx = usePageContext();
  const queries = React.useMemo(() => substituteQueries(panel.queries, ctx), [panel.queries, ctx]);
  const states = useQueries(queries);
  const facts = useFacts(panel.facts, keyed(queries, states));
  const user = useUserHighlight(panel.id);
  const problem = firstProblem(states.slice(0, 1));
  if (problem) return problemCard(problem, 160);

  const exclude = Array.isArray(o.exclude_codes) ? (o.exclude_codes as string[]) : [];
  let rows = rowsOf(states[0].data).filter((r) => r && exclude.indexOf(String(r.code)) < 0);
  if (typeof o.limit === "number") rows = rows.slice(0, o.limit);
  const first = rows[0] || {};
  const columns: string[] = Array.isArray(o.columns)
    ? (o.columns as string[])
    : Object.keys(first).filter((k) => ["string", "number"].indexOf(typeof first[k]) >= 0 && k !== "is_focus");
  const yField = String(o.y || "value");
  const format = o.format ? String(o.format) : undefined;
  const anyMark = rows.some((r) => !!hlBy0(marks)[String(r.code)]);

  const hlBy = hlBy0(marks);
  const shown: Record<string, boolean> = {};

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 10, minWidth: 0 }}>
      <div style={{ overflowX: "auto" }}>
        <table style={{ width: "100%", borderCollapse: "collapse", fontFamily: FONT_UI, fontSize: 14 }}>
          <thead>
            <tr>
              {anyMark && <th aria-label="Marks" style={{ width: 34, minWidth: 34, padding: 0, borderBottom: `2px solid ${TOKENS.ink}` }} />}
              {columns.map((c) => {
                const numeric = typeof first[c] === "number" || QUANTITY_COLUMNS.indexOf(c) >= 0;
                return (
                  <th
                    key={c}
                    scope="col"
                    style={{ textAlign: numeric ? "right" : "left", fontWeight: 600, color: TOKENS.muted, padding: "6px 8px", borderBottom: `2px solid ${TOKENS.ink}` }}
                  >
                    {columnLabel(c)}
                  </th>
                );
              })}
            </tr>
          </thead>
          <tbody>
            {rows.map((r, i) => {
              const d = hlBy[String(r.code)];
              const showN = d && d.showNumber && !shown[d.mark.id];
              if (showN) shown[d.mark.id] = true;
              const off = d ? markOffContext(d.mark, ctx) : false;
              const code = r.code !== undefined ? String(r.code) : undefined;
              const pick = () => {
                if (code && code !== "OTHER") user.toggle(code, String(r.name ?? code));
              };
              return (
                <tr
                  key={String(r.code ?? i)}
                  data-mark={d ? d.mark.id : undefined}
                  tabIndex={code ? 0 : undefined}
                  aria-pressed={code ? !!user.markOf(code) : undefined}
                  onClick={code ? pick : undefined}
                  onKeyDown={(e) => {
                    if (code && (e.key === "Enter" || e.key === " ")) {
                      e.preventDefault();
                      pick();
                    }
                  }}
                  className={code ? "cx-tr" : undefined}
                  style={{
                    boxShadow: d ? `inset 3px 0 0 ${off ? TOKENS.field : d.color}` : undefined,
                    fontWeight: r.is_focus ? 700 : 400,
                    cursor: code ? "pointer" : undefined,
                  }}
                >
                  {anyMark && (
                    <td style={{ width: 34, minWidth: 34, padding: "7px 0 7px 9px", borderBottom: `1px solid ${TOKENS.ruleSoft}` }}>
                      {showN ? (
                        <NumberCircle n={d.number} color={d.color} size={18} style={{ verticalAlign: -3, opacity: off ? OFF_CONTEXT_OPACITY : 1 }} />
                      ) : null}
                    </td>
                  )}
                  {columns.map((c) => {
                    const v = r[c];
                    const numeric = typeof v === "number";
                    if (v == null && QUANTITY_COLUMNS.indexOf(c) >= 0) {
                      return (
                        <td
                          key={c}
                          title={r.units_note || r.quantity_note || "No quantity published for this line"}
                          style={{ textAlign: "right", padding: "7px 8px", borderBottom: `1px solid ${TOKENS.ruleSoft}`, color: TOKENS.muted, fontSize: 12, whiteSpace: "nowrap" }}
                        >
                          {NOT_PUBLISHED}
                        </td>
                      );
                    }
                    return (
                      <td
                        key={c}
                        style={{
                          textAlign: numeric ? "right" : "left",
                          padding: "7px 8px",
                          borderBottom: `1px solid ${TOKENS.ruleSoft}`,
                          color: d && !numeric ? d.ink : TOKENS.ink,
                          whiteSpace: numeric ? "nowrap" : undefined,
                          ...NUM_STYLE,
                        }}
                      >
                        {numeric
                          ? formatValue(v, columnFormat(c, yField, format, v, r, o.unit ? String(o.unit) : undefined))
                          : c === "name" && /^\d{2,8}$/.test(String(r.code ?? ""))
                            ? itemLabel(String(r.code), String(v ?? ""), showCodes)
                            : String(v ?? "")}
                      </td>
                    );
                  })}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      {panel.text && (
        <span style={{ fontSize: 13, color: TOKENS.muted, lineHeight: 1.45 }}>
          <FactText template={panel.text} facts={facts} />
        </span>
      )}
    </div>
  );
};
