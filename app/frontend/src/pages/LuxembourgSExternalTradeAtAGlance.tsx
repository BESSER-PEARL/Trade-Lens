import React, { useEffect, useMemo, useRef, useState } from "react";
import { AgentComponent, type PageNotice } from "../components/AgentComponent";
import { PersonaGate, playChatHandoff } from "../components/PersonaGate";
import { Welcome, askTheData, leavePendingAsk } from "../components/Welcome";
import { PromptGroup } from "../components/PromptGroup";
import { ExportPdfButton, PrintHead, PrintSource } from "../components/ExportPdf";
import { DynamicCanvas } from "../view/DynamicCanvas";
import { AboutTag, FactText, PanelHeading } from "../view/common";
import { useFacts } from "../view/facts";
import { Panel } from "../view/Panel";
import { ChartContextMenu, useGoodsName } from "../view/ChartContextMenu";
import { EVERYONE, PERSONAS, foldedPanels, personaSwitchOps, personaTitle, profileOf, aReader, readerName, readingStyle, switchMessage } from "../view/personas";
import { PanelContextScope, placeholdersOf, rangeStart, usePageContext, useViewStore, withPanelContext } from "../view/ViewStore";
import { apiHref } from "../view/api";
import { useNewPanels, usePromptGroups } from "../view/panelPrompts";
import { clearMine, forgetThisBrowser, isFullOverview, showFullOverview } from "../view/ownership";
import { isAgentOwner } from "../view/tokens";
import { CARDS } from "../view/personaCards";
import { APP_NAME } from "../view/branding";
import { PanelControls } from "../view/PanelControls";
import { MadeToggle, PanelsMadeCard } from "../view/MadeCard";
import type { Facts, LayoutRow, Panel as PanelSpec, ViewResponse, ViewSpec } from "../view/types";

// The Crossroads page. Every panel is drawn from the view spec the backend
// holds (app/backend/view/baseline.json is version 0); the agents change it
// through POST /view/ops and the page only renders it.
//
// The page places its own (baseline) panels in four fixed sections: the
// headline, the three figures over the crossroads, the trend, and the quiet
// row of three. A panel an agent adds goes where spec.layout puts it: after
// the section of the last baseline panel before it in the layout (so
// upsert_panel.after and set_layout are honoured). Only a panel with no layout
// slot lands in "Added by the agents" at the end.
//
// The header holds only "Reading as" and the page actions (Undo, Redo, Clear
// the page, Back to the starting page). Each chart has its own quiet Year (or
// From/To) and Goods menu (contract 1.5, ChartContextMenu): it sends
// set_panel_context as agent "user", so only that chart moves. An agent's
// set_context ("Show me 2020") moves every chart that has no year of its own.
//
// "Reading as" (contract 1.4) draws the page for one reader: set_context
// {persona} with the profile's default context, and the persona's own panels,
// in one batch as agent "user". The baseline panels that reader does not need
// fold under "More" (never deleted, never while they carry marks); the numbers
// do not change.

/** The page's own sections, in order, and the baseline panels in each. */
const SECTIONS: Array<{ key: string; ids: string[] }> = [
  { key: "header", ids: ["headline"] },
  { key: "crossroads", ids: ["kpi_imports", "kpi_balance", "kpi_exports", "crossroads"] },
  { key: "trend", ids: ["trend"] },
  { key: "three", ids: ["mix_export", "specialisation", "peers"] },
];
const BASELINE = new Set(SECTIONS.reduce((acc: string[], s) => acc.concat(s.ids), []));
const SECTION_OF: Record<string, number> = {};
SECTIONS.forEach((s, i) => s.ids.forEach((id) => (SECTION_OF[id] = i)));

const DEFAULT_TITLE = "Luxembourg's trade in goods";
const FIGURES = ["kpi_imports", "kpi_balance", "kpi_exports"];

/**
 * Where each agent panel goes: the layout rows of agent panels after each
 * section (index 0..3), and the panels that have no layout slot at all.
 */
function placeAgentRows(spec: ViewSpec | null): { after: LayoutRow[][]; orphans: string[] } {
  const after: LayoutRow[][] = SECTIONS.map(() => []);
  if (!spec) return { after, orphans: [] };
  const placed = new Set<string>();
  let anchor = 0;
  spec.layout.rows.forEach((row) => {
    const own = row.panels.filter((id) => BASELINE.has(id));
    own.forEach((id) => (anchor = Math.max(anchor, SECTION_OF[id])));
    const theirs = row.panels.filter((id) => !BASELINE.has(id));
    if (theirs.length) {
      after[anchor].push({ ...row, panels: theirs });
      theirs.forEach((id) => placed.add(id));
    }
  });
  const orphans = spec.panels.filter((p) => !BASELINE.has(p.id) && !placed.has(p.id)).map((p) => p.id);
  return { after, orphans };
}

function failure(r: ViewResponse): string {
  if (r.status === 0) return "The page service is not reachable, so the page did not change.";
  if (r.status === 409) return "The page changed meanwhile. Nothing was applied; try again.";
  return r.errors?.[0]?.message || r.detail || `The page refused the change (${r.status}).`;
}

// The seasonal note under the trend over time: the weakest and strongest month of the seasonal component
// of monthly exports, as the trend analysis finds them (the same /trends/analyze call as the trend
// finder's, so the Trends tab and the page agree). Said only when the yearly pattern is not weak.
const SEASON_QUERY = { flow: "EXPORT", freq: "M", product: "$product", penalty: 1.5, min_size: 12 };
const SEASON_FACTS = {
  trough: { endpoint: "/trends/analyze", params: SEASON_QUERY, path: "seasonal_trough.position", format: "month" },
  peak: { endpoint: "/trends/analyze", params: SEASON_QUERY, path: "seasonal_peak.position", format: "month" },
  strength: { endpoint: "/trends/analyze", params: SEASON_QUERY, path: "seasonality_label", format: "text" },
} as unknown as Facts;
const NO_STATES = {};

/** Resolves once "Ask the data" shows the reader (its "Answers for" line), or after 1.5 s. */
function panelReadsFor(label: string | undefined): Promise<void> {
  const started = Date.now();
  return new Promise((resolve) => {
    const check = () => {
      const shown = document.querySelector("aside.ask .ask-for strong")?.textContent?.trim();
      if ((label ? shown === label : !shown) || Date.now() - started > 1500) return resolve();
      window.setTimeout(check, 50);
    };
    check();
  });
}

const SeasonNote: React.FC<{ goods?: string }> = ({ goods }) => {
  const facts = useFacts(SEASON_FACTS, NO_STATES);
  const v = facts.values;
  if (v.trough?.status !== "ok" || v.peak?.status !== "ok" || v.strength?.value === "weak" || v.strength?.value == null) return null;
  const of = goods ? ` of ${goods}` : "";
  return (
    <p className="cx-quiet cx-season" data-facts="season">
      <FactText template={`Weakest month {trough}, strongest {peak}, from the seasonal adjustment of monthly exports${of}.`} facts={facts} />
    </p>
  );
};

/** Whether the page's history can be undone or redone (GET /view/history), read again at every version. */
function useHistoryFlags(version: number, fallback: boolean): { canUndo: boolean; canRedo: boolean } {
  const [flags, setFlags] = useState({ canUndo: false, canRedo: false });
  useEffect(() => {
    if (fallback || version < 0) return;
    let gone = false;
    fetch(apiHref("/view/history"), { headers: { Accept: "application/json" } })
      .then((r) => (r.ok ? r.json() : null))
      .then((b) => {
        if (!gone && b) setFlags({ canUndo: !!b.can_undo, canRedo: !!b.can_redo });
      })
      .catch(() => undefined);
    return () => {
      gone = true;
    };
  }, [version, fallback]);
  return flags;
}

/**
 * Undo and Redo of the page's last change, "Clear my charts" with an in-page confirm step, and the switch
 * between "my dashboard" (the overview chart and what this reader's questions drew) and the full overview
 * (the base page as well). Clear and the switch are this session's own view: the page on the backend is
 * shared and is not touched by them; Undo and Redo do act on it (every change is a version). In the
 * confirm step Cancel has the focus, Escape cancels, and the focus goes back to the button that asked.
 */
const PageActions: React.FC<{
  disabled: boolean;
  overview: boolean;
  hasMine: boolean;
  canUndo: boolean;
  canRedo: boolean;
  onUndo: () => Promise<void>;
  onRedo: () => Promise<void>;
  onClear: () => void;
  onToggle: () => void;
}> = ({ disabled, overview, hasMine, canUndo, canRedo, onUndo, onRedo, onClear, onToggle }) => {
  const [confirm, setConfirm] = useState(false);
  const [returnTo, setReturnTo] = useState(false);
  const clearBtn = useRef<HTMLButtonElement | null>(null);
  useEffect(() => {
    if (confirm || !returnTo) return;
    clearBtn.current?.focus();
    setReturnTo(false);
  }, [confirm, returnTo]);
  const cancel = () => {
    setReturnTo(true);
    setConfirm(false);
  };
  if (confirm) {
    return (
      <div
        className="cx-pageactions"
        role="group"
        aria-label="Clear my charts"
        onKeyDown={(e) => {
          if (e.key === "Escape") {
            e.stopPropagation();
            cancel();
          }
        }}
      >
        <span className="cx-pageactions-ask">Remove your charts and notes that are not kept?</span>
        <button
          type="button"
          className="cx-pctl-btn cx-pctl-btn--danger"
          disabled={disabled}
          onClick={() => {
            setConfirm(false);
            onClear();
          }}
        >
          Clear my charts
        </button>
        <button type="button" className="cx-pctl-btn" autoFocus onClick={cancel}>
          Cancel
        </button>
      </div>
    );
  }
  return (
    <div className="cx-pageactions">
      <button type="button" className="cx-pctl-btn" disabled={disabled || !canUndo} onClick={onUndo} title="Undo the last change to the page">
        Undo
      </button>
      <button type="button" className="cx-pctl-btn" disabled={disabled || !canRedo} onClick={onRedo} title="Make the change just undone again">
        Redo
      </button>
      {hasMine && (
        <button ref={clearBtn} type="button" className="cx-pctl-btn" disabled={disabled} onClick={() => setConfirm(true)} title="Take your charts off the page; charts you kept stay">
          Clear my charts
        </button>
      )}
      <button
        type="button"
        className="cx-pctl-btn"
        disabled={disabled}
        onClick={onToggle}
        title={overview ? "Only the overview and your own charts" : "The figures, the flow map, products and peers as well"}
      >
        {overview ? "Back to my dashboard" : "Show the full overview"}
      </button>
    </div>
  );
};

// `skip`: the charts drawn in their question's group instead.
const AgentRows: React.FC<{ rows: LayoutRow[]; skip: Set<string> }> = ({ rows, skip }) =>
  rows.length ? <DynamicCanvas rows={rows} filter={(id) => !BASELINE.has(id) && !skip.has(id)} gap={44} /> : null;

const LuxembourgSExternalTradeAtAGlance: React.FC = () => {
  const { spec, version, send, undo, redo, reset, fallback } = useViewStore();
  const ctx = usePageContext();
  const history = useHistoryFlags(version, fallback);
  // A chart an agent adds: the page scrolls to it and files the question that drew it.
  useNewPanels(spec, fallback);
  // The charts of one question are drawn together, under it.
  const { groups, prompted } = usePromptGroups((spec?.panels || []).filter((p) => isAgentOwner(p.agent)).map((p) => p.id));
  const [busy, setBusy] = useState(false);
  // The reader-select screen opens once per browser session, and again from "Change reader".
  const [gate, setGate] = useState(() => sessionStorage.getItem("lux.reader.chosen") !== "1");
  // The landing's card flew to the chat before the reload: once the page is drawn, it dissolves into the panel.
  const handedOver = useRef(false);
  useEffect(() => {
    if (gate || handedOver.current) return;
    handedOver.current = true;
    requestAnimationFrame(() => requestAnimationFrame(playChatHandoff));
  }, [gate]);
  const [notice, setNotice] = useState<PageNotice | null>(null);
  // A passing confirmation ("The page shows 2025.") gives way after a few seconds: an agent may move
  // the page next, and the line must not keep saying where the controls last put it.
  useEffect(() => {
    if (!notice?.quiet || busy) return;
    const t = setTimeout(() => setNotice((n) => (n === notice ? null : n)), 6000);
    return () => clearTimeout(t);
  }, [notice, busy]);

  const year = ctx.period.slice(0, 4);

  const profile = profileOf(ctx.persona);
  const folded = useMemo(() => foldedPanels(profile, spec), [profile, spec]);
  const inSpec = (id: string) => !!spec?.panels.some((p) => p.id === id);
  // The page's own sections draw what the reader has open; the folded panels go under "More".
  const has = (id: string) => inSpec(id) && folded.indexOf(id) < 0;
  const titleOf = (id: string) => personaTitle(profile, panelOf(id));
  const reading = readingStyle(profile);
  const panelOf = (id: string): PanelSpec | undefined => spec?.panels.find((p) => p.id === id);
  const keptOn = (id: string) => !!spec?.marks.some((m) => m.target === id && m.status === "kept");
  const { after, orphans: allOrphans } = useMemo(() => placeAgentRows(spec), [spec]);
  const orphans = allOrphans.filter((id) => !prompted.has(id));

  const chooseReader = async (to: string, question?: string, fly?: () => Promise<void>) => {
    if (!spec || busy) return;
    // A question picked on the reader screen is asked in the chat once the reader's page is up. On a first
    // landing the page reloads first, so the question waits in the session until then (and only then).
    // A reader picked on the landing starts a fresh session: the starting page drawn for that reader, and none of
    // the charts or questions of an earlier visit in this browser. So does another reader picked from "Change reader":
    // each reader gets their own notebook (a fresh start); the same reader keeps the page.
    let landing = false;
    try {
      landing = sessionStorage.getItem("lux.reader.chosen") !== "1";
    } catch {
      landing = false;
    }
    // Starting with a question (picked or typed) always opens a new notebook and a new conversation, also from
    // "Change reader".
    const another = to !== (ctx.persona || EVERYONE);
    if ((landing || question || another) && !fallback) {
      setBusy(true);
      const r = await reset();
      if (r.ok && r.spec && profileOf(to)) await send(personaSwitchOps(r.spec, to), { message: switchMessage(to), baseVersion: null });
      if (fly) {
        // the chat under the landing first reads for the new reader, so the card that flies to it is its exact copy
        await panelReadsFor(profileOf(to)?.label);
        await fly();
      }
      forgetThisBrowser();
      try {
        sessionStorage.setItem("lux.reader.chosen", "1");
      } catch {
        /* blocked storage: the landing shows again after the reload */
      }
      if (question) leavePendingAsk(question);
      window.location.reload();
      return;
    }
    if (to !== (ctx.persona || EVERYONE)) await changePersona(to);
    sessionStorage.setItem("lux.reader.chosen", "1");
    setGate(false);
    if (question) askTheData(question);
  };

  const changePersona = async (to: string) => {
    if (!spec || busy || !to || to === (ctx.persona || EVERYONE)) return;
    const label = profileOf(to)?.label || "every reader";
    setBusy(true);
    setNotice({ text: `Drawing the page for ${profileOf(to) ? aReader(label) : label}.`, quiet: true });
    const r = await send(personaSwitchOps(spec, to), { message: switchMessage(to), baseVersion: null });
    setBusy(false);
    setNotice(r.ok ? { text: profileOf(to) ? `Reading as ${aReader(label)}. The figures are the same for every reader.` : "The standard page. The figures are the same for every reader.", quiet: true } : { text: failure(r), error: true });
  };

  // Clearing and the overview switch are this session's own view: the page on the backend is shared and stays as it is.
  const clearPage = () => {
    clearMine(spec);
    setNotice({ text: "Your charts are cleared.", quiet: true });
  };

  const undoLast = async () => {
    if (!spec || busy) return;
    setBusy(true);
    const r = await undo();
    setBusy(false);
    setNotice(r.ok ? { text: "The last change is undone.", quiet: true } : { text: r.status === 409 ? "There is nothing to undo." : failure(r), error: true });
  };

  const redoLast = async () => {
    if (!spec || busy) return;
    setBusy(true);
    const r = await redo();
    setBusy(false);
    setNotice(r.ok ? { text: "The change is made again.", quiet: true } : { text: r.status === 409 ? "There is nothing to redo." : failure(r), error: true });
  };

  const toggleOverview = () => showFullOverview(!overview);

  const removeFigures = async () => {
    if (!spec || busy) return;
    // As Clear: a figure that is pinned, or carries a kept note, stays.
    const ids = FIGURES.filter((id) => inSpec(id) && !panelOf(id)?.pinned && !keptOn(id));
    if (!ids.length) return;
    setBusy(true);
    const r = await send(ids.map((id) => ({ op: "remove_panel", id, force: true })), { message: "Removed the three figures", baseVersion: null });
    setBusy(false);
    if (!r.ok) setNotice({ text: failure(r), error: true });
  };

  // The trend's heading names its goods when it shows one chapter (its own, or the page's), unless an agent rewrote it.
  const trend = panelOf("trend");
  const trendCtx = withPanelContext(ctx, trend);
  const trendGoods = useGoodsName(trendCtx.product, year);
  const trendTitle = trend && !isAgentOwner(trend.agent) && trendCtx.product !== "TOTAL" ? `Imports and exports of ${trendGoods} since 1999` : titleOf("trend");
  const crossroads = panelOf("crossroads");
  // The crossroads' own context (contract 1.5), else the page's: the year (or From/To range) and the goods.
  const crossCtx = withPanelContext(ctx, crossroads);
  const crossGoods = useGoodsName(crossCtx.product, year);
  const crossFrom = rangeStart(crossCtx);
  // The page's own crossroads says what it sums over: the goods, and the years when From is before To.
  const ranged = !!crossroads && crossFrom < crossCtx.period && placeholdersOf(crossroads).has("$start");
  const crossSubtitle =
    crossroads && !isAgentOwner(crossroads.agent)
      ? [crossroads.subtitle, crossCtx.product !== "TOTAL" ? `${crossGoods} only` : "", ranged ? `total from ${crossFrom} to ${crossCtx.period}` : ""].filter(Boolean).join(", ")
      : crossroads?.subtitle;
  const figures = FIGURES.filter(has).map((id) => panelOf(id)!).filter(Boolean);
  const controlsOff = !spec || busy || fallback;
  // "My dashboard" (the default) or the full base page, per session.
  const overview = isFullOverview();
  // The full overview with nothing on it (the page was emptied) says so and offers the switch back.
  const empty = !!spec && overview && spec.panels.length === 0;
  // Clear removes this reader's charts and notes that are not kept: nothing to clear when there are none.
  const hasMine = !!spec && (spec.panels.some((p) => isAgentOwner(p.agent) && !p.pinned && !keptOn(p.id)) || spec.marks.some((m) => m.status !== "kept"));
  // Nothing of the reader's own on the page yet: it opens on an invitation to ask, and nothing else.
  const welcome = !!spec && !fallback && !overview && !spec.panels.some((p) => isAgentOwner(p.agent)) && spec.marks.length === 0;
  const starters = (profile?.suggested_questions || ["Why did exports jump in 2025?", "Where do the goods go?", "When did exports shift for good since 1999?"]).slice(0, 3);

  // A title an agent wrote about one year ("where the 2025 export rise came from") is dimmed with an
  // "about 2025" tag while the page shows another year, like the marks.
  const title = spec?.title || DEFAULT_TITLE;
  const titleYears: string[] = title === DEFAULT_TITLE ? [] : Array.from(title.match(/\b(?:19|20)\d{2}\b/g) || []);
  const titleAbout = titleYears.length && titleYears.indexOf(year) < 0 ? titleYears[titleYears.length - 1] : undefined;

  return (
    <div
      id="Overview"
      className="cx-page"
      data-persona={profile?.id}
      data-contrast={reading.contrast}
      data-hide={(profile?.hide || []).join(" ") || undefined}
      style={{ ["--reading-zoom" as any]: reading.zoom }}
    >
      {gate && spec && (
        <PersonaGate
          current={ctx.persona || EVERYONE}
          busy={busy}
          onChoose={chooseReader}
          onClose={sessionStorage.getItem("lux.reader.chosen") === "1" ? () => setGate(false) : undefined}
        />
      )}
      <div className="cx-wrap">
        <main className="cx-main">
          <PrintHead reader={profile ? aReader(profile.label) : undefined} readerId={profile?.id} />
          <header className="cx-header">
            <button
              type="button"
              className="cx-brand"
              onClick={() => setGate(true)}
              disabled={controlsOff}
              aria-label={`${APP_NAME} homepage`}
              title="Back to the homepage"
            >
              {APP_NAME}
            </button>
            <div className="cx-titlebar">
              {/* The brand above names the page: the title is kept for screen readers, not shown. */}
              <h1 className="cx-title sr-only">
                <span style={{ opacity: titleAbout ? 0.5 : 1 }}>{title}</span>
                {titleAbout && <AboutTag period={titleAbout} style={{ marginLeft: 10, verticalAlign: "middle", fontStretch: "100%" }} />}
              </h1>
              {/* Who the page is read for: one button, in the reader's colour, that opens "Who is reading today?". */}
              <button
                type="button"
                className="cx-reader"
                style={{ ["--accent" as any]: (profile && CARDS[profile.id]?.accent) || "var(--sky)" }}
                disabled={controlsOff}
                onClick={() => setGate(true)}
                title={profile?.tagline}
              >
                <span className="cx-reader-name">{profile ? profile.label : "Every reader"}</span>
                <span className="cx-reader-change">Change reader</span>
              </button>
            </div>
            <div className="cx-toolbar" role="group" aria-label="The page">
              <PageActions
                disabled={controlsOff}
                overview={overview}
                hasMine={hasMine}
                canUndo={history.canUndo}
                canRedo={history.canRedo}
                onUndo={undoLast}
                onRedo={redoLast}
                onClear={clearPage}
                onToggle={toggleOverview}
              />
              <ExportPdfButton disabled={controlsOff} ids={[...(spec?.panels || []).map((p) => p.id), "figures"]} />
            </div>
            {!spec && <p className="cx-loading">Loading the page.</p>}
            {has("headline") && <Panel id="headline" />}
          </header>

          {empty && (
            <section className="cx-empty" aria-label="An empty page">
              <p>The page is empty. Ask a question in the Ask panel and the agents will draw here, or show the full overview.</p>
              <button type="button" className="cx-btn" disabled={controlsOff} onClick={toggleOverview}>
                Show the full overview
              </button>
            </section>
          )}

          {welcome && <Welcome starters={starters} disabled={controlsOff} />}

          {groups.length > 0 && (
            <section className="cx-groups" aria-label="Your questions and their charts">
              {groups.map((g) => (
                <PromptGroup key={g.at} group={g} />
              ))}
            </section>
          )}

          <AgentRows rows={after[0]} skip={prompted} />

          {(has("crossroads") || has("kpi_imports") || has("kpi_exports")) && (
            <section aria-label="Where goods come from and go to" className="cx-crossroads">
              {figures.length > 0 && <ChartContextMenu panels={figures} name="the three figures" className="cx-ctxmenu--figures" />}
              <div className="cx-figures">
                {has("kpi_imports") && (
                  <div>
                    <Panel id="kpi_imports" align="start" />
                  </div>
                )}
                {has("kpi_balance") && (
                  <div>
                    <Panel id="kpi_balance" align="center" />
                  </div>
                )}
                {has("kpi_exports") && (
                  <div>
                    <Panel id="kpi_exports" align="end" />
                  </div>
                )}
              </div>
              {(has("kpi_imports") || has("kpi_balance") || has("kpi_exports")) && (
                <>
                  <div className="cx-figs-foot cx-pctl">
                    <MadeToggle id="figures" label="How these were made" />
                    <button type="button" className="cx-pctl-btn" disabled={controlsOff} onClick={removeFigures} title="Remove the three figures from the page" aria-label="Remove the three figures">
                      Remove
                    </button>
                  </div>
                  <PanelsMadeCard
                    id="figures"
                    title="How these were made"
                    panels={figures}
                    calculated="The two totals, what was bought and what was sold: the large figures on each side. Their difference, sold minus bought: the balance in the middle. The number of partner countries with any trade: the captions."
                  />
                </>
              )}
              {crossroads && (
                <>
                  {(crossroads.title || crossSubtitle) && (
                    <div className="cx-cross-head">
                      <PanelHeading subtitle={crossSubtitle} aside={<PanelControls panel={crossroads} />} meta={<ChartContextMenu panels={[crossroads]} />} />
                    </div>
                  )}
                  <Panel id="crossroads" heading={false} />
                </>
              )}
            </section>
          )}

          <AgentRows rows={after[1]} skip={prompted} />

          {trend && (
            <section aria-label={trendTitle} className="cx-section">
              <PanelHeading title={trendTitle} subtitle={trend.subtitle} level={2} aside={<PanelControls panel={trend} />} meta={<ChartContextMenu panels={[trend]} name={`“${trendTitle}”`} />} />
              <Panel id="trend" heading={false} />
              {/* the season note reads the goods the trend shows (its own, contract 1.5) */}
              <PanelContextScope panel={trend}>
                <SeasonNote goods={trendCtx.product !== "TOTAL" ? trendGoods : undefined} />
              </PanelContextScope>
              <p className="cx-quiet">Before 1999 Luxembourg is reported together with Belgium (BLEU), so the series starts there.</p>
            </section>
          )}

          <AgentRows rows={after[2]} skip={prompted} />

          {(has("mix_export") || has("specialisation") || has("peers")) && (
            <section aria-label="Products and comparison" className="cx-three">
              {has("mix_export") && (
                <div>
                  <Panel id="mix_export" title={titleOf("mix_export")} />
                </div>
              )}
              {has("specialisation") && (
                <div>
                  <Panel id="specialisation" title={titleOf("specialisation")} />
                </div>
              )}
              {has("peers") && (
                <div>
                  <Panel id="peers" title={titleOf("peers")} />
                </div>
              )}
            </section>
          )}

          <AgentRows rows={after[3]} skip={prompted} />

          {folded.length > 0 && profile && (
            <details className="cx-more">
              <summary>
                More for this view: {folded.map((id) => titleOf(id) || id).join(", ")}
              </summary>
              <p className="cx-quiet">Folded for {readerName(profile.label)}, not removed: the same panels, the same figures.</p>
              <div className="cx-more-grid">
                {folded.map((id) => (
                  <div key={id}>
                    <Panel id={id} title={titleOf(id)} />
                  </div>
                ))}
              </div>
            </details>
          )}

          {orphans.length > 0 && (
            <section aria-labelledby="added-heading" className="cx-section">
              <h2 id="added-heading" className="cx-section-label">
                Added by the agents
              </h2>
              <DynamicCanvas rows={[{ panels: orphans.slice(0, 3) }, ...chunk(orphans.slice(3), 3).map((p) => ({ panels: p }))]} ruled={false} />
            </section>
          )}

          <PrintSource />

          {overview && <footer className="cx-footer">
            <p>
              Source: Eurostat ds-045409, EU trade since 1988 by HS2-4-6 and CN8, nominal euros, latest months provisional. Luxembourg
              reports on its own from 1999. The data is only ever blue and grey; any other colour was drawn by an agent. Every figure is
              computed by a fixed method from this data, never typed by the AI: “How this was made” on each chart names it.
            </p>
            <p>Built with BESSER: the data model and the first version of this page were generated from B-UML models.</p>
          </footer>}
        </main>

        <AgentComponent enabled={!gate} id="ask_the_data" agent-name="" agent-title="Ask the data" notice={notice} />
      </div>
    </div>
  );
};

function chunk<T>(xs: T[], n: number): T[][] {
  const out: T[][] = [];
  for (let i = 0; i < xs.length; i += n) out.push(xs.slice(i, i + n));
  return out;
}

export default LuxembourgSExternalTradeAtAGlance;
