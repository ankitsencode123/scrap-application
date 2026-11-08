export type LatLng = [number, number];
export type ScrapType =
  | "metal"
  | "metals"
  | "plastic"
  | "plastics"
  | "plastic_bottles"
  | "paper"
  | "paper_cardboard"
  | "ewaste"
  | "ewaste_appliances"
  | "glass"
  | "clothes"
  | "mixed";

export interface ScrapMetaItem {
  label: string;
  emoji: string;
  cssVar: string;
  ratePerKg: number;
}

export const DEFAULT_CENTER: LatLng = [22.5726, 88.3639];

export const SCRAP_META: Record<string, ScrapMetaItem> = {
  metal: { label: "Metals", emoji: "🔩", cssVar: "--metal", ratePerKg: 32 },
  metals: { label: "Metals", emoji: "🔩", cssVar: "--metal", ratePerKg: 32 },
  plastic: { label: "Plastic", emoji: "🧴", cssVar: "--plastic", ratePerKg: 14 },
  plastics: { label: "Plastics", emoji: "🧴", cssVar: "--plastic", ratePerKg: 14 },
  plastic_bottles: { label: "Plastic Bottles", emoji: "🧴", cssVar: "--plastic", ratePerKg: 14 },
  paper: { label: "Paper & Cardboard", emoji: "📰", cssVar: "--paper", ratePerKg: 12 },
  paper_cardboard: { label: "Paper & Cardboard", emoji: "📰", cssVar: "--paper", ratePerKg: 12 },
  ewaste: { label: "E-Waste", emoji: "💻", cssVar: "--ewaste", ratePerKg: 55 },
  ewaste_appliances: { label: "E-Waste & Appliances", emoji: "💻", cssVar: "--ewaste", ratePerKg: 55 },
  glass: { label: "Glass Bottles", emoji: "🫙", cssVar: "--metal", ratePerKg: 6 },
  clothes: { label: "Clothes & Textiles", emoji: "👕", cssVar: "--paper", ratePerKg: 15 },
  mixed: { label: "Mixed Scrap", emoji: "📦", cssVar: "--accent", ratePerKg: 20 },
};

export function getScrapMeta(category?: string | null): ScrapMetaItem {
  if (!category) return SCRAP_META["metal"]!;
  const key = category.toLowerCase().trim();
  return (
    SCRAP_META[key] ?? {
      label: category.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase()),
      emoji: "♻️",
      cssVar: "--primary",
      ratePerKg: 20,
    }
  );
}

export interface CentreItem {
  kind: "recycler" | "collection" | "storage";
  name: string;
  pos: LatLng;
  radius: number;
}

export function generateCentresAround(center: LatLng, areaName = "Metro"): CentreItem[] {
  const [lat, lng] = center;
  return [
    { kind: "recycler", name: `${areaName} GreenCycle Recycling Plant`, pos: [lat - 0.024, lng + 0.026], radius: 1400 },
    { kind: "recycler", name: `${areaName} EcoMetal Smelting & Recovery`, pos: [lat + 0.028, lng + 0.038], radius: 1200 },
    { kind: "collection", name: `${areaName} Central Material Recovery Facility`, pos: [lat - 0.012, lng - 0.025], radius: 1100 },
    { kind: "collection", name: `${areaName} North Scrap Transit Hub`, pos: [lat + 0.022, lng - 0.019], radius: 1000 },
    { kind: "storage", name: `${areaName} Industrial Scrap Depot`, pos: [lat - 0.031, lng - 0.008], radius: 800 },
    { kind: "storage", name: `${areaName} EcoBuffer Transit Yard`, pos: [lat + 0.016, lng + 0.018], radius: 750 },
  ];
}

export interface StopItem {
  id: string;
  label: string;
  pos: LatLng;
  category: string;
  category_label: string;
  kg: number;
  expected_price: number;
  status: string;
  is_mine?: boolean;
  done?: boolean;
}

export const STEPS = [
  "Pickup Requested",
  "Collector Assigned",
  "Collector On The Way",
  "Collector Arrived",
  "Scrap Collected",
  "Delivery Completed",
];

export function haversine(a: LatLng, b: LatLng): number {
  const R = 6371;
  const dLat = ((b[0] - a[0]) * Math.PI) / 180;
  const dLng = ((b[1] - a[1]) * Math.PI) / 180;
  const s =
    Math.sin(dLat / 2) ** 2 +
    Math.cos((a[0] * Math.PI) / 180) * Math.cos((b[0] * Math.PI) / 180) * Math.sin(dLng / 2) ** 2;
  return 2 * R * Math.asin(Math.sqrt(s));
}

export function pathLength(path: LatLng[]): number {
  let d = 0;
  for (let i = 1; i < path.length; i++) d += haversine(path[i - 1]!, path[i]!);
  return d;
}

/** Fallback road-ish path: manhattan-style elbows with densified points */
export function fallbackPath(stops: LatLng[]): LatLng[] {
  if (stops.length < 2) return stops;
  const out: LatLng[] = [];
  for (let i = 1; i < stops.length; i++) {
    const a = stops[i - 1]!,
      b = stops[i]!;
    const mid: LatLng = [a[0], b[1]];
    for (const [p, q] of [
      [a, mid],
      [mid, b],
    ] as [LatLng, LatLng][]) {
      for (let t = 0; t < 12; t++) out.push([p[0] + ((q[0] - p[0]) * t) / 12, p[1] + ((q[1] - p[1]) * t) / 12]);
    }
  }
  out.push(stops[stops.length - 1]!);
  return out;
}

export async function fetchRoute(stops: LatLng[], alternatives = false): Promise<LatLng[][]> {
  if (stops.length < 2) return [stops];
  try {
    const coords = stops.map(([la, ln]) => `${ln},${la}`).join(";");
    const res = await fetch(
      `https://router.project-osrm.org/route/v1/driving/${coords}?overview=full&geometries=geojson&alternatives=${alternatives}`,
    );
    if (!res.ok) throw new Error();
    const json = await res.json();
    if (!json.routes || json.routes.length === 0) throw new Error();
    return json.routes.map((r: { geometry: { coordinates: [number, number][] } }) =>
      r.geometry.coordinates.map(([ln, la]) => [la, ln] as LatLng),
    );
  } catch {
    return [fallbackPath(stops)];
  }
}
