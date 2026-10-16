import { APP_NAME } from "./branding";

// The copy of the reader-select screen: for each persona profile (persona_profiles.json) who it is for,
// what to call what they need the data for, and which questions each of the four agents could answer for
// them. Only things the page and its agents really do (see the profile's panels and suggested questions).
// Presentation only: a profile still decides the panels, the depth and the questions; nothing here touches
// a figure.

export type AgentKey = "trend_finder" | "cause_finder" | "forecaster" | "visualizer";

export interface PersonaCard {
  /** Who it is for, in a few words. */
  role: string;
  /** What this reader needs the trade data for, to finish "Need the figures for ...". */
  need: string;
  /** Questions each agent could answer for this reader (two each). */
  ask: Record<AgentKey, string[]>;
  accent: string;
  /** Two-letter mark drawn in the badge. */
  mark: string;
}

export const CARDS: Record<string, PersonaCard> = {
  statec_analyst: {
    role: "Official statistics",
    need: "a figure you have to reconcile and quote",
    ask: {
      trend_finder: ["Which CN8 lines explain the 2025 rise in exports?", "When did monthly exports shift for good?"],
      cause_finder: ["Do the 2025 US tariffs show in exports?", "Which events match the breaks in monthly exports?"],
      forecaster: ["Which forecast method backtests best?", "Forecast 2026 monthly exports, with its error"],
      visualizer: ["Show monthly exports to the United States since 2023", "Where is Luxembourg specialised against EU peers?"],
    },
    accent: "#0E8FD0",
    mark: "SA",
  },
  policy_adviser: {
    role: "Ministries and public bodies",
    need: "your next briefing note",
    ask: {
      trend_finder: ["What changed in our exports to the US since the tariffs?", "How exposed are we to Belgium, Germany and France?"],
      cause_finder: ["Did the 2025 US steel tariff cut our steel exports?", "What explains the 2020 drop in trade?"],
      forecaster: ["What to expect for our exports to the US over six months?", "What do exports look like for 2026, and how uncertain?"],
      visualizer: ["Show monthly exports to the United States since 2023", "Compare us with Belgium, the Netherlands and Germany"],
    },
    accent: "#2F6B9A",
    mark: "PA",
  },
  journalist: {
    role: "Media",
    need: "your next story",
    ask: {
      trend_finder: ["Why did exports jump in 2025?", "Are we exporting less to the US?"],
      cause_finder: ["What did COVID-19 do to trade?", "Did the US tariffs show up in our exports?"],
      forecaster: ["Will the 2025 export jump last?", "Where are exports heading next year?"],
      visualizer: ["Show monthly exports to the United States since 2023", "Where do our goods go?"],
    },
    accent: "#C2410C",
    mark: "JO",
  },
  exporter: {
    role: "Trade and industry",
    need: "your next export market",
    ask: {
      trend_finder: ["Which markets for our plastics grew most in 2025?", "Which of our exports to the US fell after the tariffs?"],
      cause_finder: ["Did the US steel tariff hurt our steel exports?", "Which tariffs or agreements changed our sales to the US?"],
      forecaster: ["Will our exports to the US recover this year?", "How will exports to Germany develop next year?"],
      visualizer: ["Show monthly exports to the United States since 2023", "Rank the markets for our steel by growth, on a map"],
    },
    accent: "#0F8A6B",
    mark: "BO",
  },
  researcher: {
    role: "Academia and think tanks",
    need: "your analysis",
    ask: {
      trend_finder: ["Is there a seasonal pattern in exports by chapter?", "How did partner ranks change since 1999?"],
      cause_finder: ["Which breaks in monthly exports match a dated event?", "Which events line up with the trade balance?"],
      forecaster: ["Which forecast model backtests best?", "How wide is the 2026 forecast band?"],
      visualizer: ["Show monthly exports to the United States since 2023", "Show how partner ranks changed since 1999"],
    },
    accent: "#6D4BB5",
    mark: "RE",
  },
  citizen: {
    role: "Everyone",
    need: "what Luxembourg buys and sells",
    ask: {
      trend_finder: ["What does Luxembourg sell the most?", "Do we buy more than we sell?"],
      cause_finder: ["What did COVID-19 do to trade?", "Did the US tariffs affect us?"],
      forecaster: ["Will Luxembourg sell more next year?", "What will we sell to the US this year?"],
      visualizer: ["Show monthly exports to the United States since 2023", "Who are our biggest customers?"],
    },
    accent: "#B4234F",
    mark: "CC",
  },
};

/** The line a reader's PDF export opens with: where the figures come from and how to quote or check them. */
const EXPORT_NOTES: Record<string, string> = {
  statec_analyst:
    "Every figure comes from Eurostat Comext (DS-045409, Community concept, quasi-transit included; STATEC's national concept differs) through a fixed method. Under each chart: the method, the exact queries, and who or what drew it.",
  policy_adviser:
    "Prepared for briefing use. Figures are Eurostat Comext values in nominal euros and the latest months are provisional: check the date of the data before quoting a trend.",
  journalist:
    "To quote, cite Eurostat Comext (DS-045409) and the date of this export. Under each chart, “How this was made” says how the figure was calculated.",
  exporter:
    "A market view from Luxembourg's trade data. Check the product code behind a figure before relying on it for a decision.",
  researcher:
    `To cite: Eurostat Comext, dataset DS-045409, via ${APP_NAME}. The queries behind each chart are printed so the figures can be reproduced.`,
  citizen:
    "This shows what Luxembourg sells to and buys from other countries, in euros. Under each chart, a note says where its numbers come from.",
};

const GENERIC_EXPORT_NOTE =
  "Figures come from Eurostat Comext (DS-045409), on the Community concept: goods that only pass through Luxembourg count, so STATEC's national figures differ. Under each chart, “How this was made” says where the data was pulled from and how it was calculated.";

export function exportNoteOf(id: string | undefined): string {
  return (id && EXPORT_NOTES[id]) || GENERIC_EXPORT_NOTE;
}
