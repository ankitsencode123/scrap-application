import { useQuery } from "@tanstack/react-query";
import { supabase } from "@/integrations/supabase/client";

export const CATEGORIES = [
  { id: "paper", label: "Paper & Cardboard", emoji: "📦" },
  { id: "plastic", label: "Plastic", emoji: "🧴" },
  { id: "metal", label: "Metal", emoji: "🔩" },
  { id: "ewaste", label: "E-waste", emoji: "🔌" },
  { id: "glass", label: "Glass", emoji: "🍾" },
  { id: "clothes", label: "Clothes", emoji: "👕" },
  { id: "other", label: "Other", emoji: "♻️" },
] as const;

export const categoryOf = (id: string) => CATEGORIES.find((c) => c.id === id) ?? CATEGORIES[6];

export const PICKUP_STEPS = [
  { id: "open", label: "Posted" },
  { id: "accepted", label: "Offer accepted" },
  { id: "on_the_way", label: "Collector on the way" },
  { id: "arrived", label: "Arrived" },
  { id: "completed", label: "Picked up & paid" },
] as const;

export const statusLabel = (s: string) =>
  s === "cancelled" ? "Cancelled" : (PICKUP_STEPS.find((p) => p.id === s)?.label ?? s);

export const formatINR = (n: number | null | undefined) =>
  n == null ? "—" : `₹${Number(n).toLocaleString("en-IN")}`;

export function usePhotoUrl(path: string | null | undefined) {
  return useQuery({
    queryKey: ["photo", path],
    enabled: !!path,
    staleTime: 50 * 60 * 1000,
    queryFn: async () => {
      const { data } = await supabase.storage.from("scrap-photos").createSignedUrl(path!, 3600);
      return data?.signedUrl ?? null;
    },
  });
}

export function useRating(userId: string | null | undefined) {
  return useQuery({
    queryKey: ["rating", userId],
    enabled: !!userId,
    queryFn: async () => {
      const { data } = await supabase.from("reviews").select("rating").eq("reviewee_id", userId!);
      const list = data ?? [];
      const avg = list.length ? list.reduce((a, r) => a + r.rating, 0) / list.length : 0;
      return { avg, count: list.length };
    },
  });
}

export function distanceKm(a: { lat: number; lng: number }, b: { lat: number; lng: number }) {
  const R = 6371;
  const dLat = ((b.lat - a.lat) * Math.PI) / 180;
  const dLng = ((b.lng - a.lng) * Math.PI) / 180;
  const x =
    Math.sin(dLat / 2) ** 2 +
    Math.cos((a.lat * Math.PI) / 180) * Math.cos((b.lat * Math.PI) / 180) * Math.sin(dLng / 2) ** 2;
  return 2 * R * Math.asin(Math.sqrt(x));
}
