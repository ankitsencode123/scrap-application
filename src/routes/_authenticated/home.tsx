import { createFileRoute } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { Loader2 } from "lucide-react";
import { api, type ApiListing, type ApiStats, type Page } from "@/lib/api";
import { useAuth } from "@/hooks/use-auth";
import { SellerDashboard } from "@/components/dash/SellerDashboard";
import { CollectorDashboard } from "@/components/dash/CollectorDashboard";

export const Route = createFileRoute("/_authenticated/home")({
  head: () => ({
    meta: [
      { title: "Home — scrappe" },
      { name: "description", content: "Your scrap listings, offers, pickups and daily dashboard." },
      { property: "og:title", content: "Home — scrappe" },
      { property: "og:description", content: "Your scrap listings, offers, pickups and daily dashboard." },
    ],
  }),
  component: HomePage,
});

function HomePage() {
  const { profile, user } = useAuth();
  const [cat, setCat] = useState<string | null>(null);
  const [pos, setPos] = useState<{ lat: number; lng: number } | null>(null);

  const isCollector = profile?.role === "collector";

  // Request browser geolocation for distance calculation and map sorting
  useEffect(() => {
    navigator.geolocation?.getCurrentPosition(
      (p) => setPos({ lat: p.coords.latitude, lng: p.coords.longitude }),
      () => {
        // Geolocation fallback
      }
    );
  }, []);

  // Feed from backend API: category filtering & distance sorting happen server-side
  const { data: listings = [], isLoading: isLoadingListings } = useQuery({
    queryKey: ["listings", isCollector, cat, pos?.lat, pos?.lng, user?.id],
    enabled: !!profile,
    queryFn: async () =>
      (
        await api<Page<ApiListing>>("/listings", {
          query: {
            tab: isCollector ? "open" : "mine",
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

  if (isLoadingListings && listings.length === 0) {
    return (
      <div className="flex min-h-[40vh] flex-col items-center justify-center gap-3">
        <Loader2 className="size-8 animate-spin text-primary" />
        <p className="text-sm text-muted-foreground">Loading dashboard…</p>
      </div>
    );
  }

  return isCollector ? (
    <CollectorDashboard
      listings={listings}
      stats={stats}
      selectedCategory={cat}
      onSelectCategory={setCat}
    />
  ) : (
    <SellerDashboard listings={listings} stats={stats} />
  );
}
