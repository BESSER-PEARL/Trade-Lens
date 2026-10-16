// Treemap: the goods as nested tiles (d3-hierarchy, squarified). It shows two levels at a
// time under the node the reader opened: groups (HS2 chapters, say) with their tiles (HS4)
// inside. Clicking a tile, or Enter on it, opens its group; the panel keeps the breadcrumb.
// The backend already cut the long tail with its significance ladder (an OTHER child per
// node); `limit` folds what is left beyond the largest children into one more "other" tile.
// Tiles are shaded in the flow's ramp by their share; agents' highlights outline tiles in
// their pen (a code also marks the tile that contains it: 8517 marks the tile of 85).

import React, { useMemo } from "react";
import { hierarchy, treemap, treemapSquarify } from "d3-hierarchy";
import type { HierarchyRectangularNode } from "d3-hierarchy";
import { ChartTip } from "./ChartTip";
import { fmtEur, fmtPct, truncate } from "./format";
import { FONT_UI, TOKENS, labelOn, rampFor, toneColor } from "./palette";
import type { PenMark } from "./palette";
import { SvgBadge, useSvgId } from "./parts";
import { useMeasure } from "./useMeasure";
import { useRoving } from "./useRoving";

export interface TreeNode {
  code: string;
  name: string;
  value: number;
  share?: number;
  children?: TreeNode[];
}

export interface TreemapProps {
  /** The node opened: its children are drawn, with theirs inside. */
  node: TreeNode;
  /** The whole selection's total, for shares. */
  total: number;
  title: string;
  tone?: string;
  marks?: PenMark[];
  limit?: number;
  /** Open a group (a child of node). */
  onOpen?: (child: TreeNode) => void;
  label?: (n: TreeNode) => string;
  height?: number;
}

/** Keeps the largest `limit` children and folds the rest into one tile. */
export function foldChildren(children: TreeNode[], limit: number, parentCode: string): TreeNode[] {
  const sorted = children.filter((c) => c.value > 0).sort((a, b) => b.value - a.value);
  if (sorted.length <= limit) return sorted;
  const kept = sorted.slice(0, limit);
  const rest = sorted.slice(limit);
  const v = rest.reduce((s, c) => s + c.value, 0);
  return kept.concat([{ code: `${parentCode}~rest`, name: `${rest.length} more`, value: v, children: [] }]);
}

export function isOther(code: string): boolean {
  return /OTHER|~rest$/i.test(code);
}

function shareStep(share: number): number {
  return share >= 0.1 ? 5 : share >= 0.03 ? 4 : share >= 0.01 ? 3 : share >= 0.003 ? 2 : 1;
}

interface Laid {
  node: TreeNode;
  group: TreeNode;
  x0: number;
  y0: number;
  x1: number;
  y1: number;
}

export const Treemap: React.FC<TreemapProps> = ({ node, total, title, tone = "exports", marks = [], limit = 12, onOpen, label = (n) => n.name, height }) => {
  const { ref, width } = useMeasure<HTMLDivElement>(640);
  const ramp = rampFor(tone);
  const hatchId = useSvgId("tm-hatch");
  // On a phone the tiles would be too small to read: the largest chapters alone (no headings inside),
  // on a taller canvas. Opening a chapter still drills into it.
  const narrow = width < 520;
  const h = height || (narrow ? Math.round(Math.max(340, width * 1.05)) : Math.round(Math.max(280, Math.min(460, width * 0.58))));

  const { groups, tiles } = useMemo(() => {
    const kids = foldChildren(node.children || [], narrow ? Math.min(limit, 7) : limit, node.code);
    const data: TreeNode = {
      code: node.code,
      name: node.name,
      value: node.value,
      children: kids.map((k) => ({ ...k, children: narrow ? [] : foldChildren(k.children || [], limit, k.code) })),
    };
    const root = hierarchy<TreeNode>(data, (d) => (d.children && d.children.length ? d.children : null)).sum((d) =>
      d.children && d.children.length ? 0 : Math.max(0, d.value),
    );
    const laid = treemap<TreeNode>()
      .tile(treemapSquarify.ratio(1.4))
      .size([width, h])
      .round(true)
      .paddingInner(2)
      .paddingOuter(0)
      .paddingTop((d) => (d.depth === 1 && d.children && d.y1 - d.y0 > 60 && d.x1 - d.x0 > 70 ? 20 : d.depth === 1 && d.children ? 2 : 0))(root);
    const groups: Laid[] = [];
    const tiles: Laid[] = [];
    (laid.children || []).forEach((g: HierarchyRectangularNode<TreeNode>) => {
      groups.push({ node: g.data, group: g.data, x0: g.x0, y0: g.y0, x1: g.x1, y1: g.y1 });
      const leaves = g.children && g.children.length ? g.children : [g];
      leaves.forEach((t) => tiles.push({ node: t.data, group: g.data, x0: t.x0, y0: t.y0, x1: t.x1, y1: t.y1 }));
    });
    return { groups, tiles };
  }, [node, limit, width, h, narrow]);

  const canOpen = (g: TreeNode) => !!onOpen && !!g.children && g.children.length > 0 && !isOther(g.code);
  const roving = useRoving<SVGGElement>(tiles.length, {
    onActivate: (i) => {
      const g = tiles[i]?.group;
      if (g && canOpen(g)) onOpen!(g);
    },
  });

  // Highlights: the tile (or group) whose code is the mark's code or contains it.
  const outlines: Array<{ m: PenMark; r: Laid }> = [];
  marks.forEach((m) => {
    const hits: Laid[] = [];
    m.codes.forEach((c) => {
      const tile = tiles.find((t) => t.node.code === c) || tiles.find((t) => /^\d+$/.test(t.node.code) && String(c).startsWith(t.node.code));
      const group = groups.find((g) => g.node.code === c);
      const hit = group && group.node !== tile?.node ? group : tile;
      if (hit && hits.indexOf(hit) < 0) hits.push(hit);
    });
    hits.forEach((r) => outlines.push({ m, r }));
  });
  const badgeDone: Record<string, boolean> = {};

  const share = (n: TreeNode) => (n.share !== undefined ? n.share : total ? n.value / total : 0);
  const hovered = roving.hover ? tiles[roving.hover.i] : undefined;

  return (
    <div ref={ref} style={{ width: "100%", fontFamily: FONT_UI, color: TOKENS.ink }}>
      <div ref={roving.wrap} style={{ position: "relative" }}>
        <svg
          viewBox={`0 0 ${width} ${h}`}
          width={width}
          height={h}
          style={{ display: "block", width: "100%", height: "auto" }}
          role="group"
          aria-label={title}
          onKeyDown={roving.onKeyDown}
          onMouseLeave={() => roving.setHover(null)}
        >
          <defs>
            <pattern id={hatchId} width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
              <rect width="6" height="6" fill={ramp[0]} />
              <line x1="0" y1="0" x2="0" y2="6" stroke={ramp[2]} strokeWidth="1.5" />
            </pattern>
          </defs>
          {groups.map((g) => {
            const w = g.x1 - g.x0;
            const hh = g.y1 - g.y0;
            const hasHeader = !!g.node.children && g.node.children.length > 0 && hh > 60 && w > 70;
            return (
              <g key={`g${g.node.code}`} aria-hidden="true" pointerEvents="none">
                <rect x={g.x0} y={g.y0} width={w} height={hh} fill={TOKENS.surface} stroke={TOKENS.rule} />
                {hasHeader && (
                  <text x={g.x0 + 6} y={g.y0 + 14} fontSize={12} fontWeight={700} fill={TOKENS.ink}>
                    {truncate(label(g.node), Math.floor((w - 12) / 6.6))}
                  </text>
                )}
              </g>
            );
          })}
          {tiles.map((t, i) => {
            const w = t.x1 - t.x0;
            const hh = t.y1 - t.y0;
            const other = isOther(t.node.code);
            const s = share(t.node);
            const fill = other ? `url(#${hatchId})` : ramp[shareStep(s)];
            const textFill = other ? TOKENS.ink2 : labelOn(ramp[shareStep(s)]);
            const isHover = roving.hover?.i === i;
            const opens = canOpen(t.group);
            const aria = `${label(t.node)}: ${fmtEur(t.node.value)}, ${fmtPct(s)} of the total${t.group !== t.node ? `, in ${label(t.group)}` : ""}${opens ? ". Press Enter to open." : ""}`;
            const showName = w > 54 && hh > 24;
            const showValue = w > 54 && hh > 40;
            return (
              <g
                key={`t${t.group.code}-${t.node.code}-${i}`}
                ref={roving.register(i)}
                {...roving.itemProps(i)}
                role={opens ? "button" : "img"}
                aria-label={aria}
                style={{ outline: "none", cursor: opens ? "pointer" : "default" }}
              >
                <rect x={t.x0} y={t.y0} width={Math.max(0, w)} height={Math.max(0, hh)} fill={fill} stroke={isHover ? TOKENS.ink : "none"} strokeWidth={isHover ? 2 : 0} />
                {showName && (
                  <text x={t.x0 + 5} y={t.y0 + 15} fontSize={11.5} fontWeight={600} fill={textFill} pointerEvents="none">
                    {truncate(label(t.node), Math.floor((w - 10) / 6.2))}
                  </text>
                )}
                {showValue && (
                  <text x={t.x0 + 5} y={t.y0 + 30} fontSize={11} fill={textFill} pointerEvents="none" style={{ fontVariantNumeric: "tabular-nums" }}>
                    {fmtEur(t.node.value)}
                    {w > 110 ? `, ${fmtPct(s)}` : ""}
                  </text>
                )}
              </g>
            );
          })}
          {outlines.map(({ m, r }, k) => {
            const first = !badgeDone[m.id];
            badgeDone[m.id] = true;
            return (
              <g key={`o${m.id}-${k}`} aria-hidden="true" pointerEvents="none" opacity={m.opacity}>
                <rect x={r.x0 + 1} y={r.y0 + 1} width={Math.max(0, r.x1 - r.x0 - 2)} height={Math.max(0, r.y1 - r.y0 - 2)} fill="none" stroke={TOKENS.surface} strokeWidth={4} />
                <rect x={r.x0 + 1} y={r.y0 + 1} width={Math.max(0, r.x1 - r.x0 - 2)} height={Math.max(0, r.y1 - r.y0 - 2)} fill="none" stroke={m.color} strokeWidth={2.5} />
                {first && m.showNumber && <SvgBadge x={Math.min(width - 11, r.x1 - 12)} y={Math.max(11, r.y0 + 12)} n={m.n} color={m.color} />}
              </g>
            );
          })}
        </svg>
        <ChartTip
          x={roving.hover?.x ?? 0}
          y={roving.hover?.y ?? 0}
          width={width}
          visible={!!hovered}
          title={hovered ? label(hovered.node) : undefined}
          rows={
            hovered
              ? [
                  { value: fmtEur(hovered.node.value), label: "", swatch: toneColor(tone).fill },
                  { value: fmtPct(share(hovered.node)), label: "of the total" },
                  ...(hovered.group !== hovered.node ? [{ value: label(hovered.group), label: "" }] : []),
                  ...(canOpen(hovered.group) ? [{ value: "Click to open", label: "" }] : []),
                ]
              : []
          }
        />
      </div>
    </div>
  );
};
