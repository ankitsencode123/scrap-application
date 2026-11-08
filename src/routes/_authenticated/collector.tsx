import { createFileRoute } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";
import { useState, useEffect } from "react";
import { Loader2 } from "lucide-react";
import { api, type ApiListing, type ApiStats, type Page } from "@/lib/api";
import { useAuth } from "@/hooks/use-auth";
import { CollectorDashboard } from "@/components/dash/CollectorDashboard";

export const Route = createFileRoute("/_authenticated/collector")({
  head: () => ({
    meta: [
      { title: "Collector Dashboard — scrappe" },
      { name: "description", content: "Find nearby scrap listings, manage your route, and track daily earnings." },
      { property: "og:title", content: "Collector Dashboard — scrappe" },
      { property: "og:description", content: "Find nearby scrap listings, manage your route, and track daily earnings." },
    ],
  }),
  component: CollectorRoute,
});

function CollectorRoute() {
  const { profile, user } = useAuth();
  const [cat, setCat] = useState<string | null>(null);
  const [pos, setPos] = useState<{ lat: number; lng: number } | null>(null);

  useEffect(() => {
    navigator.geolocation?.getCurrentPosition(
      (p) => setPos({ lat: p.coords.latitude, lng: p.coords.longitude }),
      () => {}
    );
  }, []);

  const { data: listings = [], isLoading } = useQuery({
    queryKey: ["listings", "collector-view", cat, pos?.lat, pos?.lng, user?.id],
    enabled: !!profile,
    queryFn: async () =>
      (
        await api<Page<ApiListing>>("/listings", {
          query: {
            tab: "open",
            category: cat,
            lat: pos?.lat,
            lng: pos?.lng,
            sort: "nearest",
            limit: 50,
          },
        })
      ).items,
  });

  const { data: stats } = useQuery({
    queryKey: ["stats", user?.id],
    enabled: !!profile,
    queryFn: () => api<ApiStats>("/users/me/stats"),
  });

  if (isLoading && listings.length === 0) {
    return (
      <div className="flex min-h-[40vh] flex-col items-center justify-center gap-3">
        <Loader2 className="size-8 animate-spin text-primary" />
        <p className="text-sm text-muted-foreground">Loading collector dashboard…</p>
      </div>
    );
  }

  return (
    <CollectorDashboard
      listings={listings}
      stats={stats}
      selectedCategory={cat}
      onSelectCategory={setCat}
    />
  );
}
