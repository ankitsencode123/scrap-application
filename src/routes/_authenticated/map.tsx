import { createFileRoute } from "@tanstack/react-router";
import { ClientOnly } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";
import { lazy, Suspense, useCallback, useEffect, useState } from "react";
import { Loader2, LocateFixed, Navigation, MapPin } from "lucide-react";
import { toast } from "sonner";
import { api, type ApiListing, type Page } from "@/lib/api";
import { ListingCard } from "@/components/ListingCard";
import { Button } from "@/components/ui/button";

const ScrapMap = lazy(() => import("@/components/ScrapMap"));
const MapView = lazy(() => import("@/components/map/MapView"));

export const Route = createFileRoute("/_authenticated/map")({
  head: () => ({
    meta: [
      { title: "Live Route Tracking & Scrap Map — scrappe" },
      { name: "description", content: "Track your scrap collector live on the map with ETA, route, nearby pickups and collection heatmaps." },
      { property: "og:title", content: "Live Route Tracking & Scrap Map — scrappe" },
      { property: "og:description", content: "Track your scrap collector live with ETA, route and collection heatmaps." },
    ],
  }),
  component: MapPage,
});

type LocationStrategy = {
  ladder: { name: string; options: PositionOptions }[];
  watch: PositionOptions;
  send_interval_s: { idle: number; tracking: number };
  default_center: { lat: number; lng: number };
};

type ResolveOut = {
  lat: number;
  lng: number;
  accuracy_m: number;
  source: string;
  confidence: number;
  zoom: number;
  hint: string | null;
  needs_confirmation: boolean;
};

const DEFAULT = { lat: 28.6139, lng: 77.209 };

function MapPage() {
  const [mode, setMode] = useState<"live" | "listings">("live");
  const [pos, setPos] = useState(DEFAULT);
  const [locating, setLocating] = useState(false);
  const [selected, setSelected] = useState<string | null>(null);

  const locate = useCallback(async () => {
    setLocating(true);
    try {
      let cfg: LocationStrategy | null = null;
      try {
        cfg = await api<LocationStrategy>("/map/location/strategy");
      } catch {
        // Fallback strategy if offline
      }

      const ladder: { name: string; options: PositionOptions }[] = cfg?.ladder ?? [
        { name: "cached", options: { enableHighAccuracy: false, timeout: 4000, maximumAge: 600000 } },
        { name: "network", options: { enableHighAccuracy: false, timeout: 10000, maximumAge: 120000 } },
        { name: "gps", options: { enableHighAccuracy: true, timeout: 20000, maximumAge: 15000 } },
      ];

      let lastErr: { code: number; message: string } | null = null;
      let resolved = false;

      if (typeof navigator !== "undefined" && navigator.geolocation) {
        for (const step of ladder) {
          try {
            const p = await new Promise<GeolocationPosition>((ok, no) =>
              navigator.geolocation.getCurrentPosition(ok, no, step.options)
            );
            const out = await api<ResolveOut>("/map/location/resolve", {
              method: "POST",
              body: {
                fix: {
                  lat: p.coords.latitude,
                  lng: p.coords.longitude,
                  accuracy_m: p.coords.accuracy,
                  source: p.coords.accuracy > 200 ? "network" : "gps",
                  captured_at: p.timestamp,
                },
              },
            });
            setPos({ lat: out.lat, lng: out.lng });
            resolved = true;
            toast.success("Updated location");
            break;
          } catch (e: any) {
            lastErr = { code: e?.code ?? 2, message: e?.message ?? "Location error" };
          }
        }
      }

      if (!resolved) {
        const out = await api<ResolveOut>("/map/location/resolve", {
          method: "POST",
          body: { client_error: lastErr ?? { code: 2, message: "No geolocation provider" } },
        });
        setPos({ lat: out.lat, lng: out.lng });
        if (out.hint) toast.info(out.hint);
      }
    } catch {
      // Graceful fallback to default center
    } finally {
      setLocating(false);
    }
  }, []);

  useEffect(() => {
    locate();
  }, [locate]);

  const { data: listings = [], isLoading } = useQuery({
    queryKey: ["map-listings", pos.lat, pos.lng],
    queryFn: async () =>
      (
        await api<Page<ApiListing>>("/listings", {
          query: { tab: "open", lat: pos.lat, lng: pos.lng, sort: "nearest", limit: 100 },
        })
      ).items.filter((l) => l.latitude != null && l.longitude != null),
  });
  const onSelect = useCallback((id: string) => setSelected(id), []);
  const sel = listings.find((l) => l.id === selected);

  if (mode === "live") {
    return (
      <div className="-mx-4 -my-5 relative h-[calc(100dvh-3.5rem-5rem)] md:mx-0 md:my-0 md:h-[calc(100dvh-7rem)] md:overflow-hidden md:rounded-3xl md:border">
        <ClientOnly fallback={<div className="size-full animate-pulse rounded-3xl bg-muted" />}>
          <Suspense fallback={<div className="size-full animate-pulse rounded-3xl bg-muted" />}>
            <MapView onSwitchToListings={() => setMode("listings")} />
          </Suspense>
        </ClientOnly>
      </div>
    );
  }

  return (
    <div className="-mx-4 -my-5 relative h-[calc(100dvh-3.5rem-5rem)] md:mx-0 md:my-0 md:h-[calc(100dvh-7rem)] md:overflow-hidden md:rounded-3xl md:border">
      <ClientOnly fallback={<div className="size-full bg-muted" />}>
        <Suspense fallback={<div className="size-full bg-muted" />}>
          <ScrapMap listings={listings} center={pos} onSelect={onSelect} />
        </Suspense>
      </ClientOnly>
      <div className="pointer-events-none absolute inset-x-0 top-3 z-[500] flex items-center justify-between px-3">
        <div className="pointer-events-auto flex items-center gap-2">
          <Button
            size="sm"
            variant="outline"
            className="rounded-full bg-card shadow-soft hover:bg-muted text-xs font-semibold"
            onClick={() => setMode("live")}
            title="Switch back to live vehicle and route tracking"
          >
            <Navigation className="mr-1.5 size-3.5 text-primary" />
            Live Route Tracking
          </Button>
          <span className="flex items-center gap-2 rounded-full bg-card px-4 py-1.5 text-xs font-semibold shadow-soft">
            {isLoading && <Loader2 className="size-3.5 animate-spin text-primary" />}
            {listings.length} pickups nearby
          </span>
        </div>
        <Button
          size="icon"
          variant="secondary"
          className="pointer-events-auto size-10 rounded-full bg-card shadow-soft hover:bg-muted"
          onClick={locate}
          disabled={locating}
          title="Locate me"
        >
          {locating ? <Loader2 className="size-4 animate-spin text-primary" /> : <LocateFixed className="size-4" />}
        </Button>
      </div>
      {sel && (
        <div className="absolute inset-x-3 bottom-3 z-[500] md:left-auto md:w-80">
          <ListingCard listing={sel} distance={sel.distance_km} />
        </div>
      )}
    </div>
  );
}
