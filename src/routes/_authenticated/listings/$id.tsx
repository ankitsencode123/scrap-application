import { createFileRoute, Link } from "@tanstack/react-router";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { Check, Loader2, MessageCircle } from "lucide-react";
import { toast } from "sonner";
import { api, errorMessage, resolveMediaUrl, type ApiBid, type ApiListing, type ApiTracker, type UserBrief } from "@/lib/api";
import { useAuth } from "@/hooks/use-auth";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Stars } from "@/components/Stars";
import { UserAvatar } from "@/components/UserAvatar";
import { PICKUP_STEPS, categoryOf, formatINR, statusLabel } from "@/lib/scrap";
import { cn } from "@/lib/utils";

export const Route = createFileRoute("/_authenticated/listings/$id")({
  head: () => ({ meta: [{ title: "Scrap listing — scrappe" }, { name: "description", content: "Scrap listing details, offers and pickup tracking." }, { property: "og:title", content: "Scrap listing — scrappe" }, { property: "og:description", content: "Scrap listing details, offers and pickup tracking." }, { property: "og:type", content: "website" }, { name: "twitter:card", content: "summary" }] }),
  component: ListingPage,
});

function PersonRow({ person, label }: { person: UserBrief; label: string }) {
  return (
    <div className="flex items-center gap-3">
      <UserAvatar
        src={person.avatar_url}
        name={person.display_name}
        className="size-10"
        fallbackClassName="size-10 bg-secondary font-bold text-secondary-foreground shadow-sm"
      />
      <div className="flex-1 min-w-0">
        <p className="text-xs text-muted-foreground">{label}</p>
        <p className="font-semibold truncate">{person.display_name || "User"}{person.city ? ` · ${person.city}` : ""}</p>
        {person.phone && (
          <a href={`tel:${person.phone}`} className="text-xs font-medium text-primary hover:underline">📞 {person.phone}</a>
        )}
      </div>
      <div className="text-right text-xs text-muted-foreground shrink-0">
        <Stars value={person.rating_avg} size="size-3.5" />
        {person.rating_count} reviews
      </div>
    </div>
  );
}

function ListingPage() {
  const { id } = Route.useParams();
  const { user, profile } = useAuth();
  const qc = useQueryClient();
  const { data: l, refetch } = useQuery({
    queryKey: ["listing", id],
    refetchInterval: 12000,
    queryFn: () => api<ApiListing>(`/listings/${id}`),
  });
  const isSeller = !!l?.is_owner;
  const isCollector = !!l?.is_assigned_collector;
  const isParty = isSeller || isCollector;
  // Seller-only: every competing bid with collector name + rating, best offer first.
  const { data: offers = [], refetch: refetchOffers } = useQuery({
    queryKey: ["offers", id],
    enabled: isSeller && l?.status === "posted",
    refetchInterval: 15000,
    queryFn: () => api<ApiBid[]>(`/listings/${id}/bids`),
  });
  // Pickup tracker (timestamps + who may advance) once an offer has been accepted.
  const { data: tracker, refetch: refetchTracker } = useQuery({
    queryKey: ["tracker", id],
    enabled: isParty && !!l && l.status !== "posted",
    refetchInterval: 12000,
    queryFn: () => api<ApiTracker>(`/listings/${id}/tracker`),
  });
  const [activeImgIdx, setActiveImgIdx] = useState(0);
  const [imgError, setImgError] = useState(false);

  useEffect(() => {
    setImgError(false);
  }, [activeImgIdx, id]);
  const [amount, setAmount] = useState("");
  const [note, setNote] = useState("");
  const [rating, setRating] = useState(5);
  const [comment, setComment] = useState("");
  const [submittedReview, setSubmittedReview] = useState<{ rating: number; comment: string } | null>(null);
  const [busy, setBusy] = useState(false);

  if (!l) {
    return (
      <div className="flex min-h-[50vh] flex-col items-center justify-center gap-3 text-muted-foreground">
        <Loader2 className="size-8 animate-spin text-primary" />
        <p className="text-sm font-medium">Loading scrap details…</p>
      </div>
    );
  }
  const myOffer = l.my_bid && l.my_bid.status !== "withdrawn" ? l.my_bid : null;
  const canBid = profile?.role === "collector" && !isSeller;
  const stepIdx = PICKUP_STEPS.findIndex((s) => s.id === l.status);
  const refresh = async () => {
    // Run all independent queries and invalidations concurrently
    await Promise.all([
      refetch(),
      refetchOffers(),
      refetchTracker(),
      ...["listings", "stats", "map-listings", "my-bids"].map((key) => qc.invalidateQueries({ queryKey: [key] })),
    ]);
  };

  const run = async (fn: () => Promise<unknown>, msg: string): Promise<boolean> => {
    setBusy(true);
    try {
      await fn();
    } catch (err) {
      setBusy(false);
      toast.error(errorMessage(err));
      refresh(); // state may have moved on (e.g. 409 double-tap) - show the latest
      return false;
    }
    setBusy(false);
    toast.success(msg);
    refresh();
    return true;
  };

  const acceptAskingPrice = async () => {
    const p = Number(l.expected_price);
    if (!p || p <= 0) {
      toast.error("Invalid asking price");
      return;
    }
    await run(
      () =>
        api(`/listings/${id}/bids`, {
          method: "POST",
          body: {
            amount: p,
            note: `I accept your asking price of ${formatINR(p)}. Ready for pickup!`,
          },
        }),
      `Accepted asking price of ${formatINR(p)}!`
    );
  };

  const makeOffer = async () => {
    const n = Number(amount);
    if (!n || n <= 0) { toast.error("Enter a price"); return; }
    await run(() => api(`/listings/${id}/bids`, { method: "POST", body: { amount: n, note: note || null } }), "Offer sent");
  };

  const withdraw = (bidId: string) => run(() => api(`/bids/${bidId}`, { method: "DELETE" }), "Offer withdrawn");

  const accept = (bidId: string) =>
    run(() => api(`/listings/${id}/bids/${bidId}/accept`, { method: "POST" }), "Offer accepted");

  // Moves the pickup exactly one step; expected_status guards against double taps.
  const advance = () =>
    run(() => api(`/listings/${id}/advance`, { method: "POST", body: { expected_status: l.status } }), statusLabel(tracker?.next_status ?? ""));

  const cancel = () => run(() => api(`/listings/${id}/cancel`, { method: "POST" }), "Listing cancelled");

  const review = async () => {
    const ok = await run(() => api(`/listings/${id}/reviews`, { method: "POST", body: { rating, comment: comment || null } }), "Thanks for your review");
    if (ok) setSubmittedReview({ rating, comment });
  };

  const other = isSeller ? l.collector : l.seller;
  const timestamp = (stepId: string) => {
    const t = tracker?.steps.find((x) => x.status === stepId)?.timestamp ?? (stepId === "posted" ? l.created_at : null);
    return t ? new Date(t).toLocaleString([], { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }) : null;
  };

  const currentImg = l.images[activeImgIdx] ?? l.images[0];
  const currentUrl = !imgError && currentImg?.url ? resolveMediaUrl(currentImg.url) : null;

  return (
    <div className="mx-auto grid max-w-5xl gap-5 md:grid-cols-[1.1fr_1fr]">
      <div className="space-y-4">
        {currentUrl ? (
          <div className="relative overflow-hidden rounded-3xl border bg-muted">
            <img
              src={currentUrl}
              alt={l.title}
              onError={() => setImgError(true)}
              className="aspect-[4/3] w-full object-cover transition-all"
            />
            {l.images.length > 1 && (
              <span className="absolute bottom-3 right-3 rounded-full bg-black/70 px-2.5 py-1 text-xs font-semibold text-white shadow backdrop-blur-sm">
                📷 {activeImgIdx + 1} / {l.images.length}
              </span>
            )}
          </div>
        ) : (
          <div className="flex aspect-[4/3] w-full flex-col items-center justify-center gap-2 rounded-3xl border bg-muted/60 text-muted-foreground shadow-xs">
            <span className="text-5xl">{categoryOf(l.category).emoji}</span>
            <span className="text-sm font-semibold">{categoryOf(l.category).label}</span>
          </div>
        )}
        {l.images.length > 1 && (
          <div className="flex gap-2 overflow-x-auto pb-1">
            {l.images.map((im, idx) => {
              const thumbUrl = resolveMediaUrl(im.url);
              return (
                <button
                  key={im.id}
                  type="button"
                  onClick={() => {
                    setActiveImgIdx(idx);
                    setImgError(false);
                  }}
                  className={cn(
                    "relative aspect-square size-16 shrink-0 overflow-hidden rounded-xl border-2 transition-all",
                    activeImgIdx === idx ? "border-primary ring-2 ring-primary/40 shadow-sm" : "border-border opacity-70 hover:opacity-100"
                  )}
                >
                  {thumbUrl ? (
                    <img src={thumbUrl} alt={l.title} loading="lazy" className="size-full object-cover" />
                  ) : (
                    <div className="flex size-full items-center justify-center text-lg">{categoryOf(l.category).emoji}</div>
                  )}
                </button>
              );
            })}
          </div>
        )}
        <div className="flex flex-wrap items-center gap-2 text-sm text-muted-foreground">
          <span>{categoryOf(l.category).emoji} {categoryOf(l.category).label}</span>
          <span>·</span>
          <span>{statusLabel(l.status)}</span>
          {l.is_negotiable && (
            <span className="rounded-full bg-emerald-50 px-2 py-0.5 text-xs font-semibold text-emerald-700 dark:bg-emerald-950/60 dark:text-emerald-300">
              🤝 Negotiable
            </span>
          )}
        </div>
        <h1 className="text-2xl font-extrabold">{l.title}</h1>
        <p>{l.weight_kg} kg · asking <span className="font-bold text-primary">{formatINR(l.expected_price)}</span>
          {l.final_price != null && <> · agreed <span className="font-bold text-primary">{formatINR(l.final_price)}</span></>}</p>
        {l.description && <p className="text-muted-foreground">{l.description}</p>}

        {/* Convenient seller-side attributes */}
        {(l.pickup_time_slot || l.floor_access || l.packaging_type || l.is_negotiable !== undefined) && (
          <div className="grid grid-cols-2 gap-3 rounded-2xl border bg-card p-4 text-xs shadow-xs">
            {l.pickup_time_slot && (
              <div>
                <span className="block text-muted-foreground">Preferred Pickup:</span>
                <span className="font-bold text-foreground">🕒 {l.pickup_time_slot}</span>
              </div>
            )}
            {l.floor_access && (
              <div>
                <span className="block text-muted-foreground">Floor / Access:</span>
                <span className="font-bold text-foreground">🏢 {l.floor_access}</span>
              </div>
            )}
            {l.packaging_type && (
              <div>
                <span className="block text-muted-foreground">Packaging:</span>
                <span className="font-bold text-foreground">📦 {l.packaging_type}</span>
              </div>
            )}
            <div>
              <span className="block text-muted-foreground">Price Negotiable:</span>
              <span className={cn("font-bold", l.is_negotiable ? "text-emerald-600 dark:text-emerald-400" : "text-muted-foreground")}>
                {l.is_negotiable ? "Yes (Offers welcome)" : "Firm price"}
              </span>
            </div>
          </div>
        )}

        {l.address && <p className="text-sm">📍 {l.address}{l.distance_km != null && ` · ${l.distance_km} km away`}</p>}
        {!isParty && <p className="text-xs text-muted-foreground">The exact address is shared once the seller accepts your offer.</p>}
        <div className="rounded-2xl border bg-card p-4"><PersonRow person={l.seller} label="Seller" /></div>
        {l.collector && <div className="rounded-2xl border bg-card p-4"><PersonRow person={l.collector} label="Collector" /></div>}
      </div>

      <div className="space-y-4">
        {/* Pickup tracking */}
        {l.status !== "cancelled" && (
          <section className="rounded-3xl border bg-card p-5">
            <h2 className="mb-4 font-extrabold">Pickup tracking</h2>
            <ol className="space-y-3">
              {PICKUP_STEPS.map((s, i) => (
                <li key={s.id} className="flex items-center gap-3">
                  <span className={cn("flex size-7 items-center justify-center rounded-full border-2 text-xs font-bold",
                    i <= stepIdx ? "border-primary bg-primary text-primary-foreground" : "border-border text-muted-foreground")}>
                    {i <= stepIdx ? <Check className="size-4" /> : i + 1}
                  </span>
                  <span className={cn("flex-1 text-sm", i === stepIdx ? "font-bold" : i < stepIdx ? "" : "text-muted-foreground")}>{s.label}</span>
                  {i <= stepIdx && timestamp(s.id) && <span className="text-xs text-muted-foreground">{timestamp(s.id)}</span>}
                </li>
              ))}
            </ol>
            {tracker?.can_advance && tracker.next_label && (
              <Button className="mt-4 w-full" disabled={busy} onClick={advance}>
                {busy && <Loader2 className="mr-2 size-4 animate-spin" />}
                Mark: {tracker.next_label}
              </Button>
            )}
            {isParty && other && (
              <Button asChild variant="outline" className="mt-2 w-full">
                <Link to="/chats" search={{ listing: id, with: other.id }}><MessageCircle className="size-4" /> Chat</Link>
              </Button>
            )}
            {isSeller && (l.status === "posted" || l.status === "offer_accepted") && (
              <Button variant="ghost" className="mt-2 w-full text-destructive" disabled={busy} onClick={cancel}>
                {busy && <Loader2 className="mr-2 size-4 animate-spin" />}
                Cancel listing
              </Button>
            )}
          </section>
        )}

        {/* Offers */}
        {l.status === "posted" && isSeller && (
          <section className="rounded-3xl border bg-card p-5">
            <h2 className="mb-3 font-extrabold">Offers ({offers.length})</h2>
            {offers.length === 0 && <p className="text-sm text-muted-foreground">No offers yet. Collectors nearby will see your scrap.</p>}
            <div className="space-y-3">
              {offers.map((o) => (
                <div key={o.id} className="space-y-2 rounded-2xl border p-3">
                  <PersonRow person={o.collector} label="Collector" />
                  <p className="text-lg font-extrabold text-primary">
                    {formatINR(o.amount)}
                    {o.is_highest && offers.length > 1 && <span className="ml-2 rounded-full bg-secondary px-2 py-0.5 align-middle text-[11px] font-semibold text-secondary-foreground">Highest</span>}
                  </p>
                  {o.note && <p className="text-sm text-muted-foreground">{o.note}</p>}
                  <div className="flex gap-2">
                    <Button size="sm" disabled={busy} onClick={() => accept(o.id)}>
                      {busy && <Loader2 className="mr-1.5 size-3.5 animate-spin" />}
                      Accept
                    </Button>
                    <Button size="sm" variant="outline" asChild>
                      <Link to="/chats" search={{ listing: id, with: o.collector.id }}>Chat</Link>
                    </Button>
                  </div>
                </div>
              ))}
            </div>
          </section>
        )}

        {l.status === "posted" && canBid && (
          <section className="rounded-3xl border bg-card p-5 space-y-4">
            <div>
              <h2 className="font-extrabold text-lg">Collector Offers</h2>
              <p className="text-xs text-muted-foreground mt-0.5">
                Asking price: <b className="text-primary font-bold">{formatINR(l.expected_price)}</b>
                {l.is_negotiable ? " (Negotiable)" : " (Firm price)"}
              </p>
            </div>

            {myOffer ? (
              <div className="space-y-3 rounded-2xl border bg-muted/30 p-3.5">
                <p className="text-sm">You offered <b className="text-primary font-bold">{formatINR(myOffer.amount)}</b> · waiting for seller response.</p>
                {myOffer.status === "pending" && (
                  <Button variant="outline" size="sm" disabled={busy} onClick={() => withdraw(myOffer.id)}>
                    {busy && <Loader2 className="mr-1.5 size-3.5 animate-spin" />}
                    Withdraw offer
                  </Button>
                )}
              </div>
            ) : (
              <div className="space-y-3">
                {/* 1-Tap Direct Accept Asking Price */}
                <Button
                  size="lg"
                  className="w-full h-12 text-base font-bold bg-primary hover:bg-primary/90 text-primary-foreground rounded-2xl shadow-sm"
                  disabled={busy}
                  onClick={acceptAskingPrice}
                >
                  {busy ? <Loader2 className="mr-2 size-5 animate-spin" /> : <Check className="mr-2 size-5" />}
                  Accept Asking Price · {formatINR(l.expected_price)}
                </Button>

                {/* Counter offer section */}
                <div className="relative my-2 flex items-center justify-center">
                  <div className="absolute inset-0 flex items-center"><span className="w-full border-t border-border" /></div>
                  <span className="relative bg-card px-2 text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
                    or send a counter-offer
                  </span>
                </div>

                <div className="space-y-2">
                  <div className="flex flex-wrap gap-1.5">
                    <button
                      type="button"
                      onClick={() => setAmount(String(l.expected_price))}
                      className={cn(
                        "rounded-full px-2.5 py-1 text-xs font-semibold border transition-colors",
                        amount === String(l.expected_price)
                          ? "bg-primary text-primary-foreground border-primary"
                          : "bg-muted hover:bg-muted/80 text-foreground border-border"
                      )}
                    >
                      {formatINR(l.expected_price)} (Asking)
                    </button>
                    {l.expected_price > 100 && (
                      <button
                        type="button"
                        onClick={() => setAmount(String(Math.round(l.expected_price * 0.9)))}
                        className={cn(
                          "rounded-full px-2.5 py-1 text-xs font-semibold border transition-colors",
                          amount === String(Math.round(l.expected_price * 0.9))
                            ? "bg-primary text-primary-foreground border-primary"
                            : "bg-muted hover:bg-muted/80 text-foreground border-border"
                        )}
                      >
                        {formatINR(Math.round(l.expected_price * 0.9))} (-10%)
                      </button>
                    )}
                    {l.expected_price > 200 && (
                      <button
                        type="button"
                        onClick={() => setAmount(String(Math.round(l.expected_price * 0.8)))}
                        className={cn(
                          "rounded-full px-2.5 py-1 text-xs font-semibold border transition-colors",
                          amount === String(Math.round(l.expected_price * 0.8))
                            ? "bg-primary text-primary-foreground border-primary"
                            : "bg-muted hover:bg-muted/80 text-foreground border-border"
                        )}
                      >
                        {formatINR(Math.round(l.expected_price * 0.8))} (-20%)
                      </button>
                    )}
                  </div>

                  <Input
                    type="number"
                    inputMode="numeric"
                    placeholder="Custom counter offer in ₹"
                    value={amount}
                    onChange={(e) => setAmount(e.target.value)}
                  />
                  <Textarea
                    placeholder="Message to seller (e.g. Can pick up today at 4 PM)"
                    value={note}
                    onChange={(e) => setNote(e.target.value)}
                  />
                  <Button variant="secondary" className="w-full font-bold" disabled={busy} onClick={makeOffer}>
                    {busy && <Loader2 className="mr-2 size-4 animate-spin" />}
                    Send Counter-Offer
                  </Button>
                </div>
              </div>
            )}
            <Button asChild variant="outline" className="w-full">
              <Link to="/chats" search={{ listing: id, with: l.seller.id }}><MessageCircle className="size-4 mr-1.5" /> Ask the seller</Link>
            </Button>
          </section>
        )}

        {/* Review */}
        {l.status === "completed" && isParty && (
          <section className="rounded-3xl border bg-card p-5">
            <h2 className="mb-3 font-extrabold">Rate the {isSeller ? "collector" : "seller"}</h2>
            {submittedReview || l.has_reviewed ? (
              <div className="space-y-1">
                {submittedReview && <Stars value={submittedReview.rating} />}
                <p className="text-sm text-muted-foreground">{submittedReview?.comment || "Thanks for your review!"}</p>
              </div>
            ) : (
              <div className="space-y-2">
                <Stars value={rating} onChange={setRating} size="size-7" />
                <Textarea placeholder="How was it?" value={comment} onChange={(e) => setComment(e.target.value)} />
                <Button className="w-full" disabled={busy} onClick={review}>
                  {busy && <Loader2 className="mr-2 size-4 animate-spin" />}
                  Submit review
                </Button>
              </div>
            )}
          </section>
        )}
      </div>
    </div>
  );
}
