import { createFileRoute } from "@tanstack/react-router";
import { ClientOnly } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";
import { lazy, Suspense, useCallback, useEffect, useState } from "react";
import { supabase } from "@/integrations/supabase/client";
import { ListingCard } from "@/components/ListingCard";
import { distanceKm } from "@/lib/scrap";

const ScrapMap = lazy(() => import("@/components/ScrapMap"));

export const Route = createFileRoute("/_authenticated/map")({
  head: () => ({
    meta: [
      { title: "Nearby scrap map — scrappe" },
      { name: "description", content: "See scrap posted for pickup around you on a map." },
      { property: "og:title", content: "Nearby scrap map — scrappe" },
      { property: "og:description", content: "See scrap posted for pickup around you on a map." },
    ],
  }),
  component: MapPage,
});

const DEFAULT = { lat: 28.6139, lng: 77.209 };

function MapPage() {
  const [pos, setPos] = useState(DEFAULT);
  const [selected, setSelected] = useState<string | null>(null);
  useEffect(() => {
    navigator.geolocation?.getCurrentPosition((p) => setPos({ lat: p.coords.latitude, lng: p.coords.longitude }));
  }, []);

  const { data: listings = [] } = useQuery({
    queryKey: ["map-listings"],
    queryFn: async () => {
      const { data } = await supabase.from("listings").select("*").eq("status", "open").not("lat", "is", null).limit(300);
      return data ?? [];
    },
  });
  const onSelect = useCallback((id: string) => setSelected(id), []);
  const sel = listings.find((l) => l.id === selected);

  return (
    <div className="-mx-4 -my-5 relative h-[calc(100dvh-3.5rem-5rem)] md:mx-0 md:my-0 md:h-[calc(100dvh-7rem)] md:overflow-hidden md:rounded-3xl md:border">
      <ClientOnly fallback={<div className="size-full bg-muted" />}>
        <Suspense fallback={<div className="size-full bg-muted" />}>
          <ScrapMap listings={listings} center={pos} onSelect={onSelect} />
        </Suspense>
      </ClientOnly>
      <div className="pointer-events-none absolute inset-x-0 top-3 z-[500] flex justify-center">
        <span className="rounded-full bg-card px-4 py-2 text-sm font-semibold shadow-soft">
          {listings.length} pickups nearby
        </span>
      </div>
      {sel && (
        <div className="absolute inset-x-3 bottom-3 z-[500] md:left-auto md:w-80">
          <ListingCard listing={sel} distance={sel.lat != null && sel.lng != null ? distanceKm(pos, { lat: sel.lat, lng: sel.lng }) : undefined} />
        </div>
      )}
    </div>
  );
}
