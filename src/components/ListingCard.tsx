import { useEffect, useState } from "react";
import { Link } from "@tanstack/react-router";
import { MapPin, Scale } from "lucide-react";
import { resolveMediaUrl, type ApiListing } from "@/lib/api";
import { categoryOf, formatINR, statusLabel } from "@/lib/scrap";

export function ListingCard({ listing, distance }: { listing: ApiListing; distance?: number | null | undefined }) {
  const [imgError, setImgError] = useState(false);
  const rawUrl = listing.images[0]?.url;

  useEffect(() => {
    setImgError(false);
  }, [rawUrl]);

  const url = !imgError ? resolveMediaUrl(rawUrl) : null;
  distance = distance ?? listing.distance_km;
  const cat = categoryOf(listing.category);
  return (
    <Link
      to="/listings/$id"
      params={{ id: listing.id }}
      className="group flex gap-3 rounded-2xl border bg-card p-3 transition hover:shadow-soft md:flex-col md:p-0 md:overflow-hidden"
    >
      <div className="relative size-24 shrink-0 overflow-hidden rounded-xl bg-muted md:aspect-[4/3] md:size-auto md:w-full md:rounded-none">
        {url ? (
          <img
            src={url}
            alt={listing.title}
            loading="lazy"
            onError={() => setImgError(true)}
            className="size-full object-cover transition group-hover:scale-105"
          />
        ) : (
          <div className="flex size-full items-center justify-center text-3xl">{cat.emoji}</div>
        )}
        {listing.images.length > 1 && (
          <span className="absolute bottom-1.5 right-1.5 rounded-md bg-black/70 px-1.5 py-0.5 text-[10px] font-semibold text-white backdrop-blur-xs">
            📷 {listing.images.length}
          </span>
        )}
      </div>
      <div className="flex min-w-0 flex-1 flex-col gap-1 md:p-4">
        <div className="flex items-center justify-between gap-2">
          <span className="truncate text-xs font-medium text-muted-foreground">
            {cat.emoji} {cat.label}
          </span>
          <div className="flex items-center gap-1 shrink-0">
            {listing.is_negotiable && (
              <span className="rounded-full bg-emerald-50 px-1.5 py-0.5 text-[10px] font-semibold text-emerald-700 dark:bg-emerald-950/60 dark:text-emerald-300">
                Negotiable
              </span>
            )}
            <span className="rounded-full bg-secondary px-2 py-0.5 text-[11px] font-semibold text-secondary-foreground">
              {statusLabel(listing.status)}
            </span>
          </div>
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
          {listing.final_price == null && (
            <span className="ml-1 text-xs font-normal text-muted-foreground">expected</span>
          )}
        </div>
      </div>
    </Link>
  );
}
