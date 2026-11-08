import { useEffect, useRef } from "react";
import L from "leaflet";
import type { ApiListing } from "@/lib/api";
import { categoryOf, formatINR } from "@/lib/scrap";

export default function ScrapMap({
  listings,
  center,
  onSelect,
}: {
  listings: ApiListing[];
  center: { lat: number; lng: number };
  onSelect: (id: string) => void;
}) {
  const el = useRef<HTMLDivElement>(null);
  const map = useRef<L.Map | null>(null);
  const layer = useRef<L.LayerGroup | null>(null);

  useEffect(() => {
    if (!el.current || map.current) return;
    map.current = L.map(el.current, { zoomControl: false }).setView([center.lat, center.lng], 13);
    L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
      attribution: "© OpenStreetMap",
      maxZoom: 19,
    }).addTo(map.current);
    L.control.zoom({ position: "bottomright" }).addTo(map.current);
    layer.current = L.layerGroup().addTo(map.current);
    return () => { map.current?.remove(); map.current = null; };
  }, []);

  useEffect(() => { map.current?.setView([center.lat, center.lng]); }, [center.lat, center.lng]);

  useEffect(() => {
    if (!layer.current) return;
    layer.current.clearLayers();
    L.circleMarker([center.lat, center.lng], { radius: 8, color: "#fff", weight: 3, fillColor: "#2f6fd6", fillOpacity: 1 }).addTo(layer.current);
    listings.forEach((l) => {
      if (l.latitude == null || l.longitude == null) return;
      const icon = L.divIcon({
        className: "",
        html: `<div style="background:#2f5a43;color:#fff;border-radius:999px;padding:4px 8px;font:600 12px Figtree,sans-serif;white-space:nowrap;box-shadow:0 4px 10px rgba(0,0,0,.25)">${categoryOf(l.category).emoji} ${formatINR(l.expected_price)}</div>`,
        iconAnchor: [30, 14],
      });
      L.marker([l.latitude, l.longitude], { icon }).on("click", () => onSelect(l.id)).addTo(layer.current!);
    });
  }, [listings, center.lat, center.lng, onSelect]);

  return <div ref={el} className="size-full" />;
}
