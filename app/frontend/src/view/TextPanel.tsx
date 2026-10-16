// Text panels: prose written by an agent, set in Newsreader, with every figure
// from a {fact} placeholder resolved from the API.
//
// {fact} formats: eur (€25.1 bn), eur_signed, pct (a fraction), pct_signed,
// percent (already in percent), percent_signed, int, number, ordinal, period,
// text; compact and raw are accepted as aliases of eur and text.
//
// Queries follow the page context ("$period" ...). A text an agent wrote about
// one year (its queries name that year) is dimmed with an "about 2025" tag when
// the page shows another, so it is never read as a sentence about the year on
// screen.

import React from "react";
import { AboutTag, FactText, NumberCircle, PanelHeading, offPeriodOf } from "./common";
import { keyed, useFacts } from "./facts";
import { FONT_AGENT, FONT_UI, TOKENS, isAgentOwner, penFor } from "./tokens";
import { useQueries } from "./useQuery";
import {
  OFF_CONTEXT_OPACITY,
  substituteQueries,
  usePageContext,
} from "./ViewStore";
import type { AgentInfo, Mark, Panel } from "./types";
import type { Storylines } from "./marks";

export interface TextPanelProps {
  panel: Panel;
  /** headline: the large serif sentence under the page title; body: a titled section. */
  variant?: "headline" | "body";
  agents?: AgentInfo[];
  /** All marks of the spec, to link the text to the storyline its agent drew in the same run. */
  allMarks?: Mark[];
  storylines?: Storylines;
}

/** Type size and measure of the headline by its length: about 70 to 95 characters a line at 1440 px. */
const HEADLINE: Record<"short" | "medium" | "long", { fontSize: string; maxWidth: string }> = {
  short: { fontSize: "clamp(24px, 2.4vw, 31px)", maxWidth: "34em" },
  medium: { fontSize: "clamp(21px, 1.85vw, 26px)", maxWidth: "38em" },
  long: { fontSize: "clamp(19px, 1.55vw, 22px)", maxWidth: "44em" },
};

/** The headline's length as read: the template with each {fact} counted as its formatted text. */
function headlineLength(template: string | undefined, values: Record<string, { text?: string } | undefined>): number {
  return String(template || "").replace(/\{(\w+)\}/g, (_, k) => values[k]?.text || "€00.0 bn").length;
}

export const TextPanel: React.FC<TextPanelProps> = ({ panel, variant, agents, allMarks, storylines }) => {
  const ctx = usePageContext();
  const queries = React.useMemo(() => substituteQueries(panel.queries, ctx), [panel.queries, ctx]);
  const states = useQueries(queries);
  const facts = useFacts(panel.facts, keyed(queries, states));
  const kind = variant || (panel.id === "headline" ? "headline" : "body");
  const author = panel.provenance?.agent || panel.agent;
  const pen = penFor(author, agents);
  const byAgent = isAgentOwner(author);
  const about = byAgent ? offPeriodOf(panel.queries, panel.facts as any, ctx.period) : undefined;
  const off = !!about;

  // The storyline the same agent run drew, so the text can carry its number.
  let linked: number | undefined;
  if (byAgent && allMarks && storylines) {
    const run = panel.provenance?.run_id;
    const m = allMarks
      .slice()
      .sort((a, b) => a.n - b.n)
      .find((x) => (run ? x.provenance?.run_id === run : false));
    if (m) linked = storylines.numberOf[m.id];
  }


  if (kind === "headline") {
    // The headline's size follows its length: a short sentence takes the full voice, an agent's longer
    // one (~160 characters) steps down so it still reads in two or three lines; nothing is ever cut.
    const length = headlineLength(panel.text, facts.values);
    const size = length > 200 ? "long" : length > 130 ? "medium" : "short";
    return (
      <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
        <p
          className="cx-headline"
          data-size={size}
          style={{
            margin: 0,
            maxWidth: HEADLINE[size].maxWidth,
            fontFamily: FONT_AGENT,
            fontSize: HEADLINE[size].fontSize,
            lineHeight: size === "short" ? 1.3 : 1.34,
            color: TOKENS.ink,
            textWrap: "pretty" as any,
          }}
        >
          <span style={{ opacity: off ? OFF_CONTEXT_OPACITY + 0.18 : 1, transition: "opacity 200ms ease" }}>
            <FactText template={panel.text} facts={facts} />
          </span>
          {linked !== undefined && (
            <NumberCircle n={linked} color={pen.color} style={{ verticalAlign: 10, marginLeft: 6, opacity: off ? OFF_CONTEXT_OPACITY + 0.18 : 1 }} title={`Written by the ${pen.title.toLowerCase()}`} />
          )}
          {off && about && <AboutTag period={about} style={{ marginLeft: 8, verticalAlign: 6 }} />}
        </p>
      </div>
    );
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
      <PanelHeading title={panel.title} subtitle={panel.subtitle} />
      <p style={{ margin: 0, maxWidth: "40em", fontFamily: FONT_AGENT, fontSize: 19, lineHeight: 1.5, color: TOKENS.ink, textWrap: "pretty" as any }}>
        <span style={{ opacity: off ? OFF_CONTEXT_OPACITY + 0.18 : 1 }}>
          <FactText template={panel.text} facts={facts} />
        </span>
        {linked !== undefined && <NumberCircle n={linked} color={pen.color} style={{ verticalAlign: 3, marginLeft: 6 }} />}
        {off && about && <AboutTag period={about} style={{ marginLeft: 8 }} />}
      </p>
      {byAgent && (
        <div style={{ display: "flex", flexWrap: "wrap", gap: 12, alignItems: "baseline", fontFamily: FONT_UI, fontSize: 13 }}>
          <span style={{ color: pen.ink, fontWeight: 600 }}>Written by the {pen.title.toLowerCase()}</span>
        </div>
      )}
    </div>
  );
};
