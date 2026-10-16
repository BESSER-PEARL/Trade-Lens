// SPDX-License-Identifier: MIT
import React from "react";
import type { TrendFilters, TrendOptions } from "../api";
import { periodLabel, sentenceCase } from "../format";

const FREQ_LABEL: Record<string, string> = { A: "Annual", M: "Monthly", Q: "Quarterly" };

/** The partner list in groups: the world and the countries, then former countries, then the codes that are no country. */
const PARTNER_GROUPS: Array<{ id: string; label?: string }> = [
  { id: "country" },
  { id: "former", label: "Former countries" },
  { id: "other", label: "Not a country (stores, partner not stated)" },
];

/** "84 Machinery" for a chapter, the label alone for the total. */
const productText = (p: { code: string; label: string }) =>
  /^\d{2}$/.test(p.code) ? `${p.code} ${sentenceCase(p.label)}` : sentenceCase(p.label);

interface Props {
  options: TrendOptions;
  filters: TrendFilters;
  /** The last period with data per frequency, when known: later periods are not offered. */
  lastPeriods: Record<string, string>;
  onChange: (next: TrendFilters) => void;
  /** A short line on what is happening (e.g. "Analysing…"), read out politely. */
  status?: string;
}

/** The periods of a frequency that hold data: the listed ones, cut at the last period with data. */
export function availablePeriods(options: TrendOptions, freq: string, lastPeriods: Record<string, string>): string[] {
  const span = options.periods[freq] ?? [];
  const last = lastPeriods[freq];
  return last ? span.filter((p) => p <= last) : span;
}

export const FilterPanel: React.FC<Props> = ({ options, filters, lastPeriods, onChange, status }) => {
  const freqs = Object.keys(options.periods).sort((a, b) => (a === "M" ? -1 : b === "M" ? 1 : a.localeCompare(b)));
  const periods = availablePeriods(options, filters.freq, lastPeriods);

  const set = (patch: Partial<TrendFilters>) => onChange({ ...filters, ...patch });

  const setFreq = (freq: string) => {
    const span = availablePeriods(options, freq, lastPeriods);
    set({ freq, start: span[0], end: span[span.length - 1] });
  };

  // Moving From past To (or To before From) moves the other bound along.
  const setBound = (bound: "start" | "end", value: string) => {
    let start = bound === "start" ? value : filters.start ?? value;
    let end = bound === "end" ? value : filters.end ?? value;
    if (start > end) {
      if (bound === "start") end = start;
      else start = end;
    }
    set({ start, end });
  };

  return (
    <div className="tr-controls" role="group" aria-label="Which series to analyse">
      <label className="tr-field tr-field--wide">
        Product
        <select className="tr-select" value={filters.product} onChange={(e) => set({ product: e.target.value })}>
          {options.products.map((p) => (
            <option key={p.code} value={p.code} title={p.full ? sentenceCase(p.full) : undefined}>{productText(p)}</option>
          ))}
        </select>
      </label>

      <label className="tr-field">
        Reporter
        <select className="tr-select" value={filters.reporter} onChange={(e) => set({ reporter: e.target.value })}>
          {options.reporters.map((r) => (
            <option key={r.code} value={r.code}>{sentenceCase(r.label)}</option>
          ))}
        </select>
      </label>

      <label className="tr-field">
        Partner
        <select className="tr-select" value={filters.partner} onChange={(e) => set({ partner: e.target.value })}>
          {options.partners
            .filter((p) => !p.group || p.group === "world")
            .map((p) => (
              <option key={p.code} value={p.code}>{sentenceCase(p.label)}</option>
            ))}
          {PARTNER_GROUPS.map((g) => {
            const items = options.partners.filter((p) => p.group === g.id);
            if (!items.length) return null;
            const list = items.map((p) => <option key={p.code} value={p.code}>{sentenceCase(p.label)}</option>);
            return g.label ? <optgroup key={g.id} label={g.label}>{list}</optgroup> : <React.Fragment key={g.id}>{list}</React.Fragment>;
          })}
        </select>
      </label>

      {options.flow && !options.flow.constant && (
        <label className="tr-field">
          {options.flow.label}
          <select className="tr-select" value={filters.flow ?? ""} onChange={(e) => set({ flow: e.target.value })}>
            {(options.flow.values ?? []).map((v) => (
              <option key={v.code} value={v.code}>{v.label}</option>
            ))}
          </select>
        </label>
      )}

      {options.indicators && !options.indicators.constant && (
        <label className="tr-field">
          {options.indicators.label}
          <select className="tr-select" value={filters.indicators ?? ""} onChange={(e) => set({ indicators: e.target.value })}>
            {(options.indicators.values ?? []).map((v) => (
              <option key={v.code} value={v.code}>{v.label}</option>
            ))}
          </select>
        </label>
      )}

      <label className="tr-field">
        Frequency
        <select className="tr-select" value={filters.freq} onChange={(e) => setFreq(e.target.value)}>
          {freqs.map((f) => (
            <option key={f} value={f}>{FREQ_LABEL[f] ?? f}</option>
          ))}
        </select>
      </label>

      <label className="tr-field">
        From
        <select className="tr-select tr-select--period" value={filters.start ?? ""} onChange={(e) => setBound("start", e.target.value)}>
          {periods.map((p) => (
            <option key={p} value={p}>{periodLabel(p)}</option>
          ))}
        </select>
      </label>

      <label className="tr-field">
        To
        <select className="tr-select tr-select--period" value={filters.end ?? ""} onChange={(e) => setBound("end", e.target.value)}>
          {periods.slice().reverse().map((p) => (
            <option key={p} value={p}>{periodLabel(p)}</option>
          ))}
        </select>
      </label>

      <div className="tr-range">
        <span>Change points</span>
        <span aria-hidden="true">more</span>
        <input
          type="range"
          aria-label="Change-point sensitivity: further left finds more change points, further right fewer"
          min={0.5}
          max={6}
          step={0.25}
          value={filters.penalty}
          aria-valuetext={`Penalty ${filters.penalty}: ${filters.penalty <= 2 ? "more" : filters.penalty >= 4.5 ? "fewer" : "a balanced number of"} change points`}
          onChange={(e) => set({ penalty: Number(e.target.value) })}
        />
        <span aria-hidden="true">fewer</span>
      </div>
      <p className="tr-status" role="status" aria-live="polite">{status}</p>
    </div>
  );
};
