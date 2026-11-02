import { createFileRoute, Link } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
import { supabase } from "@/integrations/supabase/client";
import { useAuth } from "@/hooks/use-auth";
import { ListingCard } from "@/components/ListingCard";
import { Button } from "@/components/ui/button";
import { CATEGORIES, distanceKm, formatINR } from "@/lib/scrap";
import { cn } from "@/lib/utils";

export const Route = createFileRoute("/_authenticated/home")({
  head: () => ({
    meta: [
      { title: "Home — scrappe" },
      { name: "description", content: "Your scrap listings, offers and nearby pickups." },
      { property: "og:title", content: "Home — scrappe" },
      { property: "og:description", content: "Your scrap listings, offers and nearby pickups." },
    ],
  }),
  component: HomePage,
});

function HomePage() {
  const { profile, user } = useAuth();
  const isCollector = profile?.account_type === "collector";
  const [cat, setCat] = useState<string | null>(null);
  const [tab, setTab] = useState<"open" | "mine">("open");
  const [pos, setPos] = useState<{ lat: number; lng: number } | null>(null);

  useEffect(() => {
    navigator.geolocation?.getCurrentPosition((p) => setPos({ lat: p.coords.latitude, lng: p.coords.longitude }));
  }, []);

  const { data: listings = [], isLoading } = useQuery({
    queryKey: ["listings", isCollector, tab, user?.id],
    enabled: !!profile,
    queryFn: async () => {
      let q = supabase.from("listings").select("*").order("created_at", { ascending: false }).limit(100);
      if (!isCollector) q = q.eq("seller_id", user!.id);
      else if (tab === "open") q = q.eq("status", "open");
      else q = q.eq("collector_id", user!.id);
      const { data, error } = await q;
      if (error) throw error;
      return data;
    },
  });

  const shown = useMemo(() => {
    const list = cat ? listings.filter((l) => l.category === cat) : listings;
    return list
      .map((l) => ({ l, d: pos && l.lat != null && l.lng != null ? distanceKm(pos, { lat: l.lat, lng: l.lng }) : undefined }))
      .sort((a, b) => (a.d ?? 1e9) - (b.d ?? 1e9));
  }, [listings, cat, pos]);

  const earned = listings.filter((l) => l.status === "completed").reduce((a, l) => a + Number(l.final_price ?? 0), 0);
  const active = listings.filter((l) => !["completed", "cancelled"].includes(l.status)).length;

  return (
    <div className="space-y-5">
      <div>
        <p className="text-sm text-muted-foreground">Namaste{profile?.full_name ? `, ${profile.full_name.split(" ")[0]}` : ""} 👋</p>
        <h1 className="text-2xl font-extrabold md:text-3xl">
          {isCollector ? "Scrap near you" : "Your scrap"}
        </h1>
      </div>

      <div className="grid grid-cols-2 gap-3">
        <div className="rounded-2xl bg-hero p-4 text-primary-foreground">
          <div className="text-xs opacity-75">{isCollector ? "Spent on pickups" : "Earned so far"}</div>
          <div className="font-display text-2xl font-extrabold">{formatINR(earned)}</div>
        </div>
        <div className="rounded-2xl border bg-card p-4">
          <div className="text-xs text-muted-foreground">Active</div>
          <div className="font-display text-2xl font-extrabold">{active}</div>
        </div>
      </div>

      {isCollector && (
        <div className="flex gap-2 rounded-xl bg-muted p-1">
          {(["open", "mine"] as const).map((t) => (
            <button key={t} onClick={() => setTab(t)}
              className={cn("flex-1 rounded-lg py-2 text-sm font-semibold", tab === t ? "bg-card shadow-sm" : "text-muted-foreground")}>
              {t === "open" ? "Available" : "My pickups"}
            </button>
          ))}
        </div>
      )}

      <div className="-mx-4 flex gap-2 overflow-x-auto px-4 pb-1">
        <Chip active={!cat} onClick={() => setCat(null)}>All</Chip>
        {CATEGORIES.map((c) => (
          <Chip key={c.id} active={cat === c.id} onClick={() => setCat(c.id)}>{c.emoji} {c.label}</Chip>
        ))}
      </div>

      {isLoading ? (
        <p className="text-muted-foreground">Loading…</p>
      ) : shown.length === 0 ? (
        <div className="rounded-3xl border border-dashed p-10 text-center">
          <div className="text-4xl">♻️</div>
          <p className="mt-2 font-semibold">{isCollector ? "No scrap posted here yet" : "You haven't posted anything yet"}</p>
          {!isCollector && <Button asChild variant="accent" className="mt-4"><Link to="/sell">Post your first scrap</Link></Button>}
        </div>
      ) : (
        <div className="grid gap-3 md:grid-cols-3 lg:grid-cols-4">
          {shown.map(({ l, d }) => <ListingCard key={l.id} listing={l} distance={d} />)}
        </div>
      )}
    </div>
  );
}

function Chip({ active, children, onClick }: { active: boolean; children: React.ReactNode; onClick: () => void }) {
  return (
    <button onClick={onClick}
      className={cn("shrink-0 rounded-full border px-3 py-1.5 text-sm font-medium transition",
        active ? "border-primary bg-primary text-primary-foreground" : "bg-card")}>
      {children}
    </button>
  );
}
