import { createFileRoute } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";
import { supabase } from "@/integrations/supabase/client";
import { categoryOf, formatINR, statusLabel, usePhotoUrl } from "@/lib/scrap";

export const Route = createFileRoute("/_authenticated/listings/$id")({
  head: () => ({ meta: [{ title: "Scrap listing — scrappe" }, { name: "description", content: "Scrap listing details." }, { property: "og:title", content: "Scrap listing — scrappe" }, { property: "og:description", content: "Scrap listing details." }] }),
  component: ListingPage,
});

function ListingPage() {
  const { id } = Route.useParams();
  const { data: l } = useQuery({
    queryKey: ["listing", id],
    queryFn: async () => (await supabase.from("listings").select("*").eq("id", id).maybeSingle()).data,
  });
  const { data: url } = usePhotoUrl(l?.photo_path);
  if (!l) return <p className="text-muted-foreground">Loading…</p>;
  return (
    <div className="mx-auto max-w-xl space-y-4">
      {url && <img src={url} alt={l.title} className="aspect-[4/3] w-full rounded-3xl object-cover" />}
      <p className="text-sm text-muted-foreground">{categoryOf(l.category).emoji} {categoryOf(l.category).label} · {statusLabel(l.status)}</p>
      <h1 className="text-2xl font-extrabold">{l.title}</h1>
      <p>{l.weight_kg} kg · <span className="font-bold text-primary">{formatINR(l.expected_price)}</span></p>
      {l.description && <p className="text-muted-foreground">{l.description}</p>}
      {l.address && <p className="text-sm">📍 {l.address}</p>}
    </div>
  );
}
