import React, { CSSProperties, useCallback, useState } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Legend,
  ReferenceDot,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

interface SeriesConfig {
  name: string;
  color?: string;
}

/**
 * A mark an agent drew on the bars (view runtime, src/view). Optional: without
 * the `marks` prop the chart renders as before.
 * - highlight: rings the bars whose code (options.codeField, default "code") is in `codes`.
 * - annotation: a leader line to the bar whose code is `x`; its height is read from the data.
 */
export interface BarChartMark {
  id: string;
  kind: "highlight" | "annotation";
  /** The agent's pen. */
  color: string;
  /** The agent's ink, for labels; defaults to color. */
  ink?: string;
  label?: string;
  number?: number;
  showNumber?: boolean;
  codes?: string[];
  x?: string;
}

const markFont = "'Archivo', system-ui, sans-serif";

const markNumber = (x: number, y: number, n: number | undefined, color: string) =>
  n === undefined ? null : (
    <g>
      <circle cx={x} cy={y} r={10} fill={color} stroke="#FFFFFF" strokeWidth={2} />
      <text x={x} y={y + 4.2} textAnchor="middle" style={{ fontFamily: markFont, fontSize: 12, fontWeight: 700, fill: "#FFFFFF" }}>
        {n}
      </text>
    </g>
  );

interface Props {
  id: string;
  title?: string;
  color?: string;
  data: any[];
  labelField: string;
  dataField: string;
  series?: SeriesConfig[];
  options?: Record<string, any>;
  styles?: CSSProperties;
  className?: string;
  /** Agent marks drawn on the bars (see BarChartMark). */
  marks?: BarChartMark[];
}

const defaultSeriesColors = ["#4a90e2", "#10B981", "#F97316", "#F43F5E", "#8B5CF6", "#EC4899", "#06B6D4", "#84CC16"];

// Helper to get nested values using dot notation (e.g., "measures.value")
const getNestedValue = (obj: any, path: string): any => {
  if (!obj || !path) return undefined;
  const parts = path.split('.');
  let current = obj;
  for (const part of parts) {
    if (current === null || current === undefined) return undefined;
    if (Array.isArray(current)) {
      current = current[0];
      if (current === undefined) return undefined;
    }
    current = current[part];
  }
  return current;
};

const resolveColor = (
  explicit?: string,
  options?: Record<string, any>,
  styles?: CSSProperties
) =>
  explicit ||
  options?.barColor ||
  (styles && (styles as any)["--chart-bar-color"]) ||
  "#7f56d9";

// Readable axes: large numbers in compact form (25B, 1.5M), long category labels shortened.
const compactNumber = (value: unknown): string =>
  typeof value === "number" && Math.abs(value) >= 10000
    ? new Intl.NumberFormat("en", { notation: "compact", maximumFractionDigits: 1 }).format(value)
    : String(value ?? "");
const shortLabel = (value: unknown, max = 26): string => {
  const text = String(value ?? "");
  return text.length > max ? `${text.slice(0, max - 1)}…` : text;
};

export const BarChartComponent: React.FC<Props> = ({
  id,
  title,
  color,
  data,
  labelField,
  dataField,
  series,
  options,
  styles,
  className,
  marks,
}) => {
  const [hiddenSeries, setHiddenSeries] = useState<Set<string>>(new Set());

  // Transform data if using nested fields (dot notation) and no multi-series
  const isNestedField = (field: string) => field?.includes('.') || false;
  const chartData = React.useMemo(() => {
    if (series && series.length > 0) {
      return data;
    }
    if (!isNestedField(labelField) && !isNestedField(dataField)) {
      return data; // No transformation needed
    }
    return data.map((item: any) => ({
      name: isNestedField(labelField) ? getNestedValue(item, labelField) : item[labelField],
      value: isNestedField(dataField) ? getNestedValue(item, dataField) : item[dataField],
    }));
  }, [data, labelField, dataField, series]);

  // Use transformed field names if we did transformation
  const actualLabelField =
    series && series.length > 0
      ? "name"
      : (isNestedField(labelField) || isNestedField(dataField)) ? 'name' : labelField;
  const actualDataField =
    series && series.length > 0
      ? series[0]?.name || 'value'
      : (isNestedField(labelField) || isNestedField(dataField)) ? 'value' : dataField;

  const containerStyle: CSSProperties = {
    width: "100%",
    // Grows with the chart: horizontal bar charts get taller with more categories.
    minHeight: "400px",
    marginBottom: "20px",
    ...styles,
  };

  const orientation = options?.orientation || "vertical";
  const showGrid = options?.showGrid ?? true;
  const showLegend = options?.showLegend ?? true;
  const showTooltip = options?.showTooltip ?? true;
  const legendPosition = options?.legendPosition || "top";
  const resolvedColor = resolveColor(color, options, styles);

  const handleLegendClick = useCallback((dataKey: string) => {
    setHiddenSeries((prev) => {
      const next = new Set(prev);
      if (next.has(dataKey)) {
        next.delete(dataKey);
      } else {
        next.add(dataKey);
      }
      return next;
    });
  }, []);

  // Agent marks: rings on highlighted bars, leader lines on annotated ones.
  const codeField: string = options?.codeField || "code";
  const codeOf = (row: any) => String(row?.[codeField] ?? "");
  const highlightOf = (row: any): BarChartMark | undefined =>
    (marks || []).find((m) => m.kind === "highlight" && (m.codes || []).indexOf(codeOf(row)) >= 0);
  const horizontal = orientation === "horizontal";
  const markDots = (marks || [])
    .map((m) => {
      const rows = (chartData || []).filter((row: any) =>
        m.kind === "highlight" ? (m.codes || []).indexOf(codeOf(row)) >= 0 : codeOf(row) === String(m.x ?? ""),
      );
      const row = rows[0];
      if (!row) return null;
      const cat = row[actualLabelField];
      const val = row[actualDataField];
      if (typeof val !== "number") return null;
      if (m.kind === "highlight" && !m.showNumber) return null;
      return (
        <ReferenceDot
          key={`mark-${m.id}`}
          x={horizontal ? val : cat}
          y={horizontal ? cat : val}
          ifOverflow="extendDomain"
          shape={(p: any) => {
            const cx = p?.cx ?? 0;
            const cy = p?.cy ?? 0;
            if (m.kind === "highlight") {
              return horizontal ? markNumber(cx + 16, cy, m.number, m.color) || <g /> : markNumber(cx, cy - 16, m.number, m.color) || <g />;
            }
            const lx = horizontal ? cx + 34 : cx;
            const ly = horizontal ? cy : Math.max(cy - 40, 14);
            return (
              <g>
                <line x1={cx} y1={cy} x2={horizontal ? lx - 4 : cx} y2={horizontal ? cy : ly + 4} stroke={m.color} strokeWidth={1.25} />
                <circle cx={cx} cy={cy} r={5} fill="#FFFFFF" stroke={m.color} strokeWidth={2.5} />
                {m.showNumber ? markNumber(lx + 8, ly - (horizontal ? 0 : 4), m.number, m.color) : null}
                <text x={lx + (m.showNumber ? 22 : 0)} y={ly + (horizontal ? 4 : 0)} style={{ fontFamily: markFont, fontSize: 12.5, fontWeight: 600, fill: m.ink || m.color }}>
                  {m.label}
                </text>
              </g>
            );
          }}
        />
      );
    })
    .filter(Boolean);

  const renderLegend = (props: any) => {
    const { payload } = props;
    if (!payload) return null;
    return (
      <div style={{ display: "flex", justifyContent: "center", gap: "14px", flexWrap: "wrap", padding: "8px" }}>
        {payload.map((entry: any, index: number) => (
          <div
            key={`legend-${index}`}
            onClick={() => handleLegendClick(entry.dataKey)}
            style={{
              display: "flex",
              alignItems: "center",
              gap: "6px",
              cursor: "pointer",
              opacity: hiddenSeries.has(entry.dataKey) ? 0.4 : 1,
              textDecoration: hiddenSeries.has(entry.dataKey) ? "line-through" : "none",
            }}
          >
            <span style={{ width: 12, height: 12, backgroundColor: entry.color, borderRadius: 2 }} />
            <span style={{ fontSize: 13, color: "#374151" }}>{entry.value}</span>
          </div>
        ))}
      </div>
    );
  };

  return (
    <div id={id} className={className} style={containerStyle}>
      {title && <h3 style={{ textAlign: "center", marginBottom: "10px" }}>{title}</h3>}
      <ResponsiveContainer
        width="100%"
        height={orientation === "horizontal" ? Math.max(360, (chartData?.length ?? 0) * 28 + 60) : 360}
      >
        <BarChart
          data={chartData}
          margin={{ top: 5, right: 30, left: 20, bottom: 5 }}
          layout={orientation === "horizontal" ? "vertical" : "horizontal"}
          barSize={options?.barWidth}
          barCategoryGap={options?.barGap}
        >
          {showGrid && (
            <CartesianGrid strokeDasharray="3 3" stroke={options?.gridColor} />
          )}
          <XAxis
            type={orientation === "horizontal" ? "number" : "category"}
            dataKey={orientation === "horizontal" ? actualDataField : actualLabelField}
            tickFormatter={orientation === "horizontal" ? compactNumber : (v: unknown) => shortLabel(v, 14)}
          />
          <YAxis
            type={orientation === "horizontal" ? "category" : "number"}
            dataKey={orientation === "horizontal" ? actualLabelField : undefined}
            width={orientation === "horizontal" ? 190 : 64}
            interval={0}
            tick={{ fontSize: 12 }}
            tickFormatter={orientation === "horizontal" ? (v: unknown) => shortLabel(v, 24) : compactNumber}
          />
          {showTooltip && <Tooltip formatter={(v: unknown) => (typeof v === "number" ? v.toLocaleString("en-US", { maximumFractionDigits: 2 }) : String(v ?? ""))} />}
          {showLegend && <Legend verticalAlign={legendPosition} content={renderLegend} />}
          {series && series.length > 0 ? (
            series.map((s, index) => (
              <Bar
                key={s.name || index}
                dataKey={s.name}
                name={s.name}
                fill={s.color || defaultSeriesColors[index % defaultSeriesColors.length]}
                stackId={options?.stacked ? "stack" : undefined}
                isAnimationActive={options?.animate ?? true}
                hide={hiddenSeries.has(s.name || "")}
              />
            ))
          ) : (
            <Bar
              dataKey={actualDataField}
              fill={resolvedColor}
              stackId={options?.stacked ? "stack" : undefined}
              isAnimationActive={options?.animate ?? true}
              hide={hiddenSeries.has(actualDataField)}
            >
              {marks
                ? (chartData || []).map((row: any, i: number) => {
                    const m = highlightOf(row);
                    return <Cell key={`cell-${i}`} fill={resolvedColor} stroke={m?.color} strokeWidth={m ? 3 : 0} />;
                  })
                : null}
            </Bar>
          )}
          {marks && (!series || series.length === 0) ? markDots : null}
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
};