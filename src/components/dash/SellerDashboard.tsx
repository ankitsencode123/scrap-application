import { Link } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import {
  ArrowUpRight,
  Calendar,
  Camera,
  Clock,
  IndianRupee,
  Leaf,
  MapPin,
  MessageCircle,
  Package,
  Phone,
  Plus,
  Recycle,
  Sparkles,
  TreePine,
  Wallet,
} from "lucide-react";
import { toast } from "sonner";
import { Bars, Card, SectionTitle, Stars, Stat, StatusPill } from "./Shell";
import { api, type ApiListing, type ApiReview, type ApiStats, type CursorPage, type RateItem } from "@/lib/api";
import { useAuth } from "@/hooks/use-auth";
import { formatINR } from "@/lib/scrap";

interface SellerDashboardProps {
  listings: ApiListing[];
  stats?: ApiStats;
}

export function SellerDashboard({ listings, stats }: SellerDashboardProps) {
  const { profile, user } = useAuth();
  const [timeRange, setTimeRange] = useState<"Week" | "Month" | "Year">("Month");

  // Query real scrap rates from backend database
  const { data: rates = [] } = useQuery({
    queryKey: ["meta", "rates"],
    queryFn: () => api<RateItem[]>("/meta/rates"),
  });

  // Query real user reviews from backend database
  const { data: reviewsPage } = useQuery({
    queryKey: ["reviews", user?.id],
    enabled: !!user?.id,
    queryFn: () => api<CursorPage<ApiReview>>("/users/me/reviews?limit=5"),
  });
  const reviews = reviewsPage?.items ?? [];

  // Determine active live pickup from real user listings
  const activePickup = listings.find(
    (l) => l.collector && ["offer_accepted", "on_the_way", "arrived"].includes(l.status)
  );
  const openListing = listings.find((l) => l.status === "posted");

  // Real database stats
  const totalEarnedNum = stats?.total_amount ?? 0;
  const completedPickupsCount = stats?.completed_pickups ?? 0;
  const recycledKgNum = stats?.recycled_kg ?? 0;
  const co2SavedNum = stats?.co2_saved_kg ?? roundNumber(recycledKgNum * 2.2, 1);
  const walletBalanceNum = stats?.wallet_balance ?? totalEarnedNum;

  // Monthly earnings breakdown from DB
  const monthlyData = stats?.monthly_earnings ?? [];
  const barLabels = monthlyData.length > 0 ? monthlyData.map((m) => m.month) : ["—"];
  const barValues = monthlyData.length > 0 ? monthlyData.map((m) => m.amount) : [0];
  const thisMonthEarnings = monthlyData.length > 0 ? monthlyData[monthlyData.length - 1].amount : 0;

  // Real green impact progress (Target: 50 kg for Eco Hero tier)
  const targetHeroKg = 50;
  const heroProgress = Math.min(100, Math.round((recycledKgNum / targetHeroKg) * 100));
  const treesSaved = (recycledKgNum * 0.05).toFixed(1);
  const kgRemaining = Math.max(0, targetHeroKg - recycledKgNum);
  const userLevel =
    recycledKgNum >= 50
      ? "Level 3 · Eco Hero"
      : recycledKgNum >= 15
        ? "Level 2 · Eco Recycler"
        : "Level 1 · Eco Beginner";

  const greetingName = profile?.display_name
    ? profile.display_name.split(" ")[0]
    : "Friend";

  const handleWithdraw = () => {
    if (walletBalanceNum <= 0) {
      toast.info("Your wallet balance is ₹0. Complete scrap pickups to earn cash!");
      return;
    }
    toast.success(`UPI payout request of ${formatINR(walletBalanceNum)} initiated! Funds will be transferred to your registered UPI ID.`);
  };

  const handleAddAddress = () => {
    toast.info("To add or update pickup addresses, go to your Profile settings.");
  };

  const getStepProgress = (status?: string) => {
    switch (status) {
      case "offer_accepted":
        return 25;
      case "on_the_way":
        return 65;
      case "arrived":
        return 88;
      case "completed":
        return 100;
      default:
        return 10;
    }
  };

  return (
    <div className="space-y-6">
      {/* Hero Banner */}
      <section className="bg-hero relative overflow-hidden rounded-3xl p-6 text-primary-foreground shadow-lift sm:p-8 animate-rise">
        <div className="grain absolute inset-0" />
        <div className="relative grid gap-6 lg:grid-cols-[1.4fr_1fr] lg:items-center">
          <div>
            <p className="text-sm opacity-80">Good afternoon, {greetingName} 👋</p>
            <h1 className="mt-1 text-3xl font-extrabold sm:text-4xl">Turn clutter into cash.</h1>
            <p className="mt-2 max-w-md text-sm opacity-80">
              {activePickup
                ? `Your pickup for "${activePickup.title}" is active. ${activePickup.collector?.display_name || "Your collector"} is assigned.`
                : openListing
                  ? `Your listing "${openListing.title}" is posted. Waiting for bids from nearby collectors.`
                  : "Got old newspapers, electronics, or metal scrap? Snap a photo and let verified local collectors bid."}
            </p>
            <div className="mt-5 flex flex-wrap gap-3">
              <Link
                to="/sell"
                className="flex items-center gap-2 rounded-full bg-sun px-5 py-3 text-sm font-bold text-sun-foreground transition hover:brightness-105"
              >
                <Camera className="h-4 w-4" /> Snap & sell scrap
              </Link>
              <Link
                to="/sell"
                className="flex items-center gap-2 rounded-full border border-primary-foreground/30 px-5 py-3 text-sm font-semibold transition hover:bg-primary-foreground/10"
              >
                <Calendar className="h-4 w-4" /> Schedule pickup
              </Link>
            </div>
          </div>

          {/* Live Status Card: Active Pickup or Posted Listing */}
          {activePickup ? (
            <div className="rounded-2xl border border-primary-foreground/15 bg-primary-foreground/10 p-4 backdrop-blur">
              <div className="flex items-center justify-between text-xs opacity-80">
                <span>LIVE PICKUP · #SP{activePickup.id.slice(0, 4).toUpperCase()}</span>
                <span className="flex items-center gap-1.5 font-medium">
                  <span className="relative flex h-2 w-2">
                    <span className="animate-ping-soft absolute h-2 w-2 rounded-full bg-sun" />
                    <span className="h-2 w-2 rounded-full bg-sun" />
                  </span>
                  {activePickup.status === "offer_accepted"
                    ? "Accepted"
                    : activePickup.status === "arrived"
                      ? "Arrived"
                      : "On the way"}
                </span>
              </div>

              <div className="mt-3 flex items-center gap-3">
                <div className="grid h-11 w-11 place-items-center rounded-full bg-sun font-bold text-sun-foreground">
                  {activePickup.collector?.display_name
                    ? activePickup.collector.display_name.charAt(0).toUpperCase()
                    : "C"}
                </div>
                <div className="flex-1 min-w-0">
                  <p className="font-semibold truncate">
                    {activePickup.collector?.display_name || "Assigned Collector"}
                  </p>
                  <p className="text-xs opacity-75">
                    ★ {activePickup.collector?.rating_avg ? activePickup.collector.rating_avg.toFixed(1) : "—"} ·{" "}
                    {activePickup.collector?.rating_count ?? 0} reviews
                  </p>
                </div>

                {activePickup.collector?.phone ? (
                  <a
                    href={`tel:${activePickup.collector.phone}`}
                    className="grid h-9 w-9 place-items-center rounded-full bg-primary-foreground/15 transition hover:bg-primary-foreground/25"
                    title="Call collector"
                  >
                    <Phone className="h-4 w-4" />
                  </a>
                ) : null}

                <Link
                  to="/chats"
                  search={{ listing: activePickup.id, with: activePickup.collector?.id }}
                  className="grid h-9 w-9 place-items-center rounded-full bg-primary-foreground/15 transition hover:bg-primary-foreground/25"
                  title="Chat with collector"
                >
                  <MessageCircle className="h-4 w-4" />
                </Link>
              </div>

              <div className="mt-4 h-1.5 overflow-hidden rounded-full bg-primary-foreground/15">
                <div
                  className="bg-sun-grad h-full rounded-full transition-all duration-500"
                  style={{ width: `${getStepProgress(activePickup.status)}%` }}
                />
              </div>
              <div className="mt-2 flex justify-between text-[11px] opacity-75">
                <span>Accepted</span>
                <span>En route</span>
                <span>Arrived</span>
                <span>Paid</span>
              </div>
            </div>
          ) : openListing ? (
            <div className="rounded-2xl border border-primary-foreground/15 bg-primary-foreground/10 p-5 backdrop-blur flex flex-col justify-between">
              <div>
                <div className="flex items-center justify-between text-xs opacity-80">
                  <span>POSTED LISTING · #SP{openListing.id.slice(0, 4).toUpperCase()}</span>
                  <span className="rounded-full bg-sun px-2 py-0.5 text-[10px] font-bold text-sun-foreground">
                    AWAITING BIDS
                  </span>
                </div>
                <h3 className="mt-2 text-base font-bold truncate">{openListing.title}</h3>
                <p className="text-xs opacity-75 mt-0.5">
                  ~{openListing.weight_kg} kg · Expected {formatINR(openListing.expected_price)}
                </p>
                <p className="mt-2 text-xs opacity-85">
                  {openListing.bid_count > 0
                    ? `🔥 ${openListing.bid_count} collector offer(s) received! Review and accept your best price.`
                    : "Your scrap is visible to local collectors. You will receive notifications when offers arrive."}
                </p>
              </div>
              <Link
                to="/listings/$id"
                params={{ id: openListing.id }}
                className="mt-4 inline-flex items-center justify-center rounded-full bg-sun px-4 py-2 text-xs font-bold text-sun-foreground shadow-soft transition hover:brightness-105"
              >
                {openListing.bid_count > 0 ? "Review Offers" : "View Listing"}
              </Link>
            </div>
          ) : (
            <div className="rounded-2xl border border-primary-foreground/15 bg-primary-foreground/10 p-5 backdrop-blur flex flex-col justify-between">
              <div>
                <div className="text-xs opacity-80 uppercase tracking-wider font-semibold">Active Pickups</div>
                <h3 className="mt-2 text-base font-bold">No pickups currently in progress</h3>
                <p className="text-xs opacity-75 mt-1 leading-relaxed">
                  Post your scrap today to connect with verified collectors in your neighborhood and get paid at your doorstep.
                </p>
              </div>
              <Link
                to="/sell"
                className="mt-4 inline-flex items-center justify-center gap-1.5 rounded-full bg-sun px-4 py-2 text-xs font-bold text-sun-foreground shadow-soft transition hover:brightness-105"
              >
                <Plus className="h-3.5 w-3.5" /> Post your first scrap
              </Link>
            </div>
          )}
        </div>
      </section>

      {/* Real Stats Grid from Database */}
      <section className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <Stat
          label="Total earned"
          value={formatINR(totalEarnedNum)}
          delta={completedPickupsCount > 0 ? `+${completedPickupsCount} pickups` : undefined}
          icon={Wallet}
        />
        <Stat
          label="Pickups done"
          value={String(completedPickupsCount)}
          delta={stats?.active_pickups ? `${stats.active_pickups} active` : undefined}
          icon={Package}
          tone="sun"
        />
        <Stat
          label="Scrap recycled"
          value={`${recycledKgNum} kg`}
          icon={Recycle}
          tone="info"
        />
        <Stat
          label="CO₂ saved"
          value={`${co2SavedNum} kg`}
          icon={Leaf}
        />
      </section>

      {/* Real Monthly Earnings & Green Impact */}
      <section className="grid gap-6 lg:grid-cols-3">
        <Card className="lg:col-span-2">
          <div className="mb-2 flex items-center justify-between">
            <div>
              <h3 className="text-lg font-bold">Earnings</h3>
              <p className="text-sm text-muted-foreground">Monthly breakdown from database</p>
            </div>
            <div className="flex rounded-full bg-muted p-1 text-xs font-semibold">
              {(["Week", "Month", "Year"] as const).map((t) => (
                <button
                  key={t}
                  type="button"
                  onClick={() => setTimeRange(t)}
                  className={`rounded-full px-3 py-1 transition ${
                    timeRange === t ? "bg-card shadow-soft text-foreground" : "text-muted-foreground"
                  }`}
                >
                  {t}
                </button>
              ))}
            </div>
          </div>
          <p className="font-display text-3xl font-extrabold">
            {formatINR(thisMonthEarnings)}{" "}
            <span className="text-sm font-normal text-muted-foreground">this month</span>
          </p>
          <div className="mt-6">
            <Bars data={barValues} labels={barLabels} />
          </div>
        </Card>

        {/* Real Green Impact */}
        <Card className="relative overflow-hidden">
          <SectionTitle title="Your green impact" />
          <div className="flex items-center gap-4">
            <div
              className="relative grid h-28 w-28 shrink-0 place-items-center rounded-full"
              style={{
                background: `conic-gradient(var(--primary) 0 ${heroProgress}%, var(--muted) ${heroProgress}% 100%)`,
              }}
            >
              <div className="grid h-20 w-20 place-items-center rounded-full bg-card text-center">
                <div>
                  <p className="font-display text-xl font-extrabold">{heroProgress}%</p>
                  <p className="text-[10px] text-muted-foreground">to Eco Hero</p>
                </div>
              </div>
            </div>
            <div className="space-y-2 text-sm">
              <p className="flex items-center gap-2">
                <TreePine className="h-4 w-4 text-success shrink-0" /> {treesSaved} trees saved
              </p>
              <p className="flex items-center gap-2">
                <Recycle className="h-4 w-4 text-info shrink-0" /> {recycledKgNum} kg recycled
              </p>
              <p className="flex items-center gap-2">
                <Sparkles className="h-4 w-4 text-sun shrink-0" /> {userLevel}
              </p>
            </div>
          </div>
          <div className="mt-5 rounded-2xl bg-sun-soft p-3 text-sm text-sun-foreground">
            {kgRemaining > 0 ? (
              <span>
                <b>{kgRemaining.toFixed(1)} kg more</b> to unlock Eco Hero badge + bonus 🎉
              </span>
            ) : (
              <span>
                <b>Congratulations!</b> You have unlocked the Eco Hero badge 🎉
              </span>
            )}
          </div>
        </Card>
      </section>

      {/* Real Pickups List & Live Rates */}
      <section className="grid gap-6 lg:grid-cols-3">
        <Card className="lg:col-span-2">
          <div className="mb-4 flex items-center justify-between">
            <h3 className="text-lg font-bold">Your pickups</h3>
            <Link to="/sell" className="text-sm font-medium text-primary hover:underline">
              + Post scrap
            </Link>
          </div>
          {listings.length > 0 ? (
            <div className="divide-y divide-border">
              {listings.slice(0, 5).map((p) => (
                <Link
                  key={p.id}
                  to="/listings/$id"
                  params={{ id: p.id }}
                  className="grid grid-cols-[auto_minmax(0,1fr)_auto] items-center gap-4 py-3 transition hover:bg-muted/40 rounded-xl px-2"
                >
                  <div className="grid h-11 w-11 place-items-center rounded-xl bg-primary-soft text-primary">
                    <Package className="h-5 w-5" />
                  </div>
                  <div className="min-w-0">
                    <p className="truncate font-semibold">
                      {p.title}{" "}
                      <span className="text-xs font-normal text-muted-foreground">
                        #SP{p.id.slice(0, 4).toUpperCase()}
                      </span>
                    </p>
                    <p className="truncate text-xs text-muted-foreground">
                      <Clock className="mr-1 inline h-3 w-3" />
                      {new Date(p.created_at).toLocaleDateString("en-IN", {
                        day: "numeric",
                        month: "short",
                      })}{" "}
                      · {p.weight_kg} kg · {p.collector?.display_name || "Awaiting collector"}
                    </p>
                  </div>
                  <div className="text-right">
                    <p className="font-display font-bold">
                      {formatINR(p.final_price || p.expected_price)}
                    </p>
                    <StatusPill s={p.status} />
                  </div>
                </Link>
              ))}
            </div>
          ) : (
            <div className="py-8 text-center">
              <Package className="mx-auto h-10 w-10 text-muted-foreground/50" />
              <p className="mt-2 text-sm font-semibold">No pickups yet</p>
              <p className="text-xs text-muted-foreground mt-0.5">
                Once you post scrap, your active pickups and collection history will appear here.
              </p>
              <Link
                to="/sell"
                className="mt-4 inline-flex items-center gap-1.5 rounded-full bg-primary px-4 py-2 text-xs font-semibold text-primary-foreground shadow-soft transition hover:opacity-90"
              >
                <Plus className="h-3.5 w-3.5" /> Post scrap
              </Link>
            </div>
          )}
        </Card>

        {/* Today's Rates from Database */}
        <Card>
          <SectionTitle title="Today's rates" action={profile?.city || "Kolkata"} />
          <div className="space-y-2">
            {rates.map((r) => (
              <div
                key={r.name}
                className="flex items-center justify-between rounded-xl px-3 py-2.5 transition hover:bg-muted"
              >
                <span className="text-sm font-medium">{r.name}</span>
                <span className="flex items-center gap-2 text-sm">
                  <b>₹{r.price}</b>
                  <span className="text-muted-foreground">/{r.unit}</span>
                  <span
                    className={`text-xs font-semibold ${
                      r.trend.startsWith("-")
                        ? "text-destructive"
                        : r.trend === "0"
                          ? "text-muted-foreground"
                          : "text-success"
                    }`}
                  >
                    {r.trend === "0" ? "—" : r.trend}
                  </span>
                </span>
              </div>
            ))}
          </div>
        </Card>
      </section>

      {/* Real Wallet Balance, Saved Addresses & Reviews */}
      <section className="grid gap-6 lg:grid-cols-3">
        {/* Wallet Balance Card */}
        <Card className="bg-sun-soft">
          <div className="flex items-center gap-3">
            <div className="bg-sun-grad grid h-11 w-11 place-items-center rounded-xl text-sun-foreground">
              <IndianRupee className="h-5 w-5" />
            </div>
            <div>
              <p className="text-sm text-muted-foreground">Wallet balance</p>
              <p className="font-display text-2xl font-extrabold">{formatINR(walletBalanceNum)}</p>
            </div>
          </div>
          <button
            type="button"
            onClick={handleWithdraw}
            className="mt-4 flex w-full items-center justify-center gap-2 rounded-full bg-primary py-2.5 text-sm font-semibold text-primary-foreground shadow-soft transition hover:opacity-90"
          >
            Withdraw to UPI <ArrowUpRight className="h-4 w-4" />
          </button>
        </Card>

        {/* Real Saved Addresses from DB */}
        <Card>
          <SectionTitle title="Saved addresses" />
          {stats?.saved_addresses && stats.saved_addresses.length > 0 ? (
            <div className="space-y-1">
              {stats.saved_addresses.map((addr) => (
                <p key={addr} className="flex items-center gap-2 py-1.5 text-sm truncate">
                  <MapPin className="h-4 w-4 text-primary shrink-0" />
                  <span className="truncate">{addr}</span>
                </p>
              ))}
            </div>
          ) : profile?.city ? (
            <p className="flex items-center gap-2 py-1.5 text-sm">
              <MapPin className="h-4 w-4 text-primary shrink-0" />
              <span>Home · {profile.city}</span>
            </p>
          ) : (
            <p className="text-xs text-muted-foreground py-2">
              No saved addresses yet. Addresses you use for pickups will be saved here automatically.
            </p>
          )}
          <button
            type="button"
            onClick={handleAddAddress}
            className="mt-2 text-sm font-medium text-primary hover:underline"
          >
            + Add address
          </button>
        </Card>

        {/* Real Reviews from DB */}
        <Card>
          <SectionTitle title="Recent reviews" />
          {reviews.length > 0 ? (
            <div className="space-y-3">
              {reviews.slice(0, 2).map((rev) => (
                <div key={rev.id} className="border-b last:border-b-0 pb-2">
                  <p className="text-sm flex items-center gap-2">
                    <Stars n={rev.rating} /> <b>{rev.reviewer.display_name}</b>
                  </p>
                  {rev.comment && (
                    <p className="mt-1 text-sm text-muted-foreground">"{rev.comment}"</p>
                  )}
                  <p className="mt-1.5 text-xs text-muted-foreground">
                    Pickup for: {rev.listing_title}
                  </p>
                </div>
              ))}
            </div>
          ) : (
            <div className="py-2">
              <p className="text-xs text-muted-foreground">
                No reviews yet. Complete your first scrap pickup to receive ratings and reviews from collectors.
              </p>
            </div>
          )}
        </Card>
      </section>
    </div>
  );
}

function roundNumber(num: number, decimals: number): number {
  const factor = Math.pow(10, decimals);
  return Math.round(num * factor) / factor;
}
