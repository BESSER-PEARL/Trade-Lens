// One geometry and one projection for every map on the page (choropleth, flow map).
//
// Geometry: world-atlas 2.0.2 `countries-110m.json` (TopoJSON, ~108 KB, ISC licence),
// itself derived from Natural Earth 1:110m Admin 0 countries (public domain). Country
// ids are ISO 3166-1 numeric; iso.ts maps them to the alpha-2 codes Eurostat uses.
// Projection: Equal Earth (d3-geo 3.1.1, ISC), the same projection the old explorer
// pre-projected with, fitted to the container width at render time.

import { geoCentroid, geoEqualEarth, geoPath } from "d3-geo";
import type { GeoPath, GeoProjection } from "d3-geo";
import { feature } from "topojson-client";
import type { GeometryCollection, Topology } from "topojson-specification";
import type { Feature, FeatureCollection, Geometry } from "geojson";
import topology from "world-atlas/countries-110m.json";
import { EUROSTAT_TO_ISO, ISO_N3_TO_A2, NAME_TO_A2 } from "./iso";

export type LonLat = [number, number];

export interface Country {
  /** world-atlas id (ISO numeric, or -99 for a few disputed territories). */
  id: string;
  /** ISO alpha-2, null when unknown. */
  code: string | null;
  /** Natural Earth short name; the API's label is preferred when there is one. */
  name: string;
  feature: Feature<Geometry>;
  centroid: LonLat;
}

export interface WorldGeometry {
  countries: Country[];
  byCode: Map<string, Country>;
  /** Every country but Antarctica, what the world extent is fitted to. */
  land: FeatureCollection<Geometry>;
}

/** Partners that trade with Luxembourg but are too small for the 1:110m geometry. */
const EXTRA_CENTROIDS: Record<string, LonLat> = {
  LU: [6.13, 49.61], HK: [114.17, 22.32], SG: [103.82, 1.35], MT: [14.4, 35.9], LI: [9.55, 47.14],
  MC: [7.42, 43.73], AD: [1.52, 42.5], SM: [12.46, 43.94], BH: [50.55, 26.07], MU: [57.55, -20.35],
  MV: [73.5, 4.2], BB: [-59.55, 13.19], SC: [55.45, -4.68], MO: [113.55, 22.2], GI: [-5.35, 36.14],
  KY: [-81.25, 19.3], BM: [-64.75, 32.3], VG: [-64.6, 18.4], AW: [-69.97, 12.52], CW: [-68.97, 12.17],
  AG: [-61.8, 17.08], KN: [-62.75, 17.3], LC: [-60.98, 13.9], GD: [-61.68, 12.12], VC: [-61.2, 13.25],
  DM: [-61.37, 15.42], TO: [-175.2, -21.2], WS: [-172.1, -13.76], KM: [43.87, -11.88], ST: [6.73, 0.33],
  CV: [-23.6, 15.1], FO: [-6.9, 62.0], IM: [-4.5, 54.2], JE: [-2.13, 49.21], GG: [-2.58, 49.46],
};

const ANTARCTICA = "010";

/** Eurostat partner code -> ISO alpha-2 (EL -> GR, UK -> GB). */
export function toIso(code: string): string {
  return EUROSTAT_TO_ISO[code] || code;
}

/** Planar area of a lon/lat ring (shoelace, longitude scaled by cos of the mean latitude):
 *  only used to pick a country's largest polygon, so the approximation is fine. */
function ringArea(ring: number[][]): number {
  if (ring.length < 3) return 0;
  const lat0 = (ring.reduce((s, p) => s + p[1], 0) / ring.length) * (Math.PI / 180);
  const k = Math.cos(lat0);
  let a = 0;
  for (let i = 0; i < ring.length; i++) {
    const [x1, y1] = ring[i], [x2, y2] = ring[(i + 1) % ring.length];
    a += x1 * k * y2 - x2 * k * y1;
  }
  return Math.abs(a) / 2;
}

/** Centroid of a country's main polygon: the contiguous United States, not a point
 *  pulled north-west by Alaska; mainland France, not the Atlantic. */
function mainCentroid(f: Feature<Geometry>): LonLat {
  const g = f.geometry;
  if (g.type === "MultiPolygon") {
    let best: number[][][] | null = null;
    let bestArea = -1;
    g.coordinates.forEach((poly) => {
      const a = ringArea(poly[0]);
      if (a > bestArea) { bestArea = a; best = poly; }
    });
    if (best) return geoCentroid({ type: "Polygon", coordinates: best }) as LonLat;
  }
  return geoCentroid(f) as LonLat;
}

let cache: WorldGeometry | null = null;

export function worldGeometry(): WorldGeometry {
  if (cache) return cache;
  const topo = topology as unknown as Topology;
  const fc = feature(topo, topo.objects.countries as GeometryCollection) as FeatureCollection<Geometry>;
  const countries: Country[] = [];
  const byCode = new Map<string, Country>();
  fc.features.forEach((f) => {
    // A few territories of world-atlas (Kosovo, Northern Cyprus, Somaliland) carry no id: they are
    // keyed by name, so no two countries share the key "undefined".
    const raw = f.id === undefined || f.id === null ? undefined : String(f.id);
    if (raw === ANTARCTICA) return;
    const name = String((f.properties as { name?: string } | null)?.name ?? raw ?? "");
    const id = raw ?? `name:${name}`;
    const code = (raw !== undefined ? ISO_N3_TO_A2[raw] : undefined) ?? NAME_TO_A2[name] ?? null;
    const c: Country = { id, code, name, feature: f, centroid: mainCentroid(f) };
    countries.push(c);
    if (code) byCode.set(code, c);
  });
  cache = { countries, byCode, land: { type: "FeatureCollection", features: countries.map((c) => c.feature) } };
  return cache;
}

/** Centroid of a partner (Eurostat or ISO code): from the geometry, else the small-state table. */
export function centroidOf(code: string): LonLat | null {
  const iso = toIso(code);
  const c = worldGeometry().byCode.get(iso);
  if (c) return c.centroid;
  return EXTRA_CENTROIDS[iso] || null;
}

/** Which partner codes have neither a polygon nor a known centroid (for a note under a map). */
export function offGeometry(codes: string[]): string[] {
  return codes.filter((c) => !centroidOf(c));
}

export type Extent = "world" | "europe";

// d3-geo wants spherical polygons wound clockwise (exterior ring), unlike RFC 7946.
const EUROPE_BOX: Feature<Geometry> = {
  type: "Feature",
  properties: {},
  geometry: { type: "Polygon", coordinates: [[[-11, 35], [-11, 71], [41, 71], [41, 35], [-11, 35]]] },
};

export interface Fitted {
  projection: GeoProjection;
  path: GeoPath;
  height: number;
}

/** The Equal Earth projection fitted to `width` CSS pixels; the height follows. */
export function fitProjection(width: number, extent: Extent = "world", padding = 2): Fitted {
  const world = worldGeometry();
  const projection = geoEqualEarth();
  const path = geoPath(projection);
  if (extent === "europe") {
    const height = Math.round(width * 0.8);
    projection.fitExtent([[padding, padding], [width - padding, height - padding]], EUROPE_BOX);
    return { projection, path, height };
  }
  projection.fitWidth(Math.max(1, width - 2 * padding), world.land);
  const b = path.bounds(world.land);
  const t = projection.translate();
  projection.translate([t[0] + padding - b[0][0], t[1] + padding - b[0][1]]);
  return { projection, path, height: Math.ceil(b[1][1] - b[0][1] + 2 * padding) };
}

/** A great-circle line between two points, for geoPath (which resamples it along the arc). */
export function arc(from: LonLat, to: LonLat): Feature<Geometry> {
  return { type: "Feature", properties: {}, geometry: { type: "LineString", coordinates: [from, to] } };
}

const frameCache: Record<string, Fitted> = {};

/** True when a partner's centroid falls outside the drawn frame of an extent (Europe cuts off China). */
export function outsideFrame(code: string, extent: Extent = "world"): boolean {
  const c = centroidOf(code);
  if (!c) return true;
  if (extent === "world") return false;
  const f = frameCache[extent] || (frameCache[extent] = fitProjection(1000, extent));
  const p = f.projection(c);
  if (!p) return true;
  return p[0] < 0 || p[0] > 1000 || p[1] < 0 || p[1] > f.height;
}
