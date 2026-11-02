import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { useState } from "react";
import { Camera, LocateFixed } from "lucide-react";
import { toast } from "sonner";
import { supabase } from "@/integrations/supabase/client";
import { useAuth } from "@/hooks/use-auth";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { CATEGORIES } from "@/lib/scrap";
import { cn } from "@/lib/utils";

export const Route = createFileRoute("/_authenticated/sell")({
  head: () => ({
    meta: [
      { title: "Sell scrap — scrappe" },
      { name: "description", content: "Post a photo and details of your scrap to get offers from collectors." },
      { property: "og:title", content: "Sell scrap — scrappe" },
      { property: "og:description", content: "Post a photo and details of your scrap to get offers from collectors." },
    ],
  }),
  component: SellPage,
});

function SellPage() {
  const { user } = useAuth();
  const navigate = useNavigate();
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<string | null>(null);
  const [category, setCategory] = useState("paper");
  const [title, setTitle] = useState("");
  const [weight, setWeight] = useState("");
  const [price, setPrice] = useState("");
  const [desc, setDesc] = useState("");
  const [address, setAddress] = useState("");
  const [loc, setLoc] = useState<{ lat: number; lng: number } | null>(null);
  const [busy, setBusy] = useState(false);

  const pick = (f: File | undefined) => {
    if (!f) return;
    setFile(f);
    setPreview(URL.createObjectURL(f));
  };

  const locate = () =>
    navigator.geolocation?.getCurrentPosition(
      (p) => { setLoc({ lat: p.coords.latitude, lng: p.coords.longitude }); toast.success("Location added"); },
      () => toast.error("Couldn't get your location"),
    );

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!user) return;
    setBusy(true);
    let photo_path: string | null = null;
    if (file) {
      const path = `${user.id}/${crypto.randomUUID()}-${file.name.replace(/[^\w.]/g, "")}`;
      const { error } = await supabase.storage.from("scrap-photos").upload(path, file);
      if (error) { toast.error(error.message); setBusy(false); return; }
      photo_path = path;
    }
    const { data, error } = await supabase.from("listings").insert({
      seller_id: user.id, title, category, weight_kg: Number(weight), expected_price: price ? Number(price) : null,
      description: desc || null, address: address || null, lat: loc?.lat ?? null, lng: loc?.lng ?? null, photo_path,
    }).select("id").single();
    setBusy(false);
    if (error) return toast.error(error.message);
    toast.success("Posted! Collectors can now make offers.");
    navigate({ to: "/listings/$id", params: { id: data.id } });
  };

  return (
    <form onSubmit={submit} className="mx-auto max-w-xl space-y-5">
      <h1 className="text-2xl font-extrabold">Sell your scrap</h1>

      <label className="flex aspect-[4/3] cursor-pointer flex-col items-center justify-center overflow-hidden rounded-3xl border-2 border-dashed bg-card text-muted-foreground">
        {preview ? <img src={preview} alt="Preview" className="size-full object-cover" /> : (
          <>
            <Camera className="size-10 text-primary" />
            <span className="mt-2 font-semibold text-foreground">Add a photo</span>
            <span className="text-xs">Take a picture or choose from gallery</span>
          </>
        )}
        <input type="file" accept="image/*" capture="environment" className="hidden" onChange={(e) => pick(e.target.files?.[0])} />
      </label>

      <div className="space-y-2">
        <Label>What is it?</Label>
        <div className="grid grid-cols-3 gap-2 sm:grid-cols-4">
          {CATEGORIES.map((c) => (
            <button key={c.id} type="button" onClick={() => setCategory(c.id)}
              className={cn("rounded-xl border p-2 text-center text-xs font-medium",
                category === c.id ? "border-primary bg-secondary" : "bg-card")}>
              <div className="text-xl">{c.emoji}</div>{c.label}
            </button>
          ))}
        </div>
      </div>

      <div className="space-y-1.5"><Label>Title</Label>
        <Input required placeholder="e.g. Old newspapers, 3 months" value={title} onChange={(e) => setTitle(e.target.value)} className="h-11" /></div>

      <div className="grid grid-cols-2 gap-3">
        <div className="space-y-1.5"><Label>Weight (kg)</Label>
          <Input required type="number" inputMode="decimal" step="0.1" min="0" value={weight} onChange={(e) => setWeight(e.target.value)} className="h-11" /></div>
        <div className="space-y-1.5"><Label>Expected price (₹)</Label>
          <Input type="number" inputMode="numeric" min="0" placeholder="Optional" value={price} onChange={(e) => setPrice(e.target.value)} className="h-11" /></div>
      </div>

      <div className="space-y-1.5"><Label>Details</Label>
        <Textarea placeholder="Condition, quantity, best time to pick up…" value={desc} onChange={(e) => setDesc(e.target.value)} /></div>

      <div className="space-y-1.5"><Label>Pickup address</Label>
        <Input placeholder="House, street, area" value={address} onChange={(e) => setAddress(e.target.value)} className="h-11" />
        <Button type="button" variant="outline" size="sm" onClick={locate} className="mt-1">
          <LocateFixed /> {loc ? "Location added ✓" : "Use my current location"}
        </Button>
      </div>

      <Button type="submit" size="lg" variant="accent" className="w-full" disabled={busy}>
        {busy ? "Posting…" : "Post for offers"}
      </Button>
    </form>
  );
}
