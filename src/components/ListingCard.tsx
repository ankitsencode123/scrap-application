import { Link } from "@tanstack/react-router";
import { MapPin, Scale } from "lucide-react";
import type { Tables } from "@/integrations/supabase/types";
import { categoryOf, formatINR, statusLabel, usePhotoUrl } from "@/lib/scrap";

export function ListingCard({ listing, distance }: { listing: Tables<"listings">; distance?: number }) {
  const { data: url } = usePhotoUrl(listing.photo_path);
  const cat = categoryOf(listing.category);
  return (
    <Link
      to="/listings/$id"
      params={{ id: listing.id }}
      className="group flex gap-3 rounded-2xl border bg-card p-3 transition hover:shadow-soft md:flex-col md:p-0 md:overflow-hidden"
    >
      <div className="size-24 shrink-0 overflow-hidden rounded-xl bg-muted md:aspect-[4/3] md:size-auto md:w-full md:rounded-none">
        {url ? (
          <img src={url} alt={listing.title} loading="lazy" className="size-full object-cover transition group-hover:scale-105" />
        ) : (
          <div className="flex size-full items-center justify-center text-3xl">{cat.emoji}</div>
        )}
      </div>
      <div className="flex min-w-0 flex-1 flex-col gap-1 md:p-4">
        <div className="flex items-center justify-between gap-2">
          <span className="truncate text-xs font-medium text-muted-foreground">
            {cat.emoji} {cat.label}
          </span>
          <span className="shrink-0 rounded-full bg-secondary px-2 py-0.5 text-[11px] font-semibold text-secondary-foreground">
            {statusLabel(listing.status)}
          </span>
        </div>
        <h3 className="truncate text-base font-bold">{listing.title}</h3>
        <div className="flex items-center gap-3 text-sm text-muted-foreground">
          <span className="flex items-center gap-1"><Scale className="size-3.5" />{listing.weight_kg} kg</span>
          {distance != null && (
            <span className="flex items-center gap-1"><MapPin className="size-3.5" />{distance.toFixed(1)} km</span>
          )}
        </div>
        <div className="mt-auto text-lg font-extrabold text-primary">
          {formatINR(listing.final_price ?? listing.expected_price)}
          {!listing.final_price && listing.expected_price != null && (
            <span className="ml-1 text-xs font-normal text-muted-foreground">expected</span>
          )}
        </div>
      </div>
    </Link>
  );
}
