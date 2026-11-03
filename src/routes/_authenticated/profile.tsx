import { createFileRoute } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { toast } from "sonner";
import { supabase } from "@/integrations/supabase/client";
import { useAuth } from "@/hooks/use-auth";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Stars } from "@/components/Stars";
import { formatINR, useRating } from "@/lib/scrap";

export const Route = createFileRoute("/_authenticated/profile")({
  head: () => ({ meta: [{ title: "Profile — scrappe" }, { name: "description", content: "Your scrappe profile, ratings and reviews." }, { property: "og:title", content: "Profile — scrappe" }, { property: "og:description", content: "Your scrappe profile, ratings and reviews." }, { property: "og:type", content: "website" }, { name: "twitter:card", content: "summary" }] }),
  component: ProfilePage,
});

function ProfilePage() {
  const { user, profile, refreshProfile, signOut } = useAuth();
  const [name, setName] = useState("");
  const [phone, setPhone] = useState("");
  const [city, setCity] = useState("");
  useEffect(() => { setName(profile?.full_name ?? ""); setPhone(profile?.phone ?? ""); setCity(profile?.city ?? ""); }, [profile]);
  const { data: r } = useRating(user?.id);
  const { data: reviews = [] } = useQuery({
    queryKey: ["reviewsOf", user?.id],
    queryFn: async () => (await supabase.from("reviews").select("*").eq("reviewee_id", user!.id).order("created_at", { ascending: false })).data ?? [],
  });
  const { data: done = [] } = useQuery({
    queryKey: ["done", user?.id],
    queryFn: async () => (await supabase.from("listings").select("final_price").eq("status", "completed").or(`seller_id.eq.${user!.id},collector_id.eq.${user!.id}`)).data ?? [],
  });
  const total = done.reduce((a, l) => a + Number(l.final_price ?? 0), 0);

  const save = async () => {
    const { error } = await supabase.from("profiles").update({ full_name: name, phone: phone || null, city: city || null }).eq("id", user!.id);
    if (error) { toast.error("Couldn't save"); return; }
    toast.success("Saved");
    refreshProfile();
  };

  return (
    <div className="mx-auto max-w-xl space-y-5">
      <div className="flex items-center gap-4 rounded-3xl border bg-card p-5">
        <div className="flex size-16 items-center justify-center rounded-full bg-primary text-2xl font-extrabold text-primary-foreground">
          {(profile?.full_name || user?.email || "?").slice(0, 1).toUpperCase()}
        </div>
        <div className="flex-1">
          <h1 className="text-xl font-extrabold">{profile?.full_name || "Your profile"}</h1>
          <p className="text-sm text-muted-foreground capitalize">{profile?.account_type} · {user?.email}</p>
          <div className="mt-1 flex items-center gap-2 text-sm"><Stars value={r?.avg ?? 0} /> {r?.avg ? r.avg.toFixed(1) : "—"} ({r?.count ?? 0})</div>
        </div>
      </div>
      <div className="grid grid-cols-2 gap-3">
        <div className="rounded-2xl border bg-card p-4"><p className="text-xs text-muted-foreground">Pickups done</p><p className="text-2xl font-extrabold">{done.length}</p></div>
        <div className="rounded-2xl border bg-card p-4"><p className="text-xs text-muted-foreground">{profile?.account_type === "collector" ? "Total paid" : "Total earned"}</p><p className="text-2xl font-extrabold text-primary">{formatINR(total)}</p></div>
      </div>
      <section className="space-y-3 rounded-3xl border bg-card p-5">
        <h2 className="font-extrabold">Details</h2>
        <div><Label>Name</Label><Input value={name} onChange={(e) => setName(e.target.value)} /></div>
        <div><Label>Phone</Label><Input value={phone} onChange={(e) => setPhone(e.target.value)} inputMode="tel" /></div>
        <div><Label>City</Label><Input value={city} onChange={(e) => setCity(e.target.value)} /></div>
        <Button className="w-full" onClick={save}>Save</Button>
      </section>
      <section className="space-y-3 rounded-3xl border bg-card p-5">
        <h2 className="font-extrabold">Reviews about you</h2>
        {reviews.length === 0 && <p className="text-sm text-muted-foreground">No reviews yet.</p>}
        {reviews.map((v) => (
          <div key={v.id} className="border-t pt-3"><Stars value={v.rating} size="size-3.5" />{v.comment && <p className="mt-1 text-sm">{v.comment}</p>}</div>
        ))}
      </section>
      <Button variant="outline" className="w-full" onClick={signOut}>Sign out</Button>
    </div>
  );
}
