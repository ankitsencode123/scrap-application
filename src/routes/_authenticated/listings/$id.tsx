import { createFileRoute, Link } from "@tanstack/react-router";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Check, MessageCircle } from "lucide-react";
import { toast } from "sonner";
import { supabase } from "@/integrations/supabase/client";
import { useAuth } from "@/hooks/use-auth";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Stars } from "@/components/Stars";
import { PICKUP_STEPS, categoryOf, formatINR, statusLabel, usePhotoUrl, useRating } from "@/lib/scrap";
import { cn } from "@/lib/utils";

export const Route = createFileRoute("/_authenticated/listings/$id")({
  head: () => ({ meta: [{ title: "Scrap listing — scrappe" }, { name: "description", content: "Scrap listing details, offers and pickup tracking." }, { property: "og:title", content: "Scrap listing — scrappe" }, { property: "og:description", content: "Scrap listing details, offers and pickup tracking." }, { property: "og:type", content: "website" }, { name: "twitter:card", content: "summary" }] }),
  component: ListingPage,
});

function PersonRow({ id, label }: { id: string; label: string }) {
  const { data: p } = useQuery({
    queryKey: ["profile", id],
    queryFn: async () => (await supabase.from("profiles").select("*").eq("id", id).maybeSingle()).data,
  });
  const { data: r } = useRating(id);
  return (
    <div className="flex items-center gap-3">
      <div className="flex size-10 items-center justify-center rounded-full bg-secondary font-bold text-secondary-foreground">
        {(p?.full_name || "?").slice(0, 1).toUpperCase()}
      </div>
      <div className="flex-1">
        <p className="text-xs text-muted-foreground">{label}</p>
        <p className="font-semibold">{p?.full_name || "User"}{p?.city ? ` · ${p.city}` : ""}</p>
      </div>
      <div className="text-right text-xs text-muted-foreground">
        <Stars value={r?.avg ?? 0} size="size-3.5" />
        {r?.count ?? 0} reviews
      </div>
    </div>
  );
}

function ListingPage() {
  const { id } = Route.useParams();
  const { user } = useAuth();
  const qc = useQueryClient();
  const { data: l, refetch } = useQuery({
    queryKey: ["listing", id],
    refetchInterval: 8000,
    queryFn: async () => (await supabase.from("listings").select("*").eq("id", id).maybeSingle()).data,
  });
  const { data: offers = [], refetch: refetchOffers } = useQuery({
    queryKey: ["offers", id],
    refetchInterval: 8000,
    queryFn: async () => (await supabase.from("offers").select("*").eq("listing_id", id).order("amount", { ascending: false })).data ?? [],
  });
  const { data: myReview, refetch: refetchReview } = useQuery({
    queryKey: ["myReview", id, user?.id],
    queryFn: async () => (await supabase.from("reviews").select("*").eq("listing_id", id).eq("reviewer_id", user!.id).maybeSingle()).data,
  });
  const { data: url } = usePhotoUrl(l?.photo_path);
  const [amount, setAmount] = useState("");
  const [note, setNote] = useState("");
  const [rating, setRating] = useState(5);
  const [comment, setComment] = useState("");
  const [busy, setBusy] = useState(false);

  if (!l) return <p className="text-muted-foreground">Loading…</p>;
  const isSeller = l.seller_id === user?.id;
  const isCollector = l.collector_id === user?.id;
  const myOffer = offers.find((o) => o.collector_id === user?.id);
  const stepIdx = PICKUP_STEPS.findIndex((s) => s.id === l.status);
  const refresh = () => { refetch(); refetchOffers(); qc.invalidateQueries({ queryKey: ["listings"] }); };

  const run = async (fn: () => Promise<{ error: unknown } | void>, msg: string) => {
    setBusy(true);
    const res = await fn();
    setBusy(false);
    if (res && res.error) return toast.error("Something went wrong");
    toast.success(msg);
    refresh();
  };

  const makeOffer = () =>
    run(async () => {
      const n = Number(amount);
      if (!n || n <= 0) { toast.error("Enter a price"); return { error: true }; }
      return supabase.from("offers").insert({ listing_id: id, collector_id: user!.id, amount: n, message: note || null });
    }, "Offer sent");

  const accept = (offerId: string, collectorId: string, amt: number) =>
    run(async () => {
      const a = await supabase.from("offers").update({ status: "accepted" }).eq("id", offerId);
      if (a.error) return a;
      await supabase.from("offers").update({ status: "rejected" }).eq("listing_id", id).neq("id", offerId);
      return supabase.from("listings").update({ collector_id: collectorId, final_price: amt, status: "accepted" }).eq("id", id);
    }, "Offer accepted");

  const setStatus = (s: string) =>
    run(async () => supabase.from("listings").update({ status: s }).eq("id", id), statusLabel(s));

  const review = () =>
    run(async () => supabase.from("reviews").insert({
      listing_id: id, reviewer_id: user!.id, reviewee_id: isSeller ? l.collector_id! : l.seller_id, rating, comment: comment || null,
    }), "Thanks for your review").then(() => refetchReview());

  const other = isSeller ? l.collector_id : l.seller_id;
  const nextStep = stepIdx >= 1 && stepIdx < PICKUP_STEPS.length - 1 ? PICKUP_STEPS[stepIdx + 1] : null;

  return (
    <div className="mx-auto grid max-w-5xl gap-5 md:grid-cols-[1.1fr_1fr]">
      <div className="space-y-4">
        {url && <img src={url} alt={l.title} className="aspect-[4/3] w-full rounded-3xl object-cover" />}
        <p className="text-sm text-muted-foreground">{categoryOf(l.category).emoji} {categoryOf(l.category).label} · {statusLabel(l.status)}</p>
        <h1 className="text-2xl font-extrabold">{l.title}</h1>
        <p>{l.weight_kg} kg · asking <span className="font-bold text-primary">{formatINR(l.expected_price)}</span>
          {l.final_price != null && <> · agreed <span className="font-bold text-primary">{formatINR(l.final_price)}</span></>}</p>
        {l.description && <p className="text-muted-foreground">{l.description}</p>}
        {l.address && <p className="text-sm">📍 {l.address}</p>}
        <div className="rounded-2xl border bg-card p-4"><PersonRow id={l.seller_id} label="Seller" /></div>
        {l.collector_id && <div className="rounded-2xl border bg-card p-4"><PersonRow id={l.collector_id} label="Collector" /></div>}
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
                  <span className={cn("text-sm", i === stepIdx ? "font-bold" : i < stepIdx ? "" : "text-muted-foreground")}>{s.label}</span>
                </li>
              ))}
            </ol>
            {isCollector && nextStep && (
              <Button className="mt-4 w-full" disabled={busy} onClick={() => setStatus(nextStep.id)}>Mark: {nextStep.label}</Button>
            )}
            {(isSeller || isCollector) && other && (
              <Button asChild variant="outline" className="mt-2 w-full">
                <Link to="/chats" search={{ listing: id, with: other }}><MessageCircle className="size-4" /> Chat</Link>
              </Button>
            )}
            {isSeller && l.status === "open" && (
              <Button variant="ghost" className="mt-2 w-full text-destructive" disabled={busy} onClick={() => setStatus("cancelled")}>Cancel listing</Button>
            )}
          </section>
        )}

        {/* Offers */}
        {l.status === "open" && isSeller && (
          <section className="rounded-3xl border bg-card p-5">
            <h2 className="mb-3 font-extrabold">Offers ({offers.length})</h2>
            {offers.length === 0 && <p className="text-sm text-muted-foreground">No offers yet. Collectors nearby will see your scrap.</p>}
            <div className="space-y-3">
              {offers.map((o) => (
                <div key={o.id} className="space-y-2 rounded-2xl border p-3">
                  <PersonRow id={o.collector_id} label="Collector" />
                  <p className="text-lg font-extrabold text-primary">{formatINR(o.amount)}</p>
                  {o.message && <p className="text-sm text-muted-foreground">{o.message}</p>}
                  <div className="flex gap-2">
                    <Button size="sm" disabled={busy} onClick={() => accept(o.id, o.collector_id, Number(o.amount))}>Accept</Button>
                    <Button size="sm" variant="outline" asChild>
                      <Link to="/chats" search={{ listing: id, with: o.collector_id }}>Chat</Link>
                    </Button>
                  </div>
                </div>
              ))}
            </div>
          </section>
        )}

        {l.status === "open" && !isSeller && (
          <section className="rounded-3xl border bg-card p-5">
            <h2 className="mb-3 font-extrabold">Make an offer</h2>
            {myOffer ? (
              <p className="text-sm">You offered <b className="text-primary">{formatINR(myOffer.amount)}</b> · waiting for the seller.</p>
            ) : (
              <div className="space-y-2">
                <Input type="number" inputMode="numeric" placeholder="Your price in ₹" value={amount} onChange={(e) => setAmount(e.target.value)} />
                <Textarea placeholder="Message (e.g. I can come today at 5pm)" value={note} onChange={(e) => setNote(e.target.value)} />
                <Button className="w-full" disabled={busy} onClick={makeOffer}>Send offer</Button>
              </div>
            )}
            <Button asChild variant="outline" className="mt-2 w-full">
              <Link to="/chats" search={{ listing: id, with: l.seller_id }}><MessageCircle className="size-4" /> Ask the seller</Link>
            </Button>
          </section>
        )}

        {/* Review */}
        {l.status === "completed" && (isSeller || isCollector) && (
          <section className="rounded-3xl border bg-card p-5">
            <h2 className="mb-3 font-extrabold">Rate the {isSeller ? "collector" : "seller"}</h2>
            {myReview ? (
              <div className="space-y-1"><Stars value={myReview.rating} /><p className="text-sm text-muted-foreground">{myReview.comment || "Thanks for your review!"}</p></div>
            ) : (
              <div className="space-y-2">
                <Stars value={rating} onChange={setRating} size="size-7" />
                <Textarea placeholder="How was it?" value={comment} onChange={(e) => setComment(e.target.value)} />
                <Button className="w-full" disabled={busy} onClick={review}>Submit review</Button>
              </div>
            )}
          </section>
        )}
      </div>
    </div>
  );
}
