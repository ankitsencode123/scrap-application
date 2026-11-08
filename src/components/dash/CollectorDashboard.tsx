import { Link } from "@tanstack/react-router";
import { useState } from "react";
import {
  Check,
  Clock,
  Filter,
  MapPin,
  Navigation,
  Package,
  Recycle,
  Route as RouteIcon,
  Star,
  Target,
  TrendingUp,
  Wallet,
  X,
} from "lucide-react";
import { toast } from "sonner";
import { Bars, Card, SectionTitle, Stat, StatusPill } from "./Shell";
import type { ApiListing, ApiStats } from "@/lib/api";
import { useAuth } from "@/hooks/use-auth";
import { categoryOf, formatINR } from "@/lib/scrap";

interface CollectorDashboardProps {
  listings: ApiListing[];
  stats?: ApiStats;
  selectedCategory: string | null;
  onSelectCategory: (cat: string | null) => void;
}

export function CollectorDashboard({
  listings,
  stats,
  selectedCategory,
  onSelectCategory,
}: CollectorDashboardProps) {
  const { profile, user } = useAuth();
  const [dismissedIds, setDismissedIds] = useState<string[]>([]);
  const [sortNearest, setSortNearest] = useState(true);

  // Real database stats
  const totalPaidNum = stats?.total_amount ?? 0;
  const pickupsDoneNum = stats?.completed_pickups ?? 0;
  const activePickupsNum = stats?.active_pickups ?? 0;
  const recycledKgNum = stats?.recycled_kg ?? 0;
  const openBidsNum = stats?.open_items ?? 0;

  const categories = ["All", "Paper", "Metal", "Plastic", "E‑waste"];

  // Open scrap listings from database
  const openListings = listings.filter(
    (l) => l.status === "posted" && !dismissedIds.includes(l.id)
  );

  // Filter by category if selected
  const filteredListings = openListings.filter((l) => {
    if (!selectedCategory || selectedCategory === "All") return true;
    const catLower = selectedCategory.toLowerCase();
    return (
      l.category.toLowerCase().includes(catLower) ||
      l.category_label.toLowerCase().includes(catLower) ||
      l.title.toLowerCase().includes(catLower)
    );
  });

  // Collector's assigned route pickups from DB
  const routeListings = listings.filter(
    (l) =>
      l.is_assigned_collector ||
      (l.collector?.id === user?.id &&
        ["offer_accepted", "on_the_way", "arrived"].includes(l.status))
  );

  const handleDismiss = (id: string, title: string) => {
    setDismissedIds((prev) => [...prev, id]);
    toast.info(`Listing "${title}" skipped from your feed.`);
  };

  const handleSellToYard = () => {
    if (recycledKgNum <= 0) {
      toast.info("Your van inventory is empty. Complete pickups to collect scrap for yard delivery!");
      return;
    }
    toast.success(`Wholesale yard dispatch slip generated for ${recycledKgNum} kg of collected scrap!`);
  };

  const cityLabel = profile?.city || "Active Area";
  const openCount = openListings.length;
  const estimatedTotalValue = openListings.reduce(
    (sum, l) => sum + (l.expected_price || 0),
    0
  );

  // Real market collection volume capture
  const totalMarketVolume = totalPaidNum + estimatedTotalValue;
  const marketCapturePercent =
    totalMarketVolume > 0 ? Math.min(100, Math.round((totalPaidNum / totalMarketVolume) * 100)) : 100;

  // Monthly collection value from real database query
  const monthlyData = stats?.monthly_earnings ?? [];
  const barLabels = monthlyData.length > 0 ? monthlyData.map((m) => m.month) : ["—"];
  const barValues = monthlyData.length > 0 ? monthlyData.map((m) => m.amount) : [0];

  // Real van inventory from database
  const vanItems = stats?.van_inventory ?? [];

  const formatDistance = (l: ApiListing) => {
    if (l.distance_km == null) {
      return l.address ? l.address.split(",")[0] : "Nearby";
    }
    if (l.distance_km < 1) {
      return `${Math.round(l.distance_km * 1000)} m`;
    }
    if (l.distance_km < 100) {
      return `${l.distance_km.toFixed(1)} km`;
    }
    if (l.distance_km < 500) {
      return `${Math.round(l.distance_km)} km`;
    }
    // If coordinates are across the globe / dummy test locations, show city instead of thousands of km
    return l.address ? l.address.split(",").slice(-1)[0].trim() : "Regional";
  };

  return (
    <div className="space-y-6">
      <section className="grid gap-6 lg:grid-cols-[1.5fr_1fr]">
        {/* Map Hero */}
        <div className="bg-hero relative min-h-[320px] overflow-hidden rounded-3xl p-6 text-primary-foreground shadow-lift animate-rise">
          <div className="grain absolute inset-0" />
          <svg
            className="absolute inset-0 h-full w-full opacity-20"
            preserveAspectRatio="none"
            viewBox="0 0 100 100"
          >
            <path d="M0 40 Q30 30 50 45 T100 35" stroke="currentColor" strokeWidth=".6" fill="none" />
            <path d="M10 100 Q30 60 45 50 T90 0" stroke="currentColor" strokeWidth=".6" fill="none" />
            <path d="M0 75 L100 70" stroke="currentColor" strokeWidth=".4" fill="none" />
            <path d="M48 45 L58 22 M48 45 L70 58" stroke="var(--sun)" strokeWidth=".8" strokeDasharray="2 1.5" fill="none" />
          </svg>

          {/* Current collector beacon */}
          <span className="absolute" style={{ left: "50%", top: "45%" }}>
            <span className="relative flex h-5 w-5">
              <span className="animate-ping-soft absolute h-5 w-5 rounded-full bg-sun" />
              <span className="h-5 w-5 rounded-full border-2 border-primary-foreground bg-sun" />
            </span>
          </span>

          <div className="relative flex h-full flex-col justify-between">
            <div>
              <p className="text-sm opacity-80">You're online · {cityLabel}</p>
              <h1 className="mt-1 text-3xl font-extrabold sm:text-4xl">
                {openCount} {openCount === 1 ? "listing" : "listings"} available
              </h1>
              <p className="mt-1 text-sm opacity-80">
                {openCount > 0
                  ? `Estimated value ${formatINR(estimatedTotalValue)} in open scrap posts`
                  : "No open scrap listings posted right now. Check back soon!"}
              </p>
            </div>

            <div className="mt-20 flex flex-wrap gap-2">
              {categories.map((c) => {
                const isSelected =
                  c === "All"
                    ? !selectedCategory
                    : selectedCategory?.toLowerCase().includes(c.toLowerCase());
                return (
                  <button
                    key={c}
                    type="button"
                    onClick={() => onSelectCategory(c === "All" ? null : c)}
                    className={`rounded-full px-4 py-2 text-sm font-semibold backdrop-blur transition ${
                      isSelected
                        ? "bg-sun text-sun-foreground shadow-soft"
                        : "bg-primary-foreground/15 hover:bg-primary-foreground/25"
                    }`}
                  >
                    {c}
                  </button>
                );
              })}
            </div>
          </div>
        </div>

        {/* Real Collection Overview Card */}
        <Card className="flex flex-col">
          <SectionTitle title="Collection overview" />
          <div className="flex items-center gap-5">
            <div
              className="grid h-32 w-32 shrink-0 place-items-center rounded-full"
              style={{
                background: `conic-gradient(var(--sun) 0 ${marketCapturePercent}%, var(--muted) ${marketCapturePercent}% 100%)`,
              }}
            >
              <div className="grid h-24 w-24 place-items-center rounded-full bg-card text-center">
                <div>
                  <p className="font-display text-2xl font-extrabold">{formatINR(totalPaidNum)}</p>
                  <p className="text-[10px] text-muted-foreground">total paid out</p>
                </div>
              </div>
            </div>
            <div className="space-y-3 text-sm">
              <p className="flex items-center gap-2">
                <Target className="h-4 w-4 text-primary shrink-0" />
                <span><b>{pickupsDoneNum}</b> completed {pickupsDoneNum === 1 ? "pickup" : "pickups"}</span>
              </p>
              <p className="flex items-center gap-2">
                <Package className="h-4 w-4 text-info shrink-0" />
                <span><b>{recycledKgNum} kg</b> scrap collected</span>
              </p>
              <p className="flex items-center gap-2">
                <RouteIcon className="h-4 w-4 text-sun shrink-0" />
                <span><b>{activePickupsNum}</b> active on route</span>
              </p>
            </div>
          </div>
          <div className="mt-auto grid grid-cols-2 gap-3 pt-5">
            <div className="rounded-2xl bg-primary-soft p-3">
              <p className="text-xs text-muted-foreground">Pending Bids</p>
              <p className="font-display text-lg font-bold text-primary">
                {openBidsNum} active
              </p>
            </div>
            <div className="rounded-2xl bg-sun-soft p-3">
              <p className="text-xs text-muted-foreground">Reputation</p>
              <p className="font-display text-lg font-bold text-sun-foreground">
                {profile?.rating_count && profile.rating_count > 0
                  ? `${profile.rating_avg.toFixed(1)} ★ (${profile.rating_count})`
                  : "Verified"}
              </p>
            </div>
          </div>
        </Card>
      </section>

      {/* Real Collector Stats from Database (No fake multipliers) */}
      <section className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <Stat
          label="Total paid out"
          value={formatINR(totalPaidNum)}
          delta={pickupsDoneNum > 0 ? `+${pickupsDoneNum} paid` : undefined}
          icon={Wallet}
        />
        <Stat
          label="Pickups done"
          value={String(pickupsDoneNum)}
          delta={activePickupsNum > 0 ? `${activePickupsNum} active` : undefined}
          icon={Package}
          tone="sun"
        />
        <Stat
          label="Scrap collected"
          value={`${recycledKgNum} kg`}
          icon={Recycle}
          tone="info"
        />
        <Stat
          label="Available offers"
          value={`${openCount} posts`}
          delta={estimatedTotalValue > 0 ? formatINR(estimatedTotalValue) : undefined}
          icon={TrendingUp}
        />
      </section>

      {/* Real Requests & Route */}
      <section className="grid gap-6 lg:grid-cols-3">
        {/* Real Open Requests Feed from DB */}
        <Card className="lg:col-span-2">
          <div className="mb-4 flex items-center justify-between">
            <h3 className="text-lg font-bold">New requests</h3>
            <button
              type="button"
              onClick={() => setSortNearest(!sortNearest)}
              className="flex items-center gap-1.5 rounded-full border px-3 py-1.5 text-sm transition hover:bg-muted"
            >
              <Filter className="h-3.5 w-3.5" /> {sortNearest ? "Nearest" : "All"}
            </button>
          </div>

          {filteredListings.length > 0 ? (
            <div className="grid gap-3 sm:grid-cols-2">
              {filteredListings.map((l) => (
                <div
                  key={l.id}
                  className="group rounded-2xl border bg-background p-4 transition hover:-translate-y-0.5 hover:shadow-lift flex flex-col justify-between"
                >
                  <div>
                    <div className="flex items-start justify-between gap-2">
                      <div className="flex min-w-0 items-center gap-3">
                        <div className="grid h-10 w-10 shrink-0 place-items-center rounded-full bg-primary text-sm font-bold text-primary-foreground">
                          {l.seller?.display_name ? l.seller.display_name.charAt(0).toUpperCase() : "S"}
                        </div>
                        <div className="min-w-0">
                          <p className="truncate font-semibold">{l.seller?.display_name || "Seller"}</p>
                          <p className="text-xs text-muted-foreground truncate">
                            <MapPin className="mr-0.5 inline h-3 w-3" />
                            {formatDistance(l)} ·{" "}
                            <Clock className="mr-0.5 inline h-3 w-3" />
                            {l.pickup_time_slot || "Flexible"}
                          </p>
                        </div>
                      </div>
                      <span className="shrink-0 rounded-full bg-sun px-2 py-0.5 text-[10px] font-bold text-sun-foreground">
                        OPEN
                      </span>
                    </div>

                    <div className="mt-3 flex flex-wrap gap-1.5">
                      <span className="rounded-full bg-muted px-2.5 py-1 text-xs">{l.title}</span>
                      <span className="rounded-full bg-info-soft px-2.5 py-1 text-xs text-info font-medium">
                        ~{l.weight_kg} kg
                      </span>
                    </div>
                  </div>

                  <div className="mt-4 flex items-center justify-between pt-2">
                    <div>
                      <p className="text-[11px] text-muted-foreground">Expected price</p>
                      <p className="font-display text-xl font-extrabold">{formatINR(l.expected_price)}</p>
                    </div>
                    <div className="flex gap-2">
                      <button
                        type="button"
                        onClick={() => handleDismiss(l.id, l.title)}
                        className="grid h-9 w-9 place-items-center rounded-full border text-muted-foreground hover:text-destructive transition"
                        title="Skip"
                      >
                        <X className="h-4 w-4" />
                      </button>
                      <Link
                        to="/listings/$id"
                        params={{ id: l.id }}
                        className="flex items-center gap-1 rounded-full bg-primary px-4 py-2 text-sm font-semibold text-primary-foreground shadow-soft transition hover:opacity-90"
                      >
                        <Check className="h-4 w-4" /> Accept / Bid
                      </Link>
                    </div>
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <div className="py-12 text-center">
              <Package className="mx-auto h-12 w-12 text-muted-foreground/40" />
              <p className="mt-3 text-base font-semibold">No scrap requests available right now</p>
              <p className="text-sm text-muted-foreground mt-1 max-w-sm mx-auto">
                When sellers post household scrap or appliances in your area, their listings will appear here automatically.
              </p>
              <Link
                to="/map"
                className="mt-4 inline-flex items-center gap-1.5 rounded-full bg-primary px-5 py-2 text-sm font-semibold text-primary-foreground shadow-soft transition hover:opacity-90"
              >
                <MapPin className="h-4 w-4" /> View Map
              </Link>
            </div>
          )}
        </Card>

        {/* Real Assigned Route Stops from DB */}
        <Card>
          <div className="mb-4 flex items-center justify-between">
            <h3 className="text-lg font-bold">Today's route</h3>
            <Link
              to="/map"
              className="flex items-center gap-1 rounded-full bg-sun px-3 py-1.5 text-xs font-bold text-sun-foreground shadow-soft transition hover:brightness-105"
            >
              <Navigation className="h-3.5 w-3.5" /> Start map
            </Link>
          </div>

          {routeListings.length > 0 ? (
            <ol className="relative space-y-5 border-l-2 border-dashed border-border pl-6">
              {routeListings.map((s, i) => (
                <li key={s.id} className="relative">
                  <span
                    className={`absolute -left-[33px] grid h-6 w-6 place-items-center rounded-full text-[11px] font-bold ${
                      s.status === "completed"
                        ? "bg-primary text-primary-foreground"
                        : s.status === "on_the_way"
                          ? "bg-sun text-sun-foreground"
                          : "bg-muted text-muted-foreground"
                    }`}
                  >
                    {s.status === "completed" ? "✓" : i + 1}
                  </span>
                  <div className="flex items-start justify-between gap-2">
                    <div className="min-w-0">
                      <Link
                        to="/listings/$id"
                        params={{ id: s.id }}
                        className="truncate font-semibold hover:underline block"
                      >
                        {s.title}
                      </Link>
                      <p className="truncate text-xs text-muted-foreground">
                        {s.address} · {s.pickup_time_slot || "Scheduled"}
                      </p>
                    </div>
                    <StatusPill s={s.status} />
                  </div>
                </li>
              ))}
            </ol>
          ) : (
            <div className="py-6 text-center">
              <RouteIcon className="mx-auto h-8 w-8 text-muted-foreground/40" />
              <p className="mt-2 text-sm font-semibold">No active stops on your route</p>
              <p className="text-xs text-muted-foreground mt-0.5">
                Accept open scrap requests to plan your pickup route.
              </p>
            </div>
          )}
        </Card>
      </section>

      {/* Real Monthly Collection Value & Real Van Inventory */}
      <section className="grid gap-6 lg:grid-cols-3">
        <Card className="lg:col-span-2">
          <div className="mb-2">
            <h3 className="text-lg font-bold">Collection value</h3>
            <p className="text-sm text-muted-foreground">Amount paid to sellers per month from database</p>
          </div>
          <div className="mt-6">
            <Bars data={barValues} labels={barLabels} />
          </div>
        </Card>

        {/* Real Van Inventory from DB */}
        <Card>
          <SectionTitle title="Inventory in van" action="Sell to yard" onAction={handleSellToYard} />
          {vanItems.length > 0 ? (
            <div className="space-y-4">
              {vanItems.map((x) => {
                const catInfo = categoryOf(x.category);
                const pct =
                  recycledKgNum > 0
                    ? Math.min(100, Math.round((x.weight_kg / recycledKgNum) * 100))
                    : 100;
                return (
                  <div key={x.category}>
                    <div className="mb-1.5 flex justify-between items-center text-sm">
                      <span className="flex items-center gap-1.5 font-medium">
                        <span>{catInfo.emoji}</span>
                        <span>{catInfo.label}</span>
                      </span>
                      <span className="font-bold">{x.weight_kg} kg</span>
                    </div>
                    <div className="h-2 overflow-hidden rounded-full bg-muted">
                      <div
                        className="h-full rounded-full bg-primary transition-all duration-300"
                        style={{ width: `${pct}%` }}
                      />
                    </div>
                  </div>
                );
              })}
              <p className="text-xs text-muted-foreground pt-1">
                Total collected: <b>{recycledKgNum} kg</b>
              </p>
            </div>
          ) : (
            <div className="py-6 text-center">
              <Package className="mx-auto h-8 w-8 text-muted-foreground/40" />
              <p className="mt-2 text-sm font-semibold">Van inventory is empty</p>
              <p className="text-xs text-muted-foreground mt-0.5">
                Completed pickups will log collected kilograms by material here.
              </p>
            </div>
          )}
        </Card>
      </section>
    </div>
  );
}
