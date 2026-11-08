import { createFileRoute } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { Camera, Loader2 } from "lucide-react";
import { toast } from "sonner";
import { api, errorMessage, uploadAvatar, type ApiReview, type ApiStats, type ApiUser, type Page } from "@/lib/api";
import { useAuth } from "@/hooks/use-auth";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Stars } from "@/components/Stars";
import { UserAvatar } from "@/components/UserAvatar";
import { formatINR } from "@/lib/scrap";

export const Route = createFileRoute("/_authenticated/profile")({
  head: () => ({ meta: [{ title: "Profile — scrappe" }, { name: "description", content: "Your scrappe profile, ratings and reviews." }, { property: "og:title", content: "Profile — scrappe" }, { property: "og:description", content: "Your scrappe profile, ratings and reviews." }, { property: "og:type", content: "website" }, { name: "twitter:card", content: "summary" }] }),
  component: ProfilePage,
});

function ProfilePage() {
  const { user, profile, refreshProfile, signOut } = useAuth();
  const [name, setName] = useState("");
  const [phone, setPhone] = useState("");
  const [city, setCity] = useState("");
  const [saving, setSaving] = useState(false);
  const [uploadingAvatar, setUploadingAvatar] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  useEffect(() => { setName(profile?.display_name ?? ""); setPhone(profile?.phone ?? ""); setCity(profile?.city ?? ""); }, [profile]);
  const { data: reviews = [] } = useQuery({
    queryKey: ["reviewsOf", user?.id],
    enabled: !!user,
    queryFn: async () => (await api<Page<ApiReview>>("/users/me/reviews", { query: { limit: 100 } })).items,
  });
  const { data: stats } = useQuery({
    queryKey: ["stats", user?.id],
    enabled: !!user,
    queryFn: () => api<ApiStats>("/users/me/stats"),
  });
  const r = { avg: profile?.rating_avg ?? 0, count: profile?.rating_count ?? 0 };

  const handleAvatarChange = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    setUploadingAvatar(true);
    try {
      await uploadAvatar(file);
      await refreshProfile();
      toast.success("Profile photo updated");
    } catch (err) {
      toast.error(errorMessage(err));
    } finally {
      setUploadingAvatar(false);
      if (fileInputRef.current) fileInputRef.current.value = "";
    }
  };

  const save = async () => {
    setSaving(true);
    try {
      await api<ApiUser>("/users/me", { method: "PATCH", body: { display_name: name, phone: phone || null, city: city || null } });
    } catch (err) {
      toast.error(errorMessage(err));
      return;
    } finally {
      setSaving(false);
    }
    toast.success("Saved");
    refreshProfile();
  };

  return (
    <div className="mx-auto max-w-xl space-y-5">
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 rounded-3xl border bg-card p-5">
        <div className="flex items-center gap-4 min-w-0">
          <div className="relative shrink-0">
            <UserAvatar
              src={profile?.avatar_url}
              name={profile?.display_name || user?.email}
              className="size-16"
              textClassName="text-2xl"
            />
            <button
              type="button"
              onClick={() => fileInputRef.current?.click()}
              disabled={uploadingAvatar}
              aria-label="Upload profile photo"
              className="absolute -bottom-1 -right-1 flex size-7 items-center justify-center rounded-full bg-primary text-primary-foreground border-2 border-card shadow hover:bg-primary/90 transition-colors disabled:opacity-50"
            >
              {uploadingAvatar ? (
                <Loader2 className="size-3.5 animate-spin" />
              ) : (
                <Camera className="size-3.5" />
              )}
            </button>
            <input
              ref={fileInputRef}
              type="file"
              accept="image/*"
              className="hidden"
              onChange={handleAvatarChange}
            />
          </div>
          <div className="flex-1 min-w-0">
            <h1 className="text-xl font-extrabold truncate">{profile?.display_name || "Your profile"}</h1>
            <p className="text-sm text-muted-foreground truncate">
              <span className="capitalize">{profile?.role}</span> · {user?.email}
            </p>
            <div className="mt-1 flex items-center gap-2 text-sm"><Stars value={r.avg} /> {r.avg ? r.avg.toFixed(1) : "—"} ({r.count})</div>
          </div>
        </div>
        <Button
          type="button"
          variant="outline"
          size="sm"
          className="gap-1.5 text-xs self-start sm:self-center shrink-0"
          onClick={() => fileInputRef.current?.click()}
          disabled={uploadingAvatar}
        >
          {uploadingAvatar ? <Loader2 className="size-3.5 animate-spin" /> : <Camera className="size-3.5" />}
          Change Photo
        </Button>
      </div>
      <div className="grid grid-cols-2 gap-3">
        <div className="rounded-2xl border bg-card p-4"><p className="text-xs text-muted-foreground">Pickups done</p><p className="text-2xl font-extrabold">{stats?.completed_pickups ?? 0}</p></div>
        <div className="rounded-2xl border bg-card p-4"><p className="text-xs text-muted-foreground">{profile?.role === "collector" ? "Total paid" : "Total earned"}</p><p className="text-2xl font-extrabold text-primary">{formatINR(stats?.total_amount ?? 0)}</p></div>
      </div>
      <section className="space-y-3 rounded-3xl border bg-card p-5">
        <h2 className="font-extrabold">Details</h2>
        <div><Label>Name</Label><Input value={name} onChange={(e) => setName(e.target.value)} /></div>
        <div><Label>Phone</Label><Input value={phone} onChange={(e) => setPhone(e.target.value)} inputMode="tel" /></div>
        <div><Label>City</Label><Input value={city} onChange={(e) => setCity(e.target.value)} /></div>
        <Button className="w-full" disabled={saving} onClick={save}>
          {saving && <Loader2 className="mr-2 size-4 animate-spin" />}
          Save
        </Button>
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
