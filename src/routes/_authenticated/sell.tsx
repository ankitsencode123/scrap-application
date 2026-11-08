import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { useState } from "react";
import { Camera, LocateFixed, Loader2, Plus, X, Check, Clock, Building2, Package, Sparkles } from "lucide-react";
import { toast } from "sonner";
import { api, errorMessage, uploadImages, type ApiListing } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Switch } from "@/components/ui/switch";
import { CATEGORIES } from "@/lib/scrap";
import { cn } from "@/lib/utils";
import { useAuth } from "@/hooks/use-auth";

export const Route = createFileRoute("/_authenticated/sell")({
  head: () => ({
    meta: [
      { title: "Sell scrap — scrappe" },
      { name: "description", content: "Post photos and details of your scrap to get quick offers from local collectors." },
      { property: "og:title", content: "Sell scrap — scrappe" },
      { property: "og:description", content: "Post photos and details of your scrap to get quick offers from local collectors." },
    ],
  }),
  component: SellPage,
});

const TIME_SLOTS = [
  { id: "Morning (8 AM - 12 PM)", label: "Morning", sub: "8 AM - 12 PM", emoji: "🌅" },
  { id: "Afternoon (12 PM - 4 PM)", label: "Afternoon", sub: "12 PM - 4 PM", emoji: "☀️" },
  { id: "Evening (4 PM - 8 PM)", label: "Evening", sub: "4 PM - 8 PM", emoji: "🌆" },
  { id: "Anytime / Flexible", label: "Anytime", sub: "Flexible", emoji: "⏰" },
  { id: "Weekend only", label: "Weekend", sub: "Sat / Sun", emoji: "🗓️" },
];

const FLOOR_OPTIONS = [
  { id: "Ground floor", label: "Ground floor", emoji: "🏡" },
  { id: "Lift / Elevator available", label: "Lift available", emoji: "🛗" },
  { id: "1st / 2nd floor (stairs)", label: "1st - 2nd floor", emoji: "🪜" },
  { id: "Upper floor (no lift)", label: "Upper floor (no lift)", emoji: "🏢" },
];

const PACKAGING_OPTIONS = [
  { id: "In bags / boxes", label: "Bags / Boxes", emoji: "📦" },
  { id: "Tied in bundles", label: "Tied bundles", emoji: "🪢" },
  { id: "Loose items", label: "Loose scrap", emoji: "🧺" },
  { id: "Needs sorting", label: "Needs sorting", emoji: "🔧" },
];

const MAX_PHOTOS = 6;

function SellPage() {
  const navigate = useNavigate();
  const { profile } = useAuth();
  const [files, setFiles] = useState<File[]>([]);
  const [previews, setPreviews] = useState<string[]>([]);
  const [activeCoverIdx, setActiveCoverIdx] = useState(0);

  const [category, setCategory] = useState<string>("paper_cardboard");
  const [title, setTitle] = useState("");
  const [weight, setWeight] = useState("");
  const [price, setPrice] = useState("");
  const [desc, setDesc] = useState("");
  const [address, setAddress] = useState(profile?.city ? `Home, ${profile.city}` : "");
  const [loc, setLoc] = useState<{ lat: number; lng: number } | null>(
    profile?.latitude && profile?.longitude ? { lat: profile.latitude, lng: profile.longitude } : null
  );
  const [locating, setLocating] = useState(false);

  // Convenient Seller-side Fields
  const [timeSlot, setTimeSlot] = useState<string>("Morning (8 AM - 12 PM)");
  const [floorAccess, setFloorAccess] = useState<string>("Ground floor");
  const [packagingType, setPackagingType] = useState<string>("In bags / boxes");
  const [isNegotiable, setIsNegotiable] = useState(true);

  const [busy, setBusy] = useState(false);

  const addFiles = (selectedFiles: FileList | null) => {
    if (!selectedFiles || selectedFiles.length === 0) return;
    const incoming = Array.from(selectedFiles);
    const available = MAX_PHOTOS - files.length;
    if (available <= 0) {
      toast.error(`You can add up to ${MAX_PHOTOS} photos`);
      return;
    }
    const toAdd = incoming.slice(0, available);
    if (incoming.length > available) {
      toast.info(`Added ${available} photo(s). Maximum is ${MAX_PHOTOS}.`);
    }
    const newFiles = [...files, ...toAdd];
    const newPreviews = [...previews, ...toAdd.map((f) => URL.createObjectURL(f))];
    setFiles(newFiles);
    setPreviews(newPreviews);
  };

  const removePhoto = (index: number) => {
    const newFiles = files.filter((_, i) => i !== index);
    const newPreviews = previews.filter((_, i) => i !== index);
    setFiles(newFiles);
    setPreviews(newPreviews);
    if (activeCoverIdx >= newFiles.length) {
      setActiveCoverIdx(Math.max(0, newFiles.length - 1));
    }
  };

  const setAsCover = (index: number) => {
    if (index === 0) return;
    // Swap so index becomes index 0
    const nextFiles = [...files];
    const nextPreviews = [...previews];
    const [f] = nextFiles.splice(index, 1);
    const [p] = nextPreviews.splice(index, 1);
    nextFiles.unshift(f);
    nextPreviews.unshift(p);
    setFiles(nextFiles);
    setPreviews(nextPreviews);
    setActiveCoverIdx(0);
    toast.success("Cover photo updated");
  };

  const fallbackLocation = async (reason?: string) => {
    // 1. If user profile has coordinates
    if (profile?.latitude && profile?.longitude) {
      setLoc({ lat: profile.latitude, lng: profile.longitude });
      if (!address && profile.city) {
        setAddress(`Home, ${profile.city}`);
      }
      toast.info("Using saved location from your profile.");
      return;
    }

    // 2. Query backend IP/network resolver
    try {
      const out = await api<{ lat: number; lng: number; city?: string; hint?: string }>(
        "/map/location/resolve",
        {
          method: "POST",
          body: { client_error: { code: 3, message: reason || "Position acquisition timed out" } },
        }
      );
      if (out?.lat && out?.lng) {
        setLoc({ lat: out.lat, lng: out.lng });
        if (!address && out.city) {
          setAddress(out.city);
        }
        toast.info(out.hint || "Using approximate location. Please verify your address below.");
        return;
      }
    } catch {
      // Ignore network errors on fallback
    }

    // 3. User friendly guidance
    toast.info("GPS acquisition timed out. Please enter your pickup address manually below.");
  };

  const locate = async () => {
    if (typeof navigator === "undefined" || !navigator.geolocation) {
      await fallbackLocation("Geolocation is not supported by your browser");
      return;
    }

    setLocating(true);

    const tryGetPosition = (options: PositionOptions) =>
      new Promise<GeolocationPosition>((resolve, reject) => {
        navigator.geolocation.getCurrentPosition(resolve, reject, options);
      });

    try {
      let p: GeolocationPosition | null = null;
      // Step 1: Fast attempt (5s)
      try {
        p = await tryGetPosition({ enableHighAccuracy: true, timeout: 5000, maximumAge: 60000 });
      } catch {
        // Step 2: Low-accuracy network / Wi-Fi (avoids GPS hardware freeze on laptops/desktops)
        try {
          p = await tryGetPosition({ enableHighAccuracy: false, timeout: 8000, maximumAge: 300000 });
        } catch {
          // Step 3: Check cached position
          p = await tryGetPosition({ enableHighAccuracy: false, timeout: 4000, maximumAge: Infinity });
        }
      }

      if (p) {
        setLoc({ lat: p.coords.latitude, lng: p.coords.longitude });
        toast.success("Location pinned ✓");
      } else {
        await fallbackLocation("Position acquisition timed out");
      }
    } catch (err: any) {
      await fallbackLocation(err?.message || "Position acquisition timed out");
    } finally {
      setLocating(false);
    }
  };

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (files.length === 0) {
      toast.error("Add at least one photo of your scrap");
      return;
    }
    setBusy(true);
    try {
      // 1) Upload all photos in parallel
      const image_urls = await uploadImages(files);

      // 2) Create the listing with all convenient fields
      const listing = await api<ApiListing>("/listings", {
        method: "POST",
        body: {
          title,
          category,
          weight_kg: Number(weight),
          expected_price: Number(price),
          description: desc || null,
          address,
          image_urls,
          pickup_time_slot: timeSlot,
          floor_access: floorAccess,
          is_negotiable: isNegotiable,
          packaging_type: packagingType,
          ...(loc ? { latitude: loc.lat, longitude: loc.lng } : {}),
        },
      });
      toast.success("Posted! Collectors can now make offers.");
      navigate({ to: "/listings/$id", params: { id: listing.id } });
    } catch (err) {
      toast.error(errorMessage(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <form onSubmit={submit} className="mx-auto max-w-xl space-y-6 pb-12">
      <div>
        <h1 className="text-2xl font-extrabold md:text-3xl">Sell your scrap</h1>
        <p className="text-sm text-muted-foreground mt-0.5">Post photos and pickup details to receive bids from nearby collectors.</p>
      </div>

      {/* 1. PHOTOS SECTION (Multiple photos support) */}
      <div className="space-y-3">
        <div className="flex items-center justify-between">
          <Label className="text-sm font-semibold">
            Photos of the scrap <span className="text-xs font-normal text-muted-foreground">({files.length}/{MAX_PHOTOS})</span>
          </Label>
          {files.length > 0 && files.length < MAX_PHOTOS && (
            <label className="flex cursor-pointer items-center gap-1 text-xs font-semibold text-primary hover:underline">
              <Plus className="size-3.5" /> Add more
              <input
                type="file"
                multiple
                accept="image/*"
                className="hidden"
                onChange={(e) => addFiles(e.target.files)}
              />
            </label>
          )}
        </div>

        {files.length === 0 ? (
          <label className="flex aspect-[16/10] cursor-pointer flex-col items-center justify-center overflow-hidden rounded-3xl border-2 border-dashed border-primary/30 bg-card p-6 text-muted-foreground transition hover:border-primary hover:bg-secondary/40">
            <div className="rounded-2xl bg-secondary p-4 text-primary">
              <Camera className="size-8" />
            </div>
            <span className="mt-3 font-semibold text-foreground">Add scrap photos</span>
            <span className="text-xs text-muted-foreground">Select multiple pictures from gallery or camera (up to 6)</span>
            <span className="mt-2 rounded-full bg-primary/10 px-3 py-1 text-[11px] font-medium text-primary">
              Tip: Clear photos get 3x faster offers!
            </span>
            <input
              type="file"
              multiple
              accept="image/*"
              className="hidden"
              onChange={(e) => addFiles(e.target.files)}
            />
          </label>
        ) : (
          <div className="space-y-2.5">
            {/* Primary Cover Preview */}
            <div className="relative aspect-[16/10] overflow-hidden rounded-3xl border bg-black">
              <img src={previews[0]} alt="Cover preview" className="size-full object-cover" />
              <div className="absolute left-3 top-3 flex items-center gap-1.5 rounded-full bg-black/70 px-3 py-1 text-xs font-semibold text-white shadow backdrop-blur">
                <Sparkles className="size-3.5 text-yellow-400" /> Main cover photo
              </div>
              <button
                type="button"
                onClick={() => removePhoto(0)}
                className="absolute right-3 top-3 flex size-8 items-center justify-center rounded-full bg-black/70 text-white transition hover:bg-destructive hover:text-destructive-foreground"
                aria-label="Remove photo"
              >
                <X className="size-4" />
              </button>
            </div>

            {/* Thumbnails row */}
            <div className="grid grid-cols-4 gap-2 sm:grid-cols-6">
              {previews.map((src, i) => (
                <div
                  key={src}
                  className={cn(
                    "group relative aspect-square overflow-hidden rounded-2xl border-2 transition",
                    i === 0 ? "border-primary ring-2 ring-primary/20" : "border-border hover:border-primary/50",
                  )}
                >
                  <img src={src} alt={`Thumbnail ${i + 1}`} className="size-full object-cover" />
                  {i === 0 ? (
                    <span className="absolute bottom-1 left-1 rounded bg-primary px-1 text-[9px] font-bold text-primary-foreground">
                      Cover
                    </span>
                  ) : (
                    <button
                      type="button"
                      onClick={() => setAsCover(i)}
                      className="absolute inset-0 flex items-center justify-center bg-black/40 opacity-0 transition group-hover:opacity-100"
                    >
                      <span className="rounded bg-black/80 px-1 text-[10px] font-medium text-white">Make cover</span>
                    </button>
                  )}
                  <button
                    type="button"
                    onClick={(e) => {
                      e.stopPropagation();
                      removePhoto(i);
                    }}
                    className="absolute right-1 top-1 flex size-5 items-center justify-center rounded-full bg-black/70 text-white transition hover:bg-destructive"
                  >
                    <X className="size-3" />
                  </button>
                </div>
              ))}

              {files.length < MAX_PHOTOS && (
                <label className="flex aspect-square cursor-pointer flex-col items-center justify-center rounded-2xl border-2 border-dashed border-border bg-card text-muted-foreground transition hover:border-primary hover:bg-secondary/40">
                  <Plus className="size-5" />
                  <span className="text-[10px] font-medium mt-1">Add photo</span>
                  <input
                    type="file"
                    multiple
                    accept="image/*"
                    className="hidden"
                    onChange={(e) => addFiles(e.target.files)}
                  />
                </label>
              )}
            </div>
          </div>
        )}
      </div>

      {/* 2. CATEGORY SELECTION */}
      <div className="space-y-2">
        <Label className="text-sm font-semibold">Scrap Category</Label>
        <div className="grid grid-cols-3 gap-2 sm:grid-cols-4">
          {CATEGORIES.map((c) => (
            <button
              key={c.id}
              type="button"
              onClick={() => setCategory(c.id)}
              className={cn(
                "rounded-2xl border p-2.5 text-center text-xs font-semibold transition",
                category === c.id
                  ? "border-primary bg-primary/10 text-primary ring-1 ring-primary"
                  : "bg-card text-muted-foreground hover:border-border hover:bg-secondary/50",
              )}
            >
              <div className="text-2xl mb-1">{c.emoji}</div>
              {c.label}
            </button>
          ))}
        </div>
      </div>

      {/* 3. TITLE */}
      <div className="space-y-1.5">
        <Label htmlFor="title" className="text-sm font-semibold">Title</Label>
        <Input
          id="title"
          required
          minLength={3}
          placeholder="e.g. 5 Cartons of old newspapers & notebooks"
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          className="h-11 rounded-xl"
        />
      </div>

      {/* 4. WEIGHT & EXPECTED PRICE */}
      <div className="grid grid-cols-2 gap-3">
        <div className="space-y-1.5">
          <Label htmlFor="weight" className="text-sm font-semibold">Approx Weight (kg)</Label>
          <Input
            id="weight"
            required
            type="number"
            inputMode="decimal"
            step="0.1"
            min="0.1"
            placeholder="e.g. 15.0"
            value={weight}
            onChange={(e) => setWeight(e.target.value)}
            className="h-11 rounded-xl"
          />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="price" className="text-sm font-semibold">Expected Price (₹)</Label>
          <Input
            id="price"
            required
            type="number"
            inputMode="numeric"
            min="0"
            placeholder="e.g. 350"
            value={price}
            onChange={(e) => setPrice(e.target.value)}
            className="h-11 rounded-xl font-bold"
          />
        </div>
      </div>

      {/* 5. CONVENIENT SELLER FIELDS (New Fields) */}
      <div className="rounded-3xl border bg-card p-4 sm:p-5 space-y-4">
        <h2 className="text-sm font-bold tracking-tight text-foreground flex items-center gap-1.5">
          <Clock className="size-4 text-primary" /> Pickup & Logistics Preferences
        </h2>

        {/* Preferred Pickup Time */}
        <div className="space-y-2">
          <Label className="text-xs font-semibold text-muted-foreground">Preferred Pickup Time</Label>
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
            {TIME_SLOTS.map((t) => (
              <button
                key={t.id}
                type="button"
                onClick={() => setTimeSlot(t.id)}
                className={cn(
                  "flex items-center gap-2 rounded-xl border p-2 text-left transition",
                  timeSlot === t.id
                    ? "border-primary bg-primary/10 text-foreground ring-1 ring-primary"
                    : "border-border bg-background text-muted-foreground hover:bg-secondary/40",
                )}
              >
                <span className="text-lg">{t.emoji}</span>
                <div className="min-w-0">
                  <p className="text-xs font-semibold truncate leading-tight">{t.label}</p>
                  <p className="text-[10px] text-muted-foreground truncate leading-tight">{t.sub}</p>
                </div>
              </button>
            ))}
          </div>
        </div>

        {/* Floor & Accessibility */}
        <div className="space-y-2">
          <Label className="text-xs font-semibold text-muted-foreground flex items-center gap-1">
            <Building2 className="size-3.5 text-primary" /> Floor / Accessibility
          </Label>
          <div className="grid grid-cols-2 gap-2">
            {FLOOR_OPTIONS.map((f) => (
              <button
                key={f.id}
                type="button"
                onClick={() => setFloorAccess(f.id)}
                className={cn(
                  "flex items-center gap-2 rounded-xl border p-2.5 text-left text-xs font-medium transition",
                  floorAccess === f.id
                    ? "border-primary bg-primary/10 text-foreground ring-1 ring-primary"
                    : "border-border bg-background text-muted-foreground hover:bg-secondary/40",
                )}
              >
                <span className="text-base">{f.emoji}</span>
                <span className="truncate">{f.label}</span>
              </button>
            ))}
          </div>
        </div>

        {/* Packaging / Condition */}
        <div className="space-y-2">
          <Label className="text-xs font-semibold text-muted-foreground flex items-center gap-1">
            <Package className="size-3.5 text-primary" /> How is it packed?
          </Label>
          <div className="grid grid-cols-2 gap-2">
            {PACKAGING_OPTIONS.map((p) => (
              <button
                key={p.id}
                type="button"
                onClick={() => setPackagingType(p.id)}
                className={cn(
                  "flex items-center gap-2 rounded-xl border p-2.5 text-left text-xs font-medium transition",
                  packagingType === p.id
                    ? "border-primary bg-primary/10 text-foreground ring-1 ring-primary"
                    : "border-border bg-background text-muted-foreground hover:bg-secondary/40",
                )}
              >
                <span className="text-base">{p.emoji}</span>
                <span className="truncate">{p.label}</span>
              </button>
            ))}
          </div>
        </div>

        {/* Price Negotiable Toggle */}
        <div className="flex items-center justify-between rounded-2xl border bg-background p-3">
          <div>
            <p className="text-xs font-bold">Open to counter-offers / Negotiable</p>
            <p className="text-[11px] text-muted-foreground">Collectors can bid higher or negotiate fair value.</p>
          </div>
          <Switch checked={isNegotiable} onCheckedChange={setIsNegotiable} />
        </div>
      </div>

      {/* 6. DETAILS / DESCRIPTION */}
      <div className="space-y-1.5">
        <Label htmlFor="desc" className="text-sm font-semibold">Additional Details <span className="text-xs font-normal text-muted-foreground">(Optional)</span></Label>
        <Textarea
          id="desc"
          placeholder="Any specific instructions, gate code, landmarks, or special items…"
          value={desc}
          onChange={(e) => setDesc(e.target.value)}
          className="rounded-xl min-h-[80px]"
        />
      </div>

      {/* 7. PICKUP ADDRESS */}
      <div className="space-y-2">
        <Label htmlFor="address" className="text-sm font-semibold">Pickup Address</Label>
        <Input
          id="address"
          required
          minLength={5}
          placeholder="House/Flat number, building, street, area, city"
          value={address}
          onChange={(e) => setAddress(e.target.value)}
          className="h-11 rounded-xl"
        />
        <Button
          type="button"
          variant="outline"
          size="sm"
          onClick={locate}
          disabled={locating}
          className="rounded-xl text-xs"
        >
          {locating ? <Loader2 className="mr-1.5 size-3.5 animate-spin text-primary" /> : <LocateFixed className="mr-1.5 size-3.5" />}
          {locating ? "Getting current location…" : loc ? "Location pinned ✓" : "Use current GPS location"}
        </Button>
      </div>

      {/* SUBMIT BUTTON */}
      <Button
        type="submit"
        size="lg"
        variant="accent"
        className="w-full h-12 text-base font-bold rounded-2xl shadow-soft"
        disabled={busy}
      >
        {busy ? (
          <>
            <Loader2 className="mr-2 size-5 animate-spin" />
            Posting listing & uploading photos…
          </>
        ) : (
          `Post scrap listing (${files.length} photo${files.length === 1 ? "" : "s"})`
        )}
      </Button>
    </form>
  );
}
