// SPDX-License-Identifier: MIT
import React from "react";

/** A row of toggle buttons, one pressed: the visible label names the group. */
interface Segment<T> { label: string; value: T }

export function Segmented<T extends string | number>({ label, options, value, onChange }: {
  label: string; options: Segment<T>[]; value: T; onChange: (v: T) => void;
}) {
  return (
    <span className="tr-field">
      {label}
      <span className="tr-seg" role="group" aria-label={label}>
        {options.map((o) => (
          <button key={String(o.value)} type="button" aria-pressed={o.value === value} onClick={() => onChange(o.value)}>
            {o.label}
          </button>
        ))}
      </span>
    </span>
  );
}
