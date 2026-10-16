// One panel of the view spec, with the marks drawn on it. Every panel sits in
// its own error boundary, so one bad panel or mark never takes the page down:
// an unknown panel type or mark kind shows a grey "unsupported" card, a 400
// from /insights shows its message and URL, and loaded:false shows "not loaded".

import React from "react";
import { BarPanel } from "./BarPanel";
import { BumpPanel } from "./BumpPanel";
import { ButterflyPanel } from "./ButterflyPanel";
import { ChoroplethPanel } from "./ChoroplethPanel";
import { ComparePanel } from "./ComparePanel";
import { FlowMapPanel } from "./FlowMapPanel";
import { HeatmapPanel } from "./HeatmapPanel";
import { ScatterPanel } from "./ScatterPanel";
import { TreemapPanel } from "./TreemapPanel";
import { WaterfallPanel } from "./WaterfallPanel";
import { AboutTag, ErrorCard, NumberCircle, PanelHeading, UnsupportedCard, offPeriodOf } from "./common";
import { KpiPanelGuarded } from "./KpiPanel";
import { LinePanel } from "./LinePanel";
import { MARK_KINDS, PanelNotes, drawnMarks, marksOn, storylines, supportsMark } from "./marks";
import { PanelControls } from "./PanelControls";
import { PanelMadeCard } from "./MadeCard";
import { namedTitle } from "./personas";
import { PanelModelCard } from "./ModelCard";
import { SankeyPanel } from "./SankeyPanel";
import { TablePanel } from "./TablePanel";
import { TextPanel } from "./TextPanel";
import { FONT_UI, TOKENS, isAgentOwner, penFor } from "./tokens";
import { PanelContextScope, usePageContext, useViewStore } from "./ViewStore";
import { ChartContextMenu } from "./ChartContextMenu";
import type { Panel as PanelSpec } from "./types";

export const PANEL_TYPES = [
  "kpi",
  "bar",
  "line",
  "table",
  "flow",
  "text",
  // Contract 1.1.
  "waterfall",
  "choropleth",
  "flowmap",
  "heatmap",
  "treemap",
  "butterfly",
  "bump",
  "scatter",
  // Contract 1.2 (the comparisons workstream's ComparePanel).
  "compare",
];

class PanelBoundary extends React.Component<{ panelId: string; resetKey: string; children?: React.ReactNode }, { error: Error | null }> {
  state = { error: null as Error | null };

  static getDerivedStateFromError(error: Error) {
    return { error };
  }

  componentDidCatch(error: Error) {
    // eslint-disable-next-line no-console
    console.error(`Panel ${this.props.panelId} failed to render`, error);
  }

  componentDidUpdate(prev: { resetKey: string }) {
    if (prev.resetKey !== this.props.resetKey && this.state.error) this.setState({ error: null });
  }

  render() {
    if (this.state.error) {
      return <ErrorCard title={`The panel "${this.props.panelId}" could not be drawn`} message={this.state.error.message} />;
    }
    return this.props.children;
  }
}

export interface PanelProps {
  id: string;
  /** Show the compact notes of the marks on this panel under it (default true). */
  notes?: boolean;
  /** Show the title and subtitle (default true; kpi and the headline never show one). */
  heading?: boolean;
  /** A display title in place of the spec's (a persona's wording); the spec is not changed. */
  title?: string;
  /** kpi: where the figure sits. */
  align?: "start" | "center" | "end";
  /** text: headline or body (default: headline for the panel with id "headline"). */
  variant?: "headline" | "body";
  /** line: chart height in px. */
  height?: number;
  /** Show the pin (and, on an agent's panel, Remove) in the header (default true; never on a kpi). */
  controls?: boolean;
  className?: string;
  style?: React.CSSProperties;
}

const PanelBody: React.FC<PanelProps & { panel: PanelSpec }> = ({ panel, notes = true, heading = true, title, align, variant, height, className, style, controls = true }) => {
  const { spec, agents } = useViewStore();
  const ctx = usePageContext();
  const sl = React.useMemo(() => storylines(spec?.marks || []), [spec]);
  const onPanel = marksOn(spec, panel.id);
  const known = onPanel.filter((m) => MARK_KINDS.indexOf(String(m.kind)) >= 0 && supportsMark(String(panel.type), String(m.kind)));
  const unknown = onPanel.filter((m) => known.indexOf(m) < 0);
  const drawn = drawnMarks(known, sl, agents);

  if (PANEL_TYPES.indexOf(String(panel.type)) < 0) {
    return (
      <section className={className} style={style} aria-label={panel.title}>
        <UnsupportedCard what={`panel type "${panel.type}"`} detail={`${panel.title} (${panel.id})`} />
      </section>
    );
  }

  const byAgent = isAgentOwner(panel.agent) && panel.type !== "text";
  const pen = penFor(panel.agent, agents);
  // Every chart an agent pinned to another period than the page shows says so, whatever its type and
  // wherever the page places it (a text panel puts its tag after its sentence).
  const about = byAgent ? offPeriodOf(panel.queries, panel.facts as any, ctx.period) : undefined;
  const addedBy = byAgent ? (
    <span style={{ display: "flex", flexWrap: "wrap", alignItems: "center", gap: "4px 10px" }}>
      <span style={{ display: "inline-flex", alignItems: "center", gap: 6, fontSize: 13, fontWeight: 600, color: pen.ink }}>
        <span style={{ width: 10, height: 10, borderRadius: 3, background: pen.color }} />
        Drawn by the {pen.title.toLowerCase()}
      </span>
      {about && <AboutTag period={about} />}
    </span>
  ) : null;

  let body: React.ReactNode;
  switch (panel.type) {
    case "kpi":
      body = <KpiPanelGuarded panel={panel} align={align} />;
      break;
    case "text":
      body = <TextPanel panel={panel} variant={variant} agents={agents} allMarks={spec?.marks} storylines={sl} />;
      break;
    case "flow":
      body = <SankeyPanel panel={panel} marks={drawn} />;
      break;
    case "line":
      body = <LinePanel panel={panel} marks={drawn} height={height} />;
      break;
    case "bar":
      body = <BarPanel panel={panel} marks={drawn} />;
      break;
    case "table":
      body = <TablePanel panel={panel} marks={drawn} />;
      break;
    case "waterfall":
      body = <WaterfallPanel panel={panel} marks={drawn} />;
      break;
    case "choropleth":
      body = <ChoroplethPanel panel={panel} marks={drawn} />;
      break;
    case "flowmap":
      body = <FlowMapPanel panel={panel} marks={drawn} />;
      break;
    case "heatmap":
      body = <HeatmapPanel panel={panel} marks={drawn} />;
      break;
    case "treemap":
      body = <TreemapPanel panel={panel} marks={drawn} />;
      break;
    case "butterfly":
      body = <ButterflyPanel panel={panel} marks={drawn} />;
      break;
    case "bump":
      body = <BumpPanel panel={panel} marks={drawn} />;
      break;
    case "scatter":
      body = <ScatterPanel panel={panel} marks={drawn} />;
      break;
    case "compare":
      body = <ComparePanel panel={panel} marks={drawn} height={height} />;
      break;
  }

  const showHeading = heading && panel.type !== "kpi" && panel.type !== "text";
  const level = panel.type === "line" || panel.type === "flow" ? 2 : 3;
  // Keep (pin) and Remove: in the heading of a chart; under a text; a page that draws the heading
  // itself (heading={false}) puts <PanelControls> in its own heading. Never on the three figures.
  const ctl = controls && panel.type !== "kpi" ? <PanelControls panel={panel} noun={panel.type === "text" ? "text" : "chart"} /> : null;

  return (
    <section
      className={className}
      aria-label={panel.title}
      data-panel={panel.id}
      style={{ display: "flex", flexDirection: "column", gap: 12, minWidth: 0, fontFamily: FONT_UI, color: TOKENS.ink, ...style }}
    >
      {showHeading && (
        <PanelHeading title={namedTitle(title || panel.title, ctx)} subtitle={panel.subtitle} level={level} aside={ctl} meta={controls ? <ChartContextMenu panels={[panel]} /> : undefined} />
      )}
      {addedBy}
      {body}
      {panel.type === "text" && ctl && <div className="cx-pctl-row">{ctl}</div>}
      <PanelMadeCard panel={panel} />
      <PanelModelCard panel={panel} />
      {notes && <PanelNotes drawn={drawn} />}
      {unknown.map((m) => (
        <div key={m.id} style={{ display: "flex", gap: 8, alignItems: "flex-start" }}>
          <NumberCircle n={sl.numberOf[m.id] ?? m.n} color={penFor(m.agent, agents).color} />
          <UnsupportedCard
            what={MARK_KINDS.indexOf(String(m.kind)) < 0 ? `mark kind "${m.kind}"` : `${m.kind} mark on a ${panel.type} panel`}
            detail={`Mark ${m.id} by ${penFor(m.agent, agents).title.toLowerCase()}`}
          />
        </div>
      ))}
    </section>
  );
};

/** One panel of the spec on screen, by id. Renders nothing when the spec has no such panel. */
export const Panel: React.FC<PanelProps> = (props) => {
  const { spec } = useViewStore();
  const panel = spec?.panels.find((p) => p.id === props.id);
  if (!panel) return null;
  return (
    <PanelBoundary panelId={props.id} resetKey={`${spec?.version}`}>
      {/* inside, usePageContext() is the panel's: the page's with its own year and goods (contract 1.5) */}
      <PanelContextScope panel={panel}>
        <PanelBody {...props} panel={panel} />
      </PanelContextScope>
    </PanelBoundary>
  );
};

/** Ids of the panels in the spec, in layout order. */
export function usePanelIds(): string[] {
  const { spec } = useViewStore();
  return React.useMemo(() => (spec ? spec.layout.rows.reduce((acc: string[], r) => acc.concat(r.panels), []) : []), [spec]);
}
