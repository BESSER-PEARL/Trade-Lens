// SPDX-License-Identifier: MIT
import React from "react";
import type { TrendAnalysis } from "../api";
import { money, periodLabel, signedPercent } from "../format";
import { TOKENS, flowTone } from "../styles";
import { LineChart, type ChartRule, type ChartSeries } from "./LineChart";

interface Props {
  analysis: TrendAnalysis;
  showAdjusted: boolean;
  flow?: string;
  unit?: string;
  /** What the series is, for the chart's accessible name. */
  subject: string;
}

/** The observed series in the flow's colour, the trend in ink, numbered rules at the change points. */
export const TrendChart: React.FC<Props> = ({ analysis, showAdjusted, flow, unit = "€", subject }) => {
  const data = analysis.points;
  const tone = flowTone(flow);
  const hasAdjusted = data.some((p) => p.adjusted !== null);
  const adjusted = showAdjusted && hasAdjusted;
  const periods = data.map((p) => p.period);

  const series: ChartSeries[] = [
    {
      key: "observed",
      label: adjusted ? `${tone.name}, adjusted` : tone.name,
      color: tone.fill,
      text: tone.text,
      width: 1.5,
      values: data.map((p) => (adjusted ? p.adjusted : p.value)),
    },
    {
      key: "trend",
      label: "Trend",
      color: TOKENS.ink,
      text: TOKENS.ink,
      width: 2.5,
      values: data.map((p) => p.trend),
    },
  ];

  const rules: ChartRule[] = analysis.changepoints
    .map((cp, i) => {
      const index = periods.indexOf(cp.period);
      return {
        index: index >= 0 ? index : cp.index,
        n: i + 1,
        detail: `level ${signedPercent(cp.relative_change)}, ${money(cp.before_mean, unit)} to ${money(cp.after_mean, unit)}`,
      };
    })
    .filter((r) => r.index >= 0 && r.index < periods.length);

  const last = data[data.length - 1];
  const aria =
    `${subject}, ${periodLabel(analysis.start)} to ${periodLabel(analysis.end)}. ` +
    (last ? `${series[0].label} ${money(adjusted ? last.adjusted : last.value, unit)} and trend ${money(last.trend, unit)} in ${periodLabel(last.period, true)}. ` : "") +
    (rules.length ? `${rules.length} change ${rules.length === 1 ? "point" : "points"}, marked by numbered rules.` : "No change points.");

  return <LineChart periods={periods} series={series} rules={rules} unit={unit} height={380} ariaLabel={aria} />;
};
