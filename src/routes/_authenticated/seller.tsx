import { createFileRoute } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";
import { Loader2 } from "lucide-react";
import { api, type ApiListing, type ApiStats, type Page } from "@/lib/api";
import { useAuth } from "@/hooks/use-auth";
import { SellerDashboard } from "@/components/dash/SellerDashboard";

export const Route = createFileRoute("/_authenticated/seller")({
  head: () => ({
    meta: [
      { title: "Seller Dashboard — scrappe" },
      { name: "description", content: "Track your scrap pickups, earnings, live rates and environmental impact." },
      { property: "og:title", content: "Seller Dashboard — scrappe" },
      { property: "og:description", content: "Track your scrap pickups, earnings, live rates and environmental impact." },
    ],
  }),
  component: SellerRoute,
});

function SellerRoute() {
  const { profile, user } = useAuth();

  const { data: listings = [], isLoading } = useQuery({
    queryKey: ["listings", "mine", user?.id],
    enabled: !!profile,
    queryFn: async () =>
      (
        await api<Page<ApiListing>>("/listings", {
          query: {
            tab: "mine",
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
        <p className="text-sm text-muted-foreground">Loading seller dashboard…</p>
      </div>
    );
  }

  return <SellerDashboard listings={listings} stats={stats} />;
}
