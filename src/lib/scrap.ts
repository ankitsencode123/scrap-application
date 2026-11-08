export const CATEGORIES = [
  { id: "paper_cardboard", label: "Paper & Cardboard", emoji: "📦" },
  { id: "plastic_bottles", label: "Plastic & Bottles", emoji: "🧴" },
  { id: "metals", label: "Metals", emoji: "🔩" },
  { id: "ewaste_appliances", label: "E-waste & Appliances", emoji: "🔌" },
  { id: "glass", label: "Glass", emoji: "🍾" },
  { id: "clothes", label: "Clothes", emoji: "👕" },
  { id: "mixed", label: "Mixed scrap", emoji: "♻️" },
] as const;

export const categoryOf = (id: string) => CATEGORIES.find((c) => c.id === id) ?? CATEGORIES[6];

// Statuses mirror the backend's ListingStatus values (see backend/app/db/enums.py).
export const PICKUP_STEPS = [
  { id: "posted", label: "Posted" },
  { id: "offer_accepted", label: "Offer accepted" },
  { id: "on_the_way", label: "Collector on the way" },
  { id: "arrived", label: "Arrived" },
  { id: "completed", label: "Picked up & paid" },
] as const;

export const statusLabel = (s: string) =>
  s === "cancelled" ? "Cancelled" : (PICKUP_STEPS.find((p) => p.id === s)?.label ?? s);

export const formatINR = (n: number | null | undefined) =>
  n == null ? "—" : `₹${Number(n).toLocaleString("en-IN")}`;

export function distanceKm(a: { lat: number; lng: number }, b: { lat: number; lng: number }) {
  const R = 6371;
  const dLat = ((b.lat - a.lat) * Math.PI) / 180;
  const dLng = ((b.lng - a.lng) * Math.PI) / 180;
  const x =
    Math.sin(dLat / 2) ** 2 +
    Math.cos((a.lat * Math.PI) / 180) * Math.cos((b.lat * Math.PI) / 180) * Math.sin(dLng / 2) ** 2;
  return 2 * R * Math.asin(Math.sqrt(x));
}
