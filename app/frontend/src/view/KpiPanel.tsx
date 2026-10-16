// KPI panels: one big condensed figure (the fact named by panel.value) and a
// caption with facts. The figure is set in ink, so it never reads as disabled;
// the flow colour rides on a 6 px bar above it and on the caption: steel for
// imports, sky for exports, none when it mixes both (the balance).

import React from "react";
import { FactSpan, FactText, problemCard } from "./common";
import { keyed, useFacts } from "./facts";
import { FIGURE_STYLE, FONT_UI, TOKENS } from "./tokens";
import { firstProblem, useQueries } from "./useQuery";
import { substituteQueries, usePageContext } from "./ViewStore";
import type { Panel } from "./types";

export interface KpiPanelProps {
  panel: Panel;
  align?: "start" | "center" | "end";
  /** lg for bought and sold, md for the balance between them. Defaults by tone. */
  size?: "lg" | "md";
}

export const KpiPanel: React.FC<KpiPanelProps> = ({ panel, align = "start", size }) => {
  const ctx = usePageContext();
  const queries = React.useMemo(() => substituteQueries(panel.queries, ctx), [panel.queries, ctx]);
  const states = useQueries(queries);
  const facts = useFacts(panel.facts, keyed(queries, states));
  const tones = panel.queries.map((q) => q.tone || "neutral").filter((t, i, a) => a.indexOf(t) === i);
  const tone = tones.length === 1 ? tones[0] : "neutral";
  const bar = tone === "imports" ? TOKENS.steel : tone === "exports" ? TOKENS.sky : undefined;
  const captionColor = tone === "exports" ? TOKENS.skyText : tone === "imports" ? TOKENS.steelText : TOKENS.muted;
  const big = (size || (tone === "neutral" ? "md" : "lg")) === "lg";
  const name = panel.value || "";
  const value = facts.values[name];
  const alignItems = align === "center" ? "center" : align === "end" ? "flex-end" : "flex-start";

  return (
    <div
      role="group"
      aria-label={panel.title}
      data-tone={tone}
      style={{ display: "flex", flexDirection: "column", gap: 6, alignItems, textAlign: align === "start" ? "left" : align === "end" ? "right" : "center", minWidth: 0 }}
    >
      {bar && <span aria-hidden="true" style={{ display: "block", width: 56, height: 6, borderRadius: 1, background: bar, marginBottom: 4 }} />}
      <span
        style={{
          ...FIGURE_STYLE,
          fontSize: big ? "clamp(48px, 6.4vw, 92px)" : "clamp(28px, 3vw, 44px)",
          letterSpacing: big ? "-0.02em" : 0,
          color: TOKENS.ink,
        }}
      >
        {value?.status === "ok" ? value.text : <FactSpan name={name} value={value} text="" />}
      </span>
      <span style={{ fontFamily: FONT_UI, fontSize: 15, fontWeight: bar ? 600 : 400, color: captionColor }}>
        {panel.text ? <FactText template={panel.text} facts={facts} /> : panel.title}
      </span>
    </div>
  );
};

/** KPI with its error or not-loaded card in place of the figure. */
export const KpiPanelGuarded: React.FC<KpiPanelProps> = (props) => {
  const ctx = usePageContext();
  const queries = React.useMemo(() => substituteQueries(props.panel.queries, ctx), [props.panel.queries, ctx]);
  const states = useQueries(queries);
  const problem = firstProblem(states);
  if (problem && problem.kind !== "loading") return problemCard(problem);
  return <KpiPanel {...props} />;
};
