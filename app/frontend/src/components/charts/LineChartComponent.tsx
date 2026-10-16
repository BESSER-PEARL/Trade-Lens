import React, { CSSProperties, useState, useCallback } from "react";
import {
  Area,
  CartesianGrid,
  ComposedChart,
  Legend,
  Line,
  LineChart,
  ReferenceArea,
  ReferenceDot,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

/**
 * A mark an agent drew on the chart (view runtime, src/view). Optional: without
 * the `marks` prop and the view options below, the chart renders as before.
 * - event: a dashed rule at x, with its label at the top (row staggers labels).
 * - span: a shaded band from x to x2.
 * - line: an extra line from the data key `dataKey` (a fitted trend).
 * - band: a hatched range from the data key `dataKey`, whose values are [low, high].
 * - point: a ring with a leader line at (x, y); y is read from the chart's own data.
 */
export interface LineChartMark {
  id: string;
  kind: "event" | "span" | "line" | "band" | "point";
  /** The agent's pen. */
  color: string;
  /** The agent's ink, for labels; defaults to color. */
  ink?: string;
  label?: string;
  number?: number;
  showNumber?: boolean;
  x?: number | string;
  x2?: number | string;
  y?: number;
  dataKey?: string;
  /** Label row of an event, to keep labels from overlapping. */
  row?: number;
}

interface SeriesConfig {
  name: string;
  label?: string;
  color?: string;
  'data-source'?: string;
  dataSource?: string;
  endpoint?: string;
  'label-field'?: string;
  labelField?: string;
  'data-field'?: string;
  dataField?: string;
  filter?: string;
  data?: Array<{ name: string; value: number }>;
  fetchedData?: any[];  // Pre-fetched data from Renderer
  strokeWidth?: number;
  /** Colour of the series' direct label (options.directLabels). */
  labelColor?: string;
}

interface Props {
  id: string;
  title?: string;
  color?: string;
  data?: any[];
  series?: SeriesConfig[];
  labelField?: string;
  dataField?: string | string[];
  options?: Record<string, any>;
  styles?: CSSProperties;
  className?: string;
  /** Agent marks drawn over the data (see LineChartMark). */
  marks?: LineChartMark[];
}

const markFont = "'Archivo', system-ui, sans-serif";

/** The numbered circle of a mark, in its agent's pen. */
const markNumber = (x: number, y: number, n: number | undefined, color: string, key?: string) =>
  n === undefined ? null : (
    <g key={key}>
      <circle cx={x} cy={y} r={10} fill={color} stroke="#FFFFFF" strokeWidth={2} />
      <text x={x} y={y + 4.2} textAnchor="middle" style={{ fontFamily: markFont, fontSize: 12, fontWeight: 700, fill: "#FFFFFF" }}>
        {n}
      </text>
    </g>
  );

const defaultColors = ["#4a90e2", "#10B981", "#F97316", "#F43F5E", "#8B5CF6", "#EC4899", "#06B6D4", "#84CC16"];

// Helper to get nested value from object using dot notation (e.g., "measures.value")
// Handles arrays by getting first element's value
const getNestedValue = (obj: any, path: string): any => {
  if (!path || !obj) return undefined;
  // If no dot, just return direct property
  if (!path.includes('.')) return obj[path];
  // Navigate through nested properties
  const parts = path.split('.');
  let value = obj;
  for (let i = 0; i < parts.length; i++) {
    const part = parts[i];
    if (value == null) return undefined;
    
    // If current value is an array, get the first element and continue from there
    if (Array.isArray(value)) {
      if (value.length === 0) return undefined;
      // Get first element
      value = value[0];
    }
    
    value = value[part];
  }
  return value;
};


// Readable axes: large numbers in compact form (25B, 1.5M), long category labels shortened.
const compactNumber = (value: unknown): string =>
  typeof value === "number" && Math.abs(value) >= 10000
    ? new Intl.NumberFormat("en", { notation: "compact", maximumFractionDigits: 1 }).format(value)
    : String(value ?? "");

export const LineChartComponent: React.FC<Props> = ({
  id,
  title,
  color,
  data: propData,
  series: propSeries,
  labelField,
  dataField,
  options,
  styles,
  className,
  marks,
}) => {
  // Track visible series for clickable legend
  const [hiddenSeries, setHiddenSeries] = useState<Set<string>>(new Set());

  // Normalize series from props or create from single dataField
  const series: SeriesConfig[] = React.useMemo(() => {
    if (propSeries && propSeries.length > 0) {
      return propSeries;
    }
    if (Array.isArray(dataField)) {
      return dataField.map((field, i) => ({
        name: field,
        color: defaultColors[i % defaultColors.length],
        dataField: field,
        labelField: labelField || 'name',
      }));
    }
    if (dataField) {
      return [{
        name: dataField,
        color: color || defaultColors[0],
        dataField,
        labelField: labelField || 'name',
      }];
    }
    return [];
  }, [propSeries, dataField, color, labelField]);

  // Merge all series data into a single dataset for recharts
  const mergedData = React.useMemo(() => {
    if (propData && propData.length > 0) {
      const firstRow = propData[0] || {};
      const hasSeriesKeys = series.some((s) => firstRow[s.name] !== undefined);
      if (!series.length || hasSeriesKeys) {
        return propData.map((item) => ({
          ...item,
          name:
            getNestedValue(item, labelField || 'name') ??
            item?.name ??
            item?.label,
        }));
      }
    }
    
    const dataMap = new Map<string, any>();
    
    series.forEach((s) => {
      const labelFieldName =
        s['label-field'] ||
        s.labelField ||
        (Array.isArray(labelField) ? labelField[0] : labelField) ||
        'name';
      const dataFieldName =
        s['data-field'] ||
        s.dataField ||
        s.name ||
        (Array.isArray(dataField) ? dataField[0] : dataField) ||
        'value';
      const data = s.fetchedData ?? s.data ?? [];
      data.forEach((point: any) => {
        const key =
          getNestedValue(point, labelFieldName) ??
          point?.[labelFieldName] ??
          point?.name ??
          point?.label ??
          '';
        if (!dataMap.has(key)) {
          dataMap.set(key, { name: key });
        }
        const rawValue =
          getNestedValue(point, dataFieldName) ??
          point?.[dataFieldName] ??
          point?.value;
        const numericValue =
          typeof rawValue === 'number' ? rawValue : parseFloat(rawValue);
        dataMap.get(key)[s.name] = Number.isFinite(numericValue) ? numericValue : 0;
      });
    });
    
    if (dataMap.size > 0) {
      return Array.from(dataMap.values());
    }

    return propData || [];
  }, [propData, series, labelField, dataField]);

  const containerStyle: CSSProperties = {
    width: "100%",
    height: "400px",
    marginBottom: "20px",
    ...styles,
  };

  const strokeWidth = options?.lineWidth ?? 2;
  const showGrid = options?.showGrid ?? true;
  const showLegend = options?.showLegend ?? true;
  const showTooltip = options?.showTooltip ?? true;
  const legendPosition = options?.legendPosition || "top";

  // Handle legend click to toggle series visibility
  const handleLegendClick = useCallback((dataKey: string) => {
    setHiddenSeries(prev => {
      const newSet = new Set(prev);
      if (newSet.has(dataKey)) {
        newSet.delete(dataKey);
      } else {
        newSet.add(dataKey);
      }
      return newSet;
    });
  }, []);

  // Custom legend with clickable items
  const renderLegend = (props: any) => {
    const { payload } = props;
    return (
      <div style={{ display: 'flex', justifyContent: 'center', gap: '20px', flexWrap: 'wrap', padding: '10px' }}>
        {payload.map((entry: any, index: number) => (
          <div
            key={`legend-${index}`}
            onClick={() => handleLegendClick(entry.dataKey)}
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: '6px',
              cursor: 'pointer',
              opacity: hiddenSeries.has(entry.dataKey) ? 0.4 : 1,
              textDecoration: hiddenSeries.has(entry.dataKey) ? 'line-through' : 'none',
              transition: 'opacity 0.2s',
            }}
          >
            <span style={{
              width: '14px',
              height: '14px',
              backgroundColor: entry.color,
              borderRadius: '2px',
            }} />
            <span style={{ fontSize: '14px', color: '#374151' }}>{entry.value}</span>
          </div>
        ))}
      </div>
    );
  };

  // View mode (marks, a gap fill or a numeric time axis): a composed chart that
  // can also draw areas. Without any of them the chart is the plain LineChart.
  const gapFill: string[] | undefined = Array.isArray(options?.gapFill) && options?.gapFill.length === 2 ? options?.gapFill : undefined;
  const viewMode = !!marks || !!gapFill || options?.xType === "number";
  const chartData = React.useMemo(() => {
    if (!gapFill) return mergedData;
    return mergedData.map((row: any) => {
      const a = row[gapFill[0]];
      const b = row[gapFill[1]];
      return typeof a === "number" && typeof b === "number" ? { ...row, __gap: [Math.min(a, b), Math.max(a, b)] } : row;
    });
  }, [mergedData, gapFill]);
  const yTickFormatter: (v: any) => string = options?.yTickFormatter || compactNumber;
  const xTickFormatter: ((v: any) => string) | undefined = options?.xTickFormatter;
  const valueFormatter: (v: any) => string =
    options?.valueFormatter ||
    ((v: unknown) => (typeof v === "number" ? v.toLocaleString("en-US", { maximumFractionDigits: 2 }) : String(v ?? "")));
  const allMarks = marks || [];
  const eventRows = allMarks.filter((m) => m.kind === "event" || m.kind === "span").reduce((r, m) => Math.max(r, (m.row ?? 0) + 1), 0);
  const chartHeight: number = options?.height ?? 360;
  const margin = viewMode
    ? { top: 8 + eventRows * 18, right: options?.marginRight ?? 40, left: 0, bottom: 5 }
    : { top: 5, right: 30, left: 20, bottom: 5 };
  const ChartComponent: any = viewMode ? ComposedChart : LineChart;
  const directLabels = viewMode && options?.directLabels;

  const eventLabel = (m: LineChartMark) => (props: any) => {
    const vb = props?.viewBox || {};
    const x = (vb.x ?? 0) as number;
    const row = m.row ?? 0;
    const y = 12 + row * 18;
    return (
      <g>
        <text x={x + 6} y={y} style={{ fontFamily: markFont, fontSize: 12.5, fontWeight: 600, fill: m.ink || m.color }}>
          {m.label}
        </text>
        {m.showNumber ? markNumber(x - 12, y - 4, m.number, m.color) : null}
      </g>
    );
  };

  const markChildren = viewMode
    ? allMarks.map((m) => {
        switch (m.kind) {
          case "event":
            return (
              <ReferenceLine
                key={m.id}
                x={m.x as any}
                stroke={m.color}
                strokeWidth={1.25}
                strokeDasharray="3 3"
                ifOverflow="extendDomain"
                segment={undefined}
                label={eventLabel(m)}
              />
            );
          case "span":
            return (
              <ReferenceArea
                key={m.id}
                x1={m.x as any}
                x2={m.x2 as any}
                fill={m.color}
                fillOpacity={0.1}
                stroke={m.color}
                strokeOpacity={0.5}
                strokeDasharray="3 3"
                ifOverflow="extendDomain"
                label={eventLabel(m)}
              />
            );
          case "line":
            return (
              <Line
                key={m.id}
                type="linear"
                dataKey={m.dataKey || m.id}
                name={m.label || m.id}
                stroke={m.color}
                strokeWidth={2}
                strokeDasharray="6 4"
                dot={false}
                connectNulls
                isAnimationActive={false}
                legendType="none"
              />
            );
          case "band":
            return (
              <Area
                key={m.id}
                type="linear"
                dataKey={m.dataKey || m.id}
                name={m.label || m.id}
                stroke={m.color}
                strokeWidth={1}
                fill={`url(#lc-hatch-${id}-${m.id})`}
                fillOpacity={1}
                connectNulls
                isAnimationActive={false}
                legendType="none"
              />
            );
          case "point":
            return typeof m.y === "number" ? (
              <ReferenceDot
                key={m.id}
                x={m.x as any}
                y={m.y}
                ifOverflow="extendDomain"
                shape={(p: any) => {
                  const cx = p?.cx ?? 0;
                  const cy = p?.cy ?? 0;
                  const ly = Math.max(cy - 44, 14);
                  return (
                    <g>
                      <line x1={cx} x2={cx} y1={cy - 7} y2={ly + 4} stroke={m.color} strokeWidth={1.25} />
                      <circle cx={cx} cy={cy} r={6} fill="#FFFFFF" stroke={m.color} strokeWidth={2.5} />
                      <text x={cx + (m.showNumber ? 16 : 4)} y={ly} style={{ fontFamily: markFont, fontSize: 12.5, fontWeight: 600, fill: m.ink || m.color }}>
                        {m.label}
                      </text>
                      {m.showNumber ? markNumber(cx + 2, ly - 4, m.number, m.color) : null}
                    </g>
                  );
                }}
              />
            ) : null;
          default:
            return null;
        }
      })
    : null;

  // Numbers of line and band marks sit at their last point.
  const endNumbers = viewMode
    ? allMarks
        .filter((m) => (m.kind === "line" || m.kind === "band") && m.showNumber && m.dataKey)
        .map((m) => {
          let last: any = null;
          chartData.forEach((row: any) => {
            const v = row[m.dataKey as string];
            if (v !== undefined && v !== null) last = row;
          });
          if (!last) return null;
          const v = last[m.dataKey as string];
          const y = Array.isArray(v) ? (v[0] + v[1]) / 2 : v;
          return (
            <ReferenceDot
              key={`${m.id}-n`}
              x={last.name}
              y={y}
              ifOverflow="extendDomain"
              shape={(p: any) => markNumber((p?.cx ?? 0) + 20, p?.cy ?? 0, m.number, m.color) || <g />}
            />
          );
        })
    : null;

  const directLabel = (s: SeriesConfig, color: string) => (p: any) => {
    if (p?.index !== 0 || typeof p?.x !== "number") return <g />;
    const below = options?.directLabelBelow?.includes?.(s.name);
    return (
      <text
        x={p.x}
        y={p.y + (below ? 24 : -12)}
        style={{ fontFamily: markFont, fontSize: 13, fontWeight: 600, fill: s.labelColor || color }}
      >
        {s.label || s.name}
      </text>
    );
  };

  return (
    <div id={id} className={className} style={containerStyle}>
      {title && <h3 style={{ textAlign: "center", marginBottom: "10px" }}>{title}</h3>}
      {viewMode && allMarks.some((m) => m.kind === "band") && (
        <svg width={0} height={0} style={{ position: "absolute" }} aria-hidden="true">
          <defs>
            {allMarks
              .filter((m) => m.kind === "band")
              .map((m) => (
                <pattern key={m.id} id={`lc-hatch-${id}-${m.id}`} width={6} height={6} patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
                  <rect width={6} height={6} fill="#FFFFFF" fillOpacity={0.6} />
                  <line x1={0} y1={0} x2={0} y2={6} stroke={m.color} strokeWidth={1.5} />
                </pattern>
              ))}
          </defs>
        </svg>
      )}
      <ResponsiveContainer width="100%" height={chartHeight}>
        <ChartComponent data={chartData} margin={margin}>
          {showGrid && (
            <CartesianGrid
              strokeDasharray={options?.gridDasharray ?? "3 3"}
              stroke={options?.gridColor}
              vertical={options?.gridVertical ?? true}
            />
          )}
          {viewMode && options?.xType === "number" ? (
            <XAxis
              dataKey="name"
              type="number"
              domain={options?.xDomain || ["dataMin", "dataMax"]}
              ticks={options?.xTicks}
              tickFormatter={xTickFormatter}
              allowDecimals={false}
              tickLine={false}
              axisLine={{ stroke: options?.axisColor || "#C5CED8" }}
              tick={{ fontSize: 12, fill: options?.tickColor || "#5E6B7A" }}
            />
          ) : (
            <XAxis dataKey="name" />
          )}
          {viewMode ? (
            <YAxis
              tickFormatter={yTickFormatter}
              width={options?.yWidth ?? 48}
              domain={options?.yDomain}
              ticks={options?.yTicks}
              tickLine={false}
              axisLine={false}
              tick={{ fontSize: 12, fill: options?.tickColor || "#5E6B7A" }}
            />
          ) : (
            <YAxis tickFormatter={compactNumber} width={64} />
          )}
          {showTooltip && (
            <Tooltip
              formatter={(v: unknown) =>
                Array.isArray(v) ? v.map((x) => valueFormatter(x)).join(" to ") : valueFormatter(v)
              }
              labelFormatter={xTickFormatter ? (l: any) => xTickFormatter(l) : undefined}
            />
          )}
          {showLegend && !directLabels && <Legend verticalAlign={legendPosition} content={renderLegend} />}
          {gapFill && (
            <Area
              type={options?.curveType || "monotone"}
              dataKey="__gap"
              stroke="none"
              fill={options?.gapFillColor || "#7A8796"}
              fillOpacity={options?.gapFillOpacity ?? 0.13}
              isAnimationActive={false}
              legendType="none"
              activeDot={false}
              tooltipType="none"
            />
          )}
          {markChildren}
          {series.map((s, index) => {
            const color = s.color || defaultColors[index % defaultColors.length];
            return (
              <Line
                key={s.name}
                type={options?.curveType || "monotone"}
                dataKey={s.name}
                name={viewMode ? s.label || s.name : s.name}
                stroke={color}
                strokeWidth={s.strokeWidth ?? strokeWidth}
                dot={options?.dots === false ? false : options?.dotSize ? { r: options.dotSize } : true}
                isAnimationActive={options?.animate ?? true}
                hide={hiddenSeries.has(s.name)}
                connectNulls={viewMode}
                label={directLabels ? directLabel(s, color) : undefined}
              />
            );
          })}
          {endNumbers}
        </ChartComponent>
      </ResponsiveContainer>
    </div>
  );
};