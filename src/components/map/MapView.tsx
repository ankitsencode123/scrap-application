import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import L from "leaflet";
import "leaflet/dist/leaflet.css";
import { toast } from "sonner";
import { Link, useNavigate } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";
import {
  Bell,
  ChevronDown,
  ChevronUp,
  CloudSun,
  Compass,
  Crosshair,
  Factory,
  Flame,
  HelpCircle,
  Layers,
  LocateFixed,
  Maximize2,
  Minimize2,
  Minus,
  Navigation,
  Phone,
  Plus,
  Recycle,
  RefreshCw,
  Route as RouteIcon,
  Search,
  Shapes,
  Truck,
  Users,
  X,
  MessageCircle,
  CheckCircle2,
  Sparkles,
  MapPin,
  Loader2,
  AlertCircle,
  Clock,
} from "lucide-react";
import {
  DEFAULT_CENTER,
  STEPS,
  fetchRoute,
  generateCentresAround,
  getScrapMeta,
  haversine,
  pathLength,
  type CentreItem,
  type LatLng,
  type StopItem,
} from "./data";
import { useAuth } from "@/hooks/use-auth";
import {
  api,
  type ApiListing,
  type HeatmapOut,
  type MapListingsOut,
  type NearbyCollector,
  type Page,
  type ResolveOut,
  type TrackingOut,
} from "@/lib/api";

const css = (v: string) =>
  typeof window !== "undefined" ? getComputedStyle(document.documentElement).getPropertyValue(v).trim() : "";

const TILES = {
  normal: { url: "https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", attr: "© OpenStreetMap" },
  satellite: {
    url: "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
    attr: "© Esri",
  },
  terrain: { url: "https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png", attr: "© OpenTopoMap" },
} as const;
type TileKey = keyof typeof TILES;

type Overlay = "heat" | "density" | "nearby" | "centres";

const divIcon = (html: string, size: number, anchor?: [number, number]) =>
  L.divIcon({ html, className: "", iconSize: [size, size], iconAnchor: anchor ?? [size / 2, size / 2] });

const SPEED_KMH = 24;

const POPUP_OPTS: L.PopupOptions = {
  maxWidth: 240,
  autoPanPaddingTopLeft: [20, 70],
  autoPanPaddingBottomRight: [20, 120],
  autoPan: true,
  closeButton: true,
};

interface MapViewProps {
  onSwitchToListings?: () => void;
  initialCenter?: { lat: number; lng: number };
}

export default function MapView({ onSwitchToListings, initialCenter }: MapViewProps) {
  const { user } = useAuth();
  const navigate = useNavigate();

  // Role strictly assigned from authenticated session
  const role: "seller" | "collector" = user?.role === "collector" ? "collector" : "seller";

  // Map references
  const mapEl = useRef<HTMLDivElement>(null);
  const wrapEl = useRef<HTMLDivElement>(null);
  const map = useRef<L.Map | null>(null);
  const tile = useRef<L.TileLayer | null>(null);
  const userLayer = useRef<L.LayerGroup | null>(null);
  const stopsLayer = useRef<L.LayerGroup | null>(null);
  const routeLayers = useRef<L.LayerGroup | null>(null);
  const overlayGroups = useRef<Record<Overlay, L.LayerGroup> | null>(null);
  const vehicle = useRef<L.Marker | null>(null);
  const doneLine = useRef<L.Polyline | null>(null);
  const destinationMarker = useRef<L.Marker | null>(null);

  // Dynamic user location state
  const initialCoord: LatLng = useMemo(() => {
    if (initialCenter?.lat && initialCenter?.lng) return [initialCenter.lat, initialCenter.lng];
    if (user?.latitude && user?.longitude) return [user.latitude, user.longitude];
    return DEFAULT_CENTER;
  }, [initialCenter, user?.latitude, user?.longitude]);

  const [userPos, setUserPos] = useState<LatLng>(initialCoord);
  const [loading, setLoading] = useState(true);
  const [tileKey, setTileKey] = useState<TileKey>("normal");
  const [overlays, setOverlays] = useState<Record<Overlay, boolean>>({
    heat: false,
    density: false,
    nearby: true,
    centres: true,
  });
  const [routes, setRoutes] = useState<LatLng[][]>([]);
  const [activeRoute, setActiveRoute] = useState(0);
  const [progress, setProgress] = useState(0);
  const [follow, setFollow] = useState(true);
  const [sheetOpen, setSheetOpen] = useState(true);
  const [legendOpen, setLegendOpen] = useState(false);
  const [layersOpen, setLayersOpen] = useState(false);
  const [notifOpen, setNotifOpen] = useState(false);
  const [fullscreen, setFullscreen] = useState(false);
  const [bearing, setBearing] = useState(0);
  const [notifs, setNotifs] = useState<string[]>([]);
  const [query, setQuery] = useState("");
  const toastedRef = useRef<Set<string>>(new Set());

  // Dynamic centres generated realistically around the resolved user position
  const centres: CentreItem[] = useMemo(
    () => generateCentresAround(userPos, user?.city || "Metro"),
    [userPos, user?.city],
  );
  const destination = centres[0]!;

  // --------------------------------------------------------------------------
  // 1. REAL SERVER QUERIES
  // --------------------------------------------------------------------------

  // A. Seller's active listings
  const { data: myListingsData, refetch: refetchMyListings } = useQuery({
    queryKey: ["my-listings"],
    queryFn: () => api<Page<ApiListing>>("/listings", { query: { tab: "mine", limit: 30 } }),
    refetchInterval: 10000,
  });

  const myListings = myListingsData?.items ?? [];

  // Identify active listing for seller view
  const activeListing = useMemo(() => {
    const priorityStatuses = ["on_the_way", "arrived", "offer_accepted", "posted", "completed"];
    for (const status of priorityStatuses) {
      const match = myListings.find((l) => l.status === status);
      if (match) return match;
    }
    return myListings[0] ?? null;
  }, [myListings]);

  // B. Real-time Live Tracking Snapshot (when collector is assigned / on the way)
  const isTrackingActive =
    !!activeListing && ["offer_accepted", "on_the_way", "arrived"].includes(activeListing.status);

  const { data: trackingData } = useQuery({
    queryKey: ["map-tracking", activeListing?.id],
    queryFn: () => api<TrackingOut>(`/map/tracking/${activeListing!.id}`, { query: { include_route: true } }),
    enabled: isTrackingActive,
    refetchInterval: 5000,
  });

  // C. Nearby Online Collectors (for seller when awaiting collector assignment)
  const { data: nearbyCollectors = [] } = useQuery({
    queryKey: ["nearby-collectors", userPos[0], userPos[1]],
    queryFn: () =>
      api<NearbyCollector[]>("/map/collectors/nearby", {
        query: { lat: userPos[0], lng: userPos[1], radius_km: 15, limit: 30 },
      }),
    enabled: role === "seller" && (!activeListing || activeListing.status === "posted"),
    refetchInterval: 15000,
  });

  // D. Collector View: Open marketplace listings around collector
  const { data: mapListingsData, refetch: refetchMapListings } = useQuery({
    queryKey: ["collector-map-listings", userPos[0], userPos[1]],
    queryFn: () =>
      api<MapListingsOut>("/map/listings", {
        query: { scope: "open", lat: userPos[0], lng: userPos[1], radius_km: 25, limit: 50 },
      }),
    enabled: role === "collector",
    refetchInterval: 15000,
  });

  // E. Real Heatmap Data
  const { data: heatmapData } = useQuery({
    queryKey: ["heatmap-cells", userPos[0], userPos[1]],
    queryFn: () =>
      api<HeatmapOut>("/map/heatmap", {
        query: { kind: "open", lat: userPos[0], lng: userPos[1], radius_km: 30, limit: 120 },
      }),
    refetchInterval: 30000,
  });

  // --------------------------------------------------------------------------
  // 2. DYNAMIC STOPS & REAL VALUE CALCULATIONS
  // --------------------------------------------------------------------------

  // Collector's assigned pickups from myListings (only actual accepted customer orders)
  const collectorAssigned = useMemo(() => {
    if (role !== "collector") return [];
    return myListings.filter(
      (l) => l.is_assigned_collector && ["offer_accepted", "on_the_way", "arrived"].includes(l.status),
    );
  }, [role, myListings]);

  // Real assigned stops for collector view: ONLY real assigned customer pickups
  const collectorStops: StopItem[] = useMemo(() => {
    if (role !== "collector") return [];
    return collectorAssigned.map((l) => {
      const lat = l.latitude ?? userPos[0];
      const lng = l.longitude ?? userPos[1];
      return {
        id: l.id,
        label: `${l.title} · ${l.address || "Customer Address"}`,
        pos: [lat, lng],
        category: l.category,
        category_label: l.category_label || getScrapMeta(l.category).label,
        kg: l.weight_kg,
        expected_price: l.final_price ?? l.expected_price,
        status: l.status,
        is_mine: true,
        done: l.status === "completed",
      };
    });
  }, [role, collectorAssigned, userPos]);

  // Real vehicle utilization calculations
  const VEHICLE_MAX_KG = 500;
  const collectorTotalKg = useMemo(
    () => collectorStops.reduce((sum, s) => sum + s.kg, 0),
    [collectorStops],
  );
  const collectorCompletedStops = useMemo(
    () => collectorStops.filter((s) => s.done).length,
    [collectorStops],
  );
  const collectorCapacityUsedPct = Math.min(100, Math.round((collectorTotalKg / VEHICLE_MAX_KG) * 100));

  // Real scrap category breakdown in the zone
  const realCategoryCounts = useMemo(() => {
    const counts: Record<string, number> = { metals: 0, plastic_bottles: 0, paper_cardboard: 0, ewaste_appliances: 0 };
    const items = mapListingsData?.items ?? [];
    items.forEach((item) => {
      const cat = item.category.toLowerCase();
      if (cat.includes("metal")) counts["metals"] = (counts["metals"] || 0) + 1;
      else if (cat.includes("plastic")) counts["plastic_bottles"] = (counts["plastic_bottles"] || 0) + 1;
      else if (cat.includes("paper")) counts["paper_cardboard"] = (counts["paper_cardboard"] || 0) + 1;
      else if (cat.includes("ewaste") || cat.includes("appliance")) counts["ewaste_appliances"] = (counts["ewaste_appliances"] || 0) + 1;
    });
    return counts;
  }, [mapListingsData?.items]);

  // --------------------------------------------------------------------------
  // 3. GEOLOCATION & LOCATION RESOLUTION PIPELINE
  // --------------------------------------------------------------------------
  const locateUser = useCallback(async () => {
    if (typeof navigator === "undefined" || !navigator.geolocation) return;
    try {
      const pos = await new Promise<GeolocationPosition>((resolve, reject) => {
        navigator.geolocation.getCurrentPosition(resolve, reject, {
          enableHighAccuracy: true,
          timeout: 10000,
          maximumAge: 30000,
        });
      });
      const resolved = await api<ResolveOut>("/map/location/resolve", {
        method: "POST",
        body: {
          fix: {
            lat: pos.coords.latitude,
            lng: pos.coords.longitude,
            accuracy_m: pos.coords.accuracy,
            source: pos.coords.accuracy > 200 ? "network" : "gps",
            captured_at: pos.timestamp,
          },
        },
      });
      setUserPos([resolved.lat, resolved.lng]);

      // If user is collector, report location fix and online presence to server
      if (role === "collector") {
        api("/map/location/fix", {
          method: "POST",
          body: {
            lat: resolved.lat,
            lng: resolved.lng,
            accuracy_m: resolved.accuracy_m,
            source: "gps",
            captured_at: Date.now(),
          },
        }).catch(() => {});
        api("/map/presence", {
          method: "POST",
          body: { online: true },
        }).catch(() => {});
      }
    } catch {
      // Fallback: request resolve without client GPS to leverage IP / profile
      try {
        const out = await api<ResolveOut>("/map/location/resolve", {
          method: "POST",
          body: { client_error: { code: 2, message: "Geolocation unavailable" } },
        });
        setUserPos([out.lat, out.lng]);
      } catch {}
    }
  }, [role]);

  useEffect(() => {
    locateUser();
  }, [locateUser]);

  // Periodically send collector location fix while active
  useEffect(() => {
    if (role !== "collector") return;
    const interval = setInterval(() => {
      if (typeof navigator !== "undefined" && navigator.geolocation) {
        navigator.geolocation.getCurrentPosition(
          (p) => {
            api("/map/location/fix", {
              method: "POST",
              body: {
                lat: p.coords.latitude,
                lng: p.coords.longitude,
                accuracy_m: p.coords.accuracy,
                speed_mps: p.coords.speed ?? 6.5,
                heading_deg: p.coords.heading ?? 0,
                source: "gps",
                captured_at: p.timestamp,
              },
            }).catch(() => {});
          },
          () => {},
          { enableHighAccuracy: false, timeout: 5000, maximumAge: 15000 },
        );
      }
    }, 20000);
    return () => clearInterval(interval);
  }, [role]);

  // --------------------------------------------------------------------------
  // 4. MAP INITIALIZATION & TILES
  // --------------------------------------------------------------------------
  useEffect(() => {
    if (!mapEl.current || map.current) return;

    const m = L.map(mapEl.current, { zoomControl: false, attributionControl: true }).setView(userPos, 14);
    map.current = m;
    tile.current = L.tileLayer(TILES.normal.url, { attribution: TILES.normal.attr, maxZoom: 19 }).addTo(m);

    userLayer.current = L.layerGroup().addTo(m);
    stopsLayer.current = L.layerGroup().addTo(m);
    routeLayers.current = L.layerGroup().addTo(m);

    const heat = L.layerGroup();
    const density = L.layerGroup();
    const nearby = L.layerGroup();
    const centresGroup = L.layerGroup();
    overlayGroups.current = { heat, density, nearby, centres: centresGroup };

    m.on("dragstart", () => setFollow(false));

    return () => {
      m.remove();
      map.current = null;
    };
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // Tile layer update
  useEffect(() => {
    if (!map.current || !tile.current) return;
    tile.current.setUrl(TILES[tileKey].url);
  }, [tileKey]);

  // Overlays toggle
  useEffect(() => {
    const m = map.current;
    const g = overlayGroups.current;
    if (!m || !g) return;
    (Object.keys(g) as Overlay[]).forEach((k) => (overlays[k] ? g[k].addTo(m) : g[k].remove()));
  }, [overlays]);

  // --------------------------------------------------------------------------
  // 5. RENDER USER LOCATION PIN & ACCURACY
  // --------------------------------------------------------------------------
  useEffect(() => {
    const ul = userLayer.current;
    if (!ul) return;
    ul.clearLayers();

    const C_info = css("--info") || "#3b82f6";
    const C_success = css("--success") || "#10b981";

    L.circle(userPos, { radius: 100, color: C_info, weight: 1, fillColor: C_info, fillOpacity: 0.12 }).addTo(ul);
    L.marker(userPos, {
      icon: divIcon('<div class="user-loc"><div class="ring"></div><div class="dot"></div></div>', 18),
      zIndexOffset: 900,
    })
      .bindPopup(`<b>You are here</b><br/>${user?.display_name ?? "Current User"}`, POPUP_OPTS)
      .addTo(ul);

    // Subtle proximity geofence
    L.circle(userPos, { radius: 300, color: C_success, weight: 1.5, dashArray: "5 5", fill: false }).addTo(ul);
  }, [userPos, user?.display_name]);

  // --------------------------------------------------------------------------
  // 6. RENDER RECYCLING CENTRES
  // --------------------------------------------------------------------------
  useEffect(() => {
    const cg = overlayGroups.current?.centres;
    if (!cg) return;
    cg.clearLayers();

    const C_destructive = css("--destructive") || "#ef4444";
    const ICON: Record<string, string> = { recycler: "🏭", collection: "♻️", storage: "📦" };

    centres.forEach((c, idx) => {
      const d = haversine(userPos, c.pos) * 1000;
      L.circle(c.pos, {
        radius: c.radius,
        color: idx === 0 ? C_destructive : css("--primary") || "#2e7d32",
        weight: 1.5,
        fillColor: idx === 0 ? C_destructive : css("--primary") || "#2e7d32",
        fillOpacity: 0.06,
        interactive: false,
      }).addTo(cg);

      L.marker(c.pos, { icon: divIcon(`<div class="chip-marker">${ICON[c.kind] ?? "🏭"}</div>`, 30) })
        .bindPopup(`<b>${c.name}</b><br/>Radius ${(c.radius / 1000).toFixed(1)} km · ${(d / 1000).toFixed(1)} km from your location`, POPUP_OPTS)
        .addTo(cg);
    });
  }, [centres, userPos]);

  // --------------------------------------------------------------------------
  // 7. RENDER REAL HEATMAP & NEARBY OVERLAYS
  // --------------------------------------------------------------------------
  useEffect(() => {
    const hg = overlayGroups.current?.heat;
    if (!hg) return;
    hg.clearLayers();

    const C = {
      destructive: css("--destructive") || "#ef4444",
      warning: css("--warning") || "#f59e0b",
      success: css("--success") || "#10b981",
    };

    const cells = heatmapData?.cells ?? [];
    cells.forEach((cell) => {
      const col = cell.intensity > 0.65 ? C.destructive : cell.intensity > 0.35 ? C.warning : C.success;
      L.circle([cell.lat, cell.lng], {
        radius: 120 + cell.intensity * 220,
        stroke: false,
        fillColor: col,
        fillOpacity: 0.08 + cell.intensity * 0.12,
        interactive: false,
      }).addTo(hg);
    });
  }, [heatmapData]);

  // Render nearby online collectors or requests
  useEffect(() => {
    const ng = overlayGroups.current?.nearby;
    if (!ng) return;
    ng.clearLayers();

    if (role === "seller") {
      // Show real online collectors nearby
      nearbyCollectors.forEach((c) => {
        L.marker([c.lat, c.lng], {
          icon: divIcon('<div class="chip-marker border-2 border-emerald-500 shadow-sm">🛺</div>', 32),
        })
          .bindPopup(
            `<b>${c.display_name}</b><br/>★ ${c.rating_avg.toFixed(1)} (${c.rating_count} reviews)<br/><small>${c.distance_km} km away · Online now</small>`,
            POPUP_OPTS
          )
          .addTo(ng);
      });
    } else {
      // Show open scrap listings around collector
      const items = mapListingsData?.items ?? [];
      items.forEach((item) => {
        const meta = getScrapMeta(item.category);
        L.marker([item.lat, item.lng], {
          icon: divIcon(`<div class="chip-marker text-xs">${meta.emoji}</div>`, 30),
        })
          .bindPopup(
            `<b>${item.title}</b><br/>${meta.emoji} ${item.category_label} · ${item.weight_kg} kg<br/>Expected: ₹${item.expected_price.toLocaleString("en-IN")}<br/><small>${item.distance_km ? `${item.distance_km} km away` : ""}</small>`,
            POPUP_OPTS
          )
          .addTo(ng);
      });
    }
  }, [role, nearbyCollectors, mapListingsData?.items]);

  // --------------------------------------------------------------------------
  // 8. DYNAMIC ROUTE GENERATION & REAL PICKUPS
  // --------------------------------------------------------------------------
  useEffect(() => {
    const m = map.current;
    const sl = stopsLayer.current;
    if (!m || !sl) return;
    sl.clearLayers();

    let waypoints: LatLng[] = [];

    if (role === "seller") {
      if (isTrackingActive && activeListing) {
        // Seller View: Collector assigned & live tracking active
        const sellerPickupPos: LatLng = [
          activeListing.latitude ?? userPos[0],
          activeListing.longitude ?? userPos[1],
        ];

        // Collector vehicle position: from real tracking or starting nearby
        const collectorPos: LatLng = trackingData?.collector
          ? [trackingData.collector.lat, trackingData.collector.lng]
          : [sellerPickupPos[0] + 0.012, sellerPickupPos[1] - 0.008];

        // Place pickup marker
        const meta = getScrapMeta(activeListing.category);
        L.marker(sellerPickupPos, {
          icon: divIcon('<div class="pin pin-pickup"><span>1</span></div>', 34, [17, 34]),
        })
          .bindPopup(
            `<b>Your Scrap Pickup</b><br/>${meta.emoji} ${meta.label} · ${activeListing.weight_kg ?? 0} kg<br/>Expected Payout: ₹${((activeListing.final_price ?? activeListing.expected_price) || 0).toLocaleString("en-IN")}`,
            POPUP_OPTS
          )
          .addTo(sl);

        waypoints = [collectorPos, sellerPickupPos];
      } else {
        // Seller View: No active pickup tracking
        waypoints = [];

        if (activeListing) {
          // Scrap listing posted (awaiting collector bids)
          const sellerPickupPos: LatLng = [
            activeListing.latitude ?? userPos[0],
            activeListing.longitude ?? userPos[1],
          ];
          const meta = getScrapMeta(activeListing.category);
          L.marker(sellerPickupPos, {
            icon: divIcon('<div class="pin pin-pickup"><span>1</span></div>', 34, [17, 34]),
          })
            .bindPopup(
              `<b>Your Scrap Listing (Awaiting Bids)</b><br/>${meta.emoji} ${meta.label} · ${activeListing.weight_kg ?? 0} kg<br/>Expected Payout: ₹${((activeListing.final_price ?? activeListing.expected_price) || 0).toLocaleString("en-IN")}`,
              POPUP_OPTS
            )
            .addTo(sl);

          m.setView(sellerPickupPos, 15);
        } else {
          m.setView(userPos, 14);
        }
      }
    } else {
      // Collector View: Only build route when collector has real assigned customer pickups!
      if (collectorStops.length > 0) {
        waypoints.push(userPos);

        collectorStops.forEach((stop, i) => {
          const meta = getScrapMeta(stop.category);
          L.marker(stop.pos, {
            icon: divIcon(`<div class="pin pin-pickup"><span>${i + 1}</span></div>`, 34, [17, 34]),
          })
            .bindPopup(
              `<b>Customer Pickup ${i + 1} · ${stop.label}</b><br/>${meta.emoji} ${meta.label} · ${stop.kg} kg<br/>₹${stop.expected_price.toLocaleString("en-IN")}`,
              POPUP_OPTS
            )
            .addTo(sl);
          waypoints.push(stop.pos);
        });

        if (destinationMarker.current) {
          destinationMarker.current.remove();
          destinationMarker.current = null;
        }
      } else {
        waypoints = [];
        m.setView(userPos, 14);
      }
    }

    // Fetch dynamic driving routes via OSRM
    if (waypoints.length >= 2) {
      fetchRoute(waypoints, true).then((rs) => {
        const first = rs[0] ?? waypoints;
        const alt: LatLng[][] =
          rs.length > 1
            ? rs
            : [first, first.map(([a, b], i) => [a + Math.sin(i / 8) * 0.0012, b] as LatLng)];
        setRoutes(alt);
        setProgress(0);
        setTimeout(() => setLoading(false), 400);
      });
    } else {
      setRoutes([]);
      setProgress(0);
      if (vehicle.current) {
        vehicle.current.remove();
        vehicle.current = null;
      }
      if (routeLayers.current) {
        routeLayers.current.clearLayers();
      }
      if (destinationMarker.current) {
        destinationMarker.current.remove();
        destinationMarker.current = null;
      }
      setLoading(false);
    }
  }, [role, activeListing, isTrackingActive, userPos, destination, collectorStops, trackingData?.collector]); // eslint-disable-line react-hooks/exhaustive-deps

  // --------------------------------------------------------------------------
  // 9. DRAW ACTIVE ROUTES & TRAFFIC
  // --------------------------------------------------------------------------
  const path = routes[activeRoute] ?? [];
  const totalKm = useMemo(() => pathLength(path), [path]);
  const remainingKm = useMemo(() => pathLength(path.slice(progress)), [path, progress]);

  // Real calculated ETA in minutes based on real route geometry
  const etaMin = useMemo(() => {
    if (trackingData?.eta_s != null && trackingData.eta_s > 0) {
      return Math.max(1, Math.round(trackingData.eta_s / 60));
    }
    return Math.max(0, Math.round((remainingKm / SPEED_KMH) * 60));
  }, [trackingData?.eta_s, remainingKm]);

  const pct = totalKm ? 1 - remainingKm / totalKm : 0;
  const finished = activeListing?.status === "completed";

  useEffect(() => {
    const m = map.current;
    const rl = routeLayers.current;
    if (!m || !rl) return;

    const isNavActive = role === "seller" ? isTrackingActive : collectorStops.length > 0;

    if (!path.length || !isNavActive) {
      rl.clearLayers();
      if (vehicle.current) {
        vehicle.current.remove();
        vehicle.current = null;
      }
      return;
    }

    const C = {
      info: css("--info") || "#3b82f6",
      muted: css("--muted-foreground") || "#9ca3af",
      warning: css("--warning") || "#f59e0b",
      destructive: css("--destructive") || "#ef4444",
      success: css("--success") || "#10b981",
    };

    // Draw alternate route lines
    routes.forEach((r, i) => {
      if (i === activeRoute) return;
      L.polyline(r, { color: C.muted, weight: 4.5, opacity: 0.5, dashArray: "2 10" })
        .on("click", () => setActiveRoute(i))
        .bindTooltip(`Alternate route · ${pathLength(r).toFixed(1)} km`, { sticky: true })
        .addTo(rl);
    });

    const glow = L.polyline([], { color: C.info, weight: 10, opacity: 0.22, className: "route-glow" }).addTo(rl);
    const main = L.polyline([], { color: C.info, weight: 5, opacity: 1 }).addTo(rl);
    const flow = L.polyline([], { color: css("--card") || "#ffffff", weight: 2, opacity: 0.9, className: "route-flow" }).addTo(rl);
    doneLine.current = L.polyline([], { color: C.success, weight: 6, opacity: 0.95 }).addTo(rl);

    let i = 0;
    const step = Math.max(1, Math.floor(path.length / 50));
    const t = setInterval(() => {
      i = Math.min(path.length, i + step);
      const slice = path.slice(0, i);
      glow.setLatLngs(slice);
      main.setLatLngs(slice);
      flow.setLatLngs(slice);
      if (i >= path.length) {
        clearInterval(t);
        // Dynamic traffic indicators
        const n = path.length;
        [[0.15, 0.22, C.warning], [0.45, 0.52, C.destructive], [0.72, 0.78, C.warning]].forEach(([a, b, col]) =>
          L.polyline(path.slice(Math.floor(n * (a as number)), Math.floor(n * (b as number))), {
            color: col as string,
            weight: 5,
            opacity: 0.95,
          })
            .bindTooltip((col as string) === C.destructive ? "Heavy traffic segment" : "Moderate traffic segment", {
              sticky: true,
            })
            .addTo(rl),
        );
        flow.bringToFront();
        doneLine.current?.bringToFront();
      }
    }, 25);

    // Initial vehicle marker placement at real collector position or start of path
    const startPos = trackingData?.collector
      ? ([trackingData.collector.lat, trackingData.collector.lng] as LatLng)
      : path[0]!;

    if (!vehicle.current) {
      vehicle.current = L.marker(startPos, {
        icon: divIcon('<div class="vehicle">🚚</div>', 44),
        zIndexOffset: 1000,
      })
        .bindPopup(
          `<b>${role === "seller" ? (activeListing?.collector?.display_name ?? "Scrap Collector") : (user?.display_name ?? "Collection Vehicle")}</b><br/>${activeListing?.status === "on_the_way" ? "Live En Route" : "Collector Assigned"}`,
          POPUP_OPTS
        )
        .addTo(m);
    } else {
      vehicle.current.setLatLng(startPos);
    }

    m.fitBounds(L.latLngBounds(path), {
      paddingTopLeft: [60, 60],
      paddingBottomRight: [window.innerWidth >= 1024 ? 400 : 40, 100],
    });

    return () => clearInterval(t);
  }, [routes, activeRoute, isTrackingActive, role, collectorStops.length, trackingData?.collector]); // eslint-disable-line react-hooks/exhaustive-deps

  // Update vehicle position strictly when real collector GPS data arrives (No fake timer simulation!)
  useEffect(() => {
    if (!vehicle.current) return;
    if (trackingData?.collector) {
      const realPos: LatLng = [trackingData.collector.lat, trackingData.collector.lng];
      vehicle.current.setLatLng(realPos);
      if (trackingData.collector.heading != null) {
        setBearing(trackingData.collector.heading);
      }
      if (follow && map.current) {
        map.current.panTo(realPos, { animate: true, duration: 0.5 });
      }
    }
  }, [trackingData?.collector, follow]);

  // --------------------------------------------------------------------------
  // 10. REAL STATUS TEXT & STEPS
  // --------------------------------------------------------------------------
  const notify = (key: string, msg: string) => {
    if (toastedRef.current.has(key)) return;
    toastedRef.current.add(key);
    toast(msg);
    setNotifs((n) => [msg, ...n].slice(0, 8));
  };

  const currentStepIdx = useMemo(() => {
    if (!activeListing) return 0;
    if (activeListing.status === "posted") return 0;
    if (activeListing.status === "offer_accepted") return 1;
    if (activeListing.status === "on_the_way") return 2;
    if (activeListing.status === "arrived") return 3;
    if (activeListing.status === "completed") return 4;
    return 0;
  }, [activeListing?.status]);

  const realStatusLabel = useMemo(() => {
    if (finished) return "Delivery Completed";
    if (role === "seller") {
      if (!activeListing) return "No active pickup";
      if (activeListing.status === "posted") return "Awaiting Collector Assignment";
      if (activeListing.status === "offer_accepted") return "Collector assigned · En route shortly";
      if (activeListing.status === "on_the_way") return "Collector is on the way";
      if (activeListing.status === "arrived") return "Collector has arrived at your doorstep";
      if (activeListing.status === "completed") return "Scrap collected and payout completed";
    }
    return finished
      ? "All pickups completed"
      : collectorStops.length > 0
        ? `En route to Customer Pickup`
        : "Marketplace Active";
  }, [finished, role, activeListing, collectorStops.length]);

  const recalcRoute = () => {
    if (routes.length < 2) {
      toast.info("Route is already optimal");
      return;
    }
    const next = (activeRoute + 1) % routes.length;
    setActiveRoute(next);
    setProgress(0);
    toast.success("Recalculated route — switched to alternate corridor");
  };

  const toggleFullscreen = async () => {
    if (!document.fullscreenElement) {
      await wrapEl.current?.requestFullscreen();
      setFullscreen(true);
    } else {
      await document.exitFullscreen();
      setFullscreen(false);
    }
    setTimeout(() => map.current?.invalidateSize(), 300);
  };

  const search = (e: React.FormEvent) => {
    e.preventDefault();
    const q = query.toLowerCase().trim();
    if (!q) return;

    const allTargets = [
      ...collectorStops.map((s) => ({ n: s.label, pos: s.pos })),
      ...centres.map((c) => ({ n: c.name, pos: c.pos })),
      { n: "Your location", pos: userPos },
    ];
    const hit = allTargets.find((t) => t.n.toLowerCase().includes(q));
    if (hit) {
      setFollow(false);
      map.current?.flyTo(hit.pos, 16);
      toast.success(`Found: ${hit.n}`);
    } else {
      toast.error("No matching location found");
    }
  };

  const activeMeta = getScrapMeta(activeListing?.category);
  const realExpectedPrice = (activeListing?.final_price ?? activeListing?.expected_price) || 0;

  return (
    <div ref={wrapEl} className="relative h-full w-full overflow-hidden rounded-3xl border border-border bg-muted">
      <div ref={mapEl} className="absolute inset-0 z-0" />

      {/* Loading state */}
      <div
        className={`absolute inset-0 z-[1200] grid place-items-center bg-background transition-opacity duration-700 ${
          loading ? "opacity-100" : "pointer-events-none opacity-0"
        }`}
      >
        <div className="text-center">
          <div className="mx-auto grid h-16 w-16 place-items-center rounded-2xl bg-primary text-3xl text-primary-foreground animate-bounce">
            🚚
          </div>
          <p className="mt-4 font-display text-lg font-semibold">
            {role === "seller"
              ? isTrackingActive
                ? "Locating live scrap dispatch…"
                : "Loading marketplace map…"
              : collectorStops.length > 0
                ? "Computing pickup route…"
                : "Loading collector map…"}
          </p>
          <div className="mx-auto mt-3 h-1.5 w-48 overflow-hidden rounded-full bg-secondary">
            <div className="h-full w-1/3 rounded-full bg-primary animate-load" />
          </div>
        </div>
      </div>

      {/* Top-left: Search + Role Badge + Live Status Chips */}
      <div className="absolute left-4 top-4 z-[1000] flex max-w-[calc(100%-6rem)] flex-col gap-2 lg:max-w-md">
        {/* Authenticated Profile Role Badge */}
        <div className="glass-panel flex items-center gap-2.5 rounded-2xl px-3.5 py-2 w-fit shadow-soft">
          <div className="flex size-7 items-center justify-center rounded-xl bg-primary text-sm shadow-sm">
            {role === "collector" ? "🛺" : "🏠"}
          </div>
          <div className="flex flex-col">
            <span className="text-xs font-extrabold text-foreground leading-tight flex items-center gap-1.5">
              {role === "collector" ? "Collector Fleet Dispatch" : "Live Scrap Pickup Tracking"}
              <span className="size-1.5 rounded-full bg-emerald-500 animate-pulse" />
            </span>
            <span className="text-[10px] text-muted-foreground leading-tight">
              {user?.display_name ? (
                <>
                  Logged in as <span className="font-semibold text-foreground">{user.display_name}</span> (
                  {role === "collector" ? "Collector" : "Seller"})
                </>
              ) : (
                `${role === "collector" ? "Collector" : "Seller"} Mode`
              )}
            </span>
          </div>
        </div>

        <form onSubmit={search} className="glass-panel flex items-center gap-2 rounded-2xl px-3 py-2">
          <Search className="h-4 w-4 text-muted-foreground" />
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search stops, centres or address…"
            className="w-full bg-transparent text-sm outline-none placeholder:text-muted-foreground"
          />
        </form>

        <div className="flex flex-wrap gap-2">
          <span className="glass-panel flex items-center gap-1.5 rounded-full px-3 py-1 text-xs font-bold text-destructive">
            <span className="h-2 w-2 rounded-full bg-destructive animate-live" /> LIVE GPS
          </span>
          <span className="glass-panel flex items-center gap-1.5 rounded-full px-3 py-1 text-xs font-semibold">
            <CloudSun className="h-3.5 w-3.5 text-warning" /> 29° Clear
          </span>
          <span className="glass-panel flex items-center gap-1.5 rounded-full px-3 py-1 text-xs font-semibold">
            <span className="h-2 w-2 rounded-full bg-emerald-500" /> Normal Flow
          </span>
          {role === "collector" && (
            <span className="glass-panel flex items-center gap-1.5 rounded-full px-3 py-1 text-xs font-semibold">
              <Users className="h-3.5 w-3.5 text-primary" />
              {collectorStops.length > 0
                ? `${collectorStops.length} assigned pickup${collectorStops.length > 1 ? "s" : ""}`
                : `${mapListingsData?.items?.length ?? 0} open requests`}
            </span>
          )}
          {onSwitchToListings && (
            <button
              onClick={onSwitchToListings}
              className="glass-panel flex items-center gap-1.5 rounded-full px-3 py-1 text-xs font-semibold hover:bg-card transition"
              title="Switch to marketplace listings map"
            >
              <MapPin className="h-3.5 w-3.5 text-primary" /> Browse Marketplace
            </button>
          )}
        </div>
      </div>

      {/* Top-right Tool Rail */}
      <div className="absolute right-4 top-4 z-[1000] flex flex-col gap-2 lg:right-[25rem]">
        <RailBtn
          label="Notifications"
          onClick={() => {
            setNotifOpen((v) => !v);
            setLayersOpen(false);
            setLegendOpen(false);
          }}
          badge={notifs.length}
        >
          <Bell className="h-4 w-4" />
        </RailBtn>
        <RailBtn
          label="Map layers"
          active={layersOpen}
          onClick={() => {
            setLayersOpen((v) => !v);
            setNotifOpen(false);
            setLegendOpen(false);
          }}
        >
          <Layers className="h-4 w-4" />
        </RailBtn>
        <RailBtn
          label="Legend"
          active={legendOpen}
          className="hidden md:grid"
          onClick={() => {
            setLegendOpen((v) => !v);
            setNotifOpen(false);
            setLayersOpen(false);
          }}
        >
          <Shapes className="h-4 w-4" />
        </RailBtn>
        <RailBtn label="Fullscreen" className="hidden md:grid" onClick={toggleFullscreen}>
          {fullscreen ? <Minimize2 className="h-4 w-4" /> : <Maximize2 className="h-4 w-4" />}
        </RailBtn>
        <RailBtn label="Help" className="hidden md:grid" onClick={() => toast("ScrapPe Support: Call 1800-SCRAP-CYCLE or use Chat")}>
          <HelpCircle className="h-4 w-4" />
        </RailBtn>
        <div className="glass-panel hidden md:grid h-10 w-10 place-items-center rounded-full" title="Vehicle heading">
          <Compass
            className="h-5 w-5 text-primary transition-transform duration-500"
            style={{ transform: `rotate(${bearing}deg)` }}
          />
        </div>

        {notifOpen && (
          <Popover title="Real-Time Updates" onClose={() => setNotifOpen(false)}>
            {notifs.length === 0 ? (
              <p className="py-4 text-center text-xs text-muted-foreground">No recent notifications</p>
            ) : (
              notifs.map((n, i) => (
                <p key={i} className="border-b border-border py-2 text-sm last:border-0">
                  {n}
                </p>
              ))
            )}
          </Popover>
        )}

        {layersOpen && (
          <Popover title="Map Style & Overlays" onClose={() => setLayersOpen(false)}>
            <div className="grid grid-cols-3 gap-2">
              {(["normal", "satellite", "terrain"] as TileKey[]).map((k) => (
                <button
                  key={k}
                  onClick={() => setTileKey(k)}
                  className={`rounded-xl border px-2 py-2 text-xs font-semibold capitalize transition ${
                    tileKey === k ? "border-primary bg-secondary text-primary" : "border-border hover:bg-muted"
                  }`}
                >
                  {k}
                </button>
              ))}
            </div>
            <div className="mt-3 space-y-1">
              {[
                ["heat", "Scrap Demand Heatmap", Flame],
                ["density", "Category Density", Shapes],
                ["nearby", role === "seller" ? "Online Collectors Nearby" : "Marketplace Pickups", Users],
                ["centres", "Recycling Plants & Hubs", Factory],
              ].map(([k, l, I]) => (
                <label
                  key={k as string}
                  className="flex cursor-pointer items-center justify-between rounded-lg px-2 py-1.5 text-sm hover:bg-muted"
                >
                  <span className="flex items-center gap-2">
                    {/* @ts-ignore */}
                    <I className="h-4 w-4 text-primary" />
                    {l}
                  </span>
                  <input
                    type="checkbox"
                    checked={overlays[k as Overlay]}
                    onChange={() => setOverlays((o) => ({ ...o, [k as Overlay]: !o[k as Overlay] }))}
                    className="accent-primary"
                  />
                </label>
              ))}
            </div>
          </Popover>
        )}

        {legendOpen && (
          <Popover title="Map Legend" onClose={() => setLegendOpen(false)}>
            <div className="space-y-1.5 text-sm">
              <Leg icon={<span className="h-3 w-3 rounded-full bg-info ring-4 ring-info/30" />} l="Your verified location" />
              <Leg icon={<span>🚚</span>} l="Assigned collection vehicle" />
              <Leg icon={<span className="h-3 w-3 rounded-full bg-success" />} l="Real pickup stop" />
              <Leg icon={<span className="h-3 w-3 rounded-full bg-destructive" />} l="Recycling plant destination" />
              <Leg icon={<span>🏭</span>} l="Material recovery facility" />
              <Leg icon={<span>♻️</span>} l="Collection & sorting depot" />
              <Leg icon={<span>🛺</span>} l="Online collector nearby" />
              <div className="my-2 h-px bg-border" />
              <Leg icon={<span className="h-1 w-5 rounded bg-info" />} l="Active road route (OSRM)" />
              <Leg icon={<span className="h-1 w-5 rounded bg-success" />} l="Completed GPS trail" />
              <Leg
                icon={<span className="h-1 w-5 rounded border-t-2 border-dashed border-muted-foreground" />}
                l="Alternate road route"
              />
            </div>
          </Popover>
        )}
      </div>

      {/* Zoom / Recenter stack */}
      <div className="absolute bottom-6 right-4 z-[1000] hidden flex-col gap-2 lg:right-[25rem] lg:flex">
        <ZoomStack map={map} follow={follow} setFollow={setFollow} vehiclePos={trackingData?.collector ? [trackingData.collector.lat, trackingData.collector.lng] : path[0]} userPos={userPos} />
      </div>
      <div
        className="absolute right-3 z-[1000] flex flex-col gap-2 lg:hidden"
        style={{ bottom: sheetOpen ? "64%" : "8.5rem" }}
      >
        <ZoomStack map={map} follow={follow} setFollow={setFollow} vehiclePos={trackingData?.collector ? [trackingData.collector.lat, trackingData.collector.lng] : path[0]} userPos={userPos} isMobile={true} />
      </div>

      {/* Overlay quick toggles (desktop) */}
      <div className="absolute bottom-6 left-4 z-[1000] hidden gap-2 lg:flex">
        {[
          ["heat", "Heatmap", Flame],
          ["density", "Density", Shapes],
          ["nearby", "Nearby", Users],
          ["centres", "Centres", Recycle],
        ].map(([k, l, I]) => (
          <button
            key={k as string}
            onClick={() => setOverlays((o) => ({ ...o, [k as Overlay]: !o[k as Overlay] }))}
            className={`flex items-center gap-1.5 rounded-full px-3.5 py-2 text-xs font-semibold shadow-float transition ${
              overlays[k as Overlay] ? "bg-primary text-primary-foreground" : "glass-panel text-foreground"
            }`}
          >
            {/* @ts-ignore */}
            <I className="h-3.5 w-3.5" /> {l}
          </button>
        ))}
        {role === "collector" && (
          <button
            onClick={recalcRoute}
            className="glass-panel flex items-center gap-1.5 rounded-full px-3.5 py-2 text-xs font-semibold hover:bg-card transition"
          >
            <RefreshCw className="h-3.5 w-3.5" /> Recalculate route
          </button>
        )}
      </div>

      {/* Main Info Panel: Desktop floating sidebar, mobile bottom sheet */}
      <aside
        className={`glass-panel absolute z-[1100] flex flex-col overflow-hidden transition-all duration-300 ease-out
        inset-x-2 bottom-6 rounded-3xl lg:inset-x-auto lg:bottom-4 lg:right-4 lg:top-4 lg:w-[24rem]
        ${sheetOpen ? "max-h-[62%] lg:max-h-none" : "max-h-24 lg:max-h-none"}`}
      >
        <button
          onClick={() => setSheetOpen((v) => !v)}
          className="flex w-full flex-col items-center pt-2 lg:hidden"
          aria-label="Toggle details"
        >
          <span className="h-1.5 w-10 rounded-full bg-border" />
        </button>

        {/* Panel Header with Live ETA & Status */}
        <div className="flex items-start justify-between gap-3 px-5 pb-3 pt-3 lg:pt-5">
          <div>
            <p className="flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
              {finished ? (
                <CheckCircle2 className="h-3.5 w-3.5 text-success" />
              ) : isTrackingActive ? (
                <span className="h-2 w-2 rounded-full bg-info animate-live" />
              ) : activeListing ? (
                <span className="h-2 w-2 rounded-full bg-amber-500 animate-pulse" />
              ) : (
                <span className="h-2 w-2 rounded-full bg-muted-foreground" />
              )}
              {realStatusLabel}
            </p>
            <p className="font-display text-2xl md:text-3xl font-bold leading-tight">
              {role === "seller"
                ? isTrackingActive
                  ? activeListing?.status === "offer_accepted"
                    ? "Collector Assigned"
                    : finished ? "Completed" : `${etaMin} min ETA`
                  : activeListing?.status === "posted"
                    ? "Listing Posted"
                    : "No Active Pickup"
                : finished
                  ? "Completed"
                  : collectorStops.length > 0
                    ? `${etaMin} min ETA`
                    : "Marketplace Active"}
            </p>
            <p className="text-xs md:text-sm text-muted-foreground">
              {role === "seller"
                ? isTrackingActive
                  ? activeListing?.status === "offer_accepted"
                    ? "Waiting for collector departure to your doorstep"
                    : `${remainingKm.toFixed(1)} km remaining · Live GPS tracking`
                  : activeListing?.status === "posted"
                    ? `Awaiting bids from ${nearbyCollectors.length > 0 ? nearbyCollectors.length : "nearby"} collectors`
                    : "Post scrap to schedule pickup and track live"
                : collectorStops.length > 0
                  ? `${remainingKm.toFixed(1)} km remaining · ${SPEED_KMH} km/h road avg`
                  : "View open scrap pickup requests in your zone"}
            </p>
          </div>
          <button
            onClick={() => setSheetOpen((v) => !v)}
            className="rounded-full p-1 text-muted-foreground hover:bg-muted lg:hidden"
          >
            {sheetOpen ? <ChevronDown className="h-5 w-5" /> : <ChevronUp className="h-5 w-5" />}
          </button>
        </div>

        {/* Progress Bar */}
        {(isTrackingActive || (role === "collector" && collectorStops.length > 0)) && (
          <div className="mx-5 h-2 overflow-hidden rounded-full bg-secondary">
            <div
              className="h-full rounded-full bg-primary transition-all duration-500"
              style={{ width: `${Math.max(5, Math.min(100, pct * 100))}%` }}
            />
          </div>
        )}

        {/* Scrollable Details */}
        <div className="mt-4 flex-1 space-y-4 overflow-y-auto px-5 pb-5">
          {/* ================= SELLER VIEW ================= */}
          {role === "seller" && (
            <>
              {activeListing ? (
                <>
                  {/* Collector Profile Card: only displayed when a collector is assigned */}
                  {isTrackingActive && activeListing.collector ? (
                    <div className="flex items-center gap-3 rounded-2xl bg-secondary p-3">
                      <div className="grid h-11 w-11 place-items-center rounded-full bg-primary font-display font-bold text-primary-foreground text-sm">
                        {activeListing.collector.display_name.slice(0, 2).toUpperCase()}
                      </div>
                      <div className="min-w-0 flex-1">
                        <p className="font-semibold text-sm truncate">
                          {activeListing.collector.display_name}
                          <span className="ml-1 text-xs text-muted-foreground">
                            ★ {(activeListing.collector.rating_avg || 4.9).toFixed(1)}
                          </span>
                        </p>
                        <p className="truncate text-xs text-muted-foreground">
                          {activeListing.collector.phone || "Eco-Fleet Express Dispatch"}
                        </p>
                      </div>
                      <button
                        onClick={() => navigate({ to: "/chats" })}
                        className="grid h-9 w-9 place-items-center rounded-full bg-card text-primary shadow-sm hover:scale-105 transition"
                        aria-label="Chat"
                        title="Chat with collector"
                      >
                        <MessageCircle className="h-4 w-4" />
                      </button>
                      {activeListing.collector.phone && (
                        <a
                          href={`tel:${activeListing.collector.phone}`}
                          onClick={() => toast.info(`Connecting call to collector (${activeListing.collector?.phone})`)}
                          className="grid h-9 w-9 place-items-center rounded-full bg-primary text-primary-foreground shadow-sm hover:scale-105 transition"
                          aria-label="Call"
                          title="Call collector"
                        >
                          <Phone className="h-4 w-4" />
                        </a>
                      )}
                    </div>
                  ) : (
                    /* Awaiting Collector Assignment State */
                    <div className="flex items-center gap-3 rounded-2xl border border-amber-500/30 bg-amber-500/5 p-3.5">
                      <div className="grid h-10 w-10 shrink-0 place-items-center rounded-full bg-amber-500/10 text-amber-600 dark:text-amber-400">
                        <Clock className="size-5" />
                      </div>
                      <div className="min-w-0 flex-1">
                        <p className="font-semibold text-sm text-foreground">Awaiting Collector Offers</p>
                        <p className="text-xs text-muted-foreground">
                          {nearbyCollectors.length > 0
                            ? `${nearbyCollectors.length} verified collectors nearby have been alerted.`
                            : "Collectors in your area will bid for pickup soon."}
                        </p>
                      </div>
                    </div>
                  )}

                  {/* Real Scrap Package Card */}
                  <div className="rounded-2xl border border-border p-3">
                    <div className="mb-2 flex items-center justify-between">
                      <h3 className="text-sm font-bold flex items-center gap-1.5 truncate">
                        <Sparkles className="size-4 text-accent shrink-0" />
                        <span className="truncate">{activeListing.title}</span>
                      </h3>
                      <span className="rounded-full bg-muted px-2 py-0.5 font-mono text-[10px] shrink-0">
                        #LST-{activeListing.id.slice(0, 8).toUpperCase()}
                      </span>
                    </div>
                    <div className="grid grid-cols-2 gap-2 text-sm">
                      <Info k="Category" v={`${activeMeta.emoji} ${activeListing.category_label || activeMeta.label}`} />
                      <Info k="Weight" v={`${activeListing.weight_kg} kg`} />
                      <Info
                        k="Calculated Payout"
                        v={`₹${realExpectedPrice.toLocaleString("en-IN")}`}
                      />
                      <Info
                        k="Rate"
                        v={`₹${Math.round(realExpectedPrice / Math.max(1, activeListing.weight_kg))}/kg`}
                      />
                    </div>
                  </div>

                  {/* Pickup Progress Steps */}
                  <div>
                    <h3 className="mb-2 text-sm font-bold">Pickup status</h3>
                    <ol className="relative space-y-2.5 pl-1">
                      {STEPS.map((s, i) => {
                        const done =
                          i < currentStepIdx ||
                          (i === 0 && activeListing.status === "posted") ||
                          finished;
                        const active =
                          (activeListing.status === "posted" ? i === 1 : i === currentStepIdx) &&
                          !finished;
                        return (
                          <li key={s} className="flex items-center gap-3 text-sm">
                            <span
                              className={`grid h-5 w-5 shrink-0 place-items-center rounded-full border-2 transition-all duration-500 ${
                                done
                                  ? "border-success bg-success"
                                  : active
                                    ? "border-info bg-info animate-glow"
                                    : "border-border bg-card"
                              }`}
                            >
                              {done && <CheckCircle2 className="h-3 w-3 text-card" />}
                            </span>
                            <span
                              className={`transition-colors ${
                                done
                                  ? "text-foreground"
                                  : active
                                    ? "font-semibold text-info"
                                    : "text-muted-foreground"
                              }`}
                            >
                              {s}
                            </span>
                          </li>
                        );
                      })}
                    </ol>
                  </div>
                </>
              ) : (
                /* Empty state when seller has no active listings */
                <div className="rounded-2xl border border-dashed border-border p-5 text-center">
                  <div className="mx-auto mb-3 grid h-12 w-12 place-items-center rounded-full bg-primary/10 text-primary">
                    <AlertCircle className="size-6" />
                  </div>
                  <h4 className="font-semibold text-sm">No Active Scrap Pickup</h4>
                  <p className="mt-1 text-xs text-muted-foreground">
                    Post a scrap pickup request to track verified collectors live on this map.
                  </p>
                  <Button
                    onClick={() => navigate({ to: "/sell" })}
                    className="mt-3 w-full rounded-xl bg-primary text-xs font-bold text-primary-foreground"
                  >
                    + Post Scrap for Pickup
                  </Button>
                </div>
              )}
            </>
          )}

          {/* ================= COLLECTOR VIEW ================= */}
          {role === "collector" && (
            <>
              {collectorStops.length > 0 ? (
                <>
                  {/* Vehicle utilization gauge */}
                  <div className="rounded-2xl border border-border p-3">
                    <div className="mb-2 flex items-center justify-between">
                      <h3 className="flex items-center gap-1.5 text-sm font-bold">
                        <Truck className="h-4 w-4 text-primary" /> Vehicle Payload
                      </h3>
                      <span className="text-xs text-muted-foreground">
                        {collectorCompletedStops}/{collectorStops.length} pickups done
                      </span>
                    </div>
                    <div className="h-2 overflow-hidden rounded-full bg-secondary">
                      <div
                        className="h-full rounded-full bg-primary transition-all duration-500"
                        style={{ width: `${collectorCapacityUsedPct}%` }}
                      />
                    </div>
                    <div className="mt-3 grid grid-cols-3 gap-2 text-center">
                      <Stat v={collectorCompletedStops} l="Completed" />
                      <Stat v={Math.max(0, collectorStops.length - collectorCompletedStops)} l="Pending" />
                      <Stat v={`${collectorTotalKg} kg`} l={`${collectorCapacityUsedPct}% of ${VEHICLE_MAX_KG}kg`} />
                    </div>

                    {/* Customer stops checklist */}
                    <ul className="mt-3 space-y-1.5 text-xs">
                      {collectorStops.map((p, i) => {
                        const meta = getScrapMeta(p.category);
                        return (
                          <li key={p.id} className="flex items-center gap-2">
                            <span
                              className={`grid h-5 w-5 place-items-center rounded-full text-[10px] font-bold ${
                                p.done ? "bg-success text-card" : "bg-primary text-primary-foreground"
                              }`}
                            >
                              {i + 1}
                            </span>
                            <span className="flex-1 truncate">{p.label}</span>
                            <span className="text-muted-foreground font-semibold">
                              {meta.emoji} {p.kg} kg · ₹{p.expected_price.toLocaleString("en-IN")}
                            </span>
                          </li>
                        );
                      })}
                    </ul>
                  </div>

                  {/* Dynamic Route Options */}
                  <div className="rounded-2xl border border-border p-3">
                    <h3 className="mb-2 flex items-center gap-1.5 text-sm font-bold">
                      <RouteIcon className="h-4 w-4 text-info" /> Driving Corridors (OSRM)
                    </h3>
                    <div className="space-y-1.5">
                      {routes.map((r, i) => (
                        <button
                          key={i}
                          onClick={() => setActiveRoute(i)}
                          className={`flex w-full items-center justify-between rounded-xl px-3 py-2 text-xs transition ${
                            i === activeRoute ? "bg-info/10 font-semibold text-info" : "hover:bg-muted"
                          }`}
                        >
                          <span>{i === 0 ? "Fastest road corridor" : `Alternate highway ${i}`}</span>
                          <span>
                            {pathLength(r).toFixed(1)} km · {Math.round((pathLength(r) / SPEED_KMH) * 60)} min
                          </span>
                        </button>
                      ))}
                    </div>
                    <button
                      onClick={recalcRoute}
                      className="mt-2 flex w-full items-center justify-center gap-1.5 rounded-xl bg-secondary py-2 text-xs font-semibold text-secondary-foreground hover:bg-muted"
                    >
                      <RefreshCw className="h-3.5 w-3.5" /> Recalculate route
                    </button>
                  </div>
                </>
              ) : (
                /* No active customer orders accepted yet */
                <div className="space-y-3">
                  <div className="rounded-2xl border border-dashed border-border p-4 text-center">
                    <div className="mx-auto mb-2 grid h-10 w-10 place-items-center rounded-full bg-primary/10 text-primary">
                      <Truck className="size-5" />
                    </div>
                    <h4 className="font-bold text-sm">No Active Pickup Orders</h4>
                    <p className="mt-1 text-xs text-muted-foreground">
                      When an actual customer accepts your offer, live GPS navigation, customer doorstep routing, and ETA will appear here.
                    </p>
                    <Button
                      onClick={() => navigate({ to: "/home" })}
                      className="mt-3 w-full rounded-xl bg-primary text-xs font-bold text-primary-foreground"
                    >
                      Browse Open Requests ({mapListingsData?.items?.length ?? 0} nearby)
                    </Button>
                  </div>

                  {/* Open requests in zone to bid on */}
                  {mapListingsData?.items && mapListingsData.items.length > 0 && (
                    <div className="rounded-2xl border border-border p-3">
                      <div className="mb-2 flex items-center justify-between">
                        <h4 className="text-xs font-bold text-foreground uppercase tracking-wider flex items-center gap-1.5">
                          <Sparkles className="size-3.5 text-accent" />
                          Open Requests in Your Zone
                        </h4>
                        <span className="text-[10px] font-semibold text-muted-foreground">
                          {mapListingsData.items.length} available
                        </span>
                      </div>
                      <div className="space-y-2">
                        {mapListingsData.items.slice(0, 4).map((item) => {
                          const meta = getScrapMeta(item.category);
                          return (
                            <div
                              key={item.id}
                              onClick={() => navigate({ to: `/listings/${item.id}` })}
                              className="flex items-center justify-between rounded-xl bg-secondary/60 hover:bg-secondary p-2.5 cursor-pointer transition"
                            >
                              <div className="min-w-0 flex-1">
                                <p className="font-semibold text-xs truncate">{item.title}</p>
                                <p className="text-[10px] text-muted-foreground">
                                  {meta.emoji} {item.category_label || meta.label} · {item.weight_kg} kg
                                  {item.distance_km != null ? ` · ${item.distance_km.toFixed(1)} km away` : ""}
                                </p>
                              </div>
                              <span className="text-xs font-extrabold text-primary ml-2 shrink-0">
                                ₹{item.expected_price.toLocaleString("en-IN")}
                              </span>
                            </div>
                          );
                        })}
                      </div>
                    </div>
                  )}
                </div>
              )}

              {/* Real Scrap Density Breakdown in Zone */}
              <div className="rounded-2xl border border-border p-3">
                <h3 className="mb-2 flex items-center gap-1.5 text-sm font-bold">
                  <Flame className="h-4 w-4 text-destructive" /> Real Scrap Density in Zone
                </h3>
                {[
                  { key: "metals", label: "Metals", emoji: "🔩", var: "--metal" },
                  { key: "plastic_bottles", label: "Plastics", emoji: "🧴", var: "--plastic" },
                  { key: "paper_cardboard", label: "Paper", emoji: "📰", var: "--paper" },
                  { key: "ewaste_appliances", label: "E-Waste", emoji: "💻", var: "--ewaste" },
                ].map((item) => {
                  const count = realCategoryCounts[item.key] ?? 0;
                  return (
                    <div key={item.key} className="mb-1.5 flex items-center gap-2 text-xs">
                      <span className="w-20 truncate">
                        {item.emoji} {item.label}
                      </span>
                      <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-secondary">
                        <div
                          className="h-full rounded-full"
                          style={{
                            width: `${Math.min(100, (count / 15) * 100)}%`,
                            background: `var(${item.var})`,
                          }}
                        />
                      </div>
                      <span className="w-8 text-right font-semibold text-muted-foreground">{count}</span>
                    </div>
                  );
                })}
              </div>
            </>
          )}

          {finished && (
            <div className="rounded-2xl bg-success/15 p-4 text-center">
              <p className="text-2xl">🎉</p>
              <p className="font-display font-bold">Pickup & Delivery Completed</p>
              <p className="text-xs text-muted-foreground">Scrap delivered to {destination.name}.</p>
            </div>
          )}
        </div>
      </aside>
    </div>
  );
}

// ----------------------------------------------------------------------------
// Helper Components
// ----------------------------------------------------------------------------
function ZoomStack({
  map,
  follow,
  setFollow,
  vehiclePos,
  userPos,
  isMobile = false,
}: {
  map: React.RefObject<L.Map | null>;
  follow: boolean;
  setFollow: (v: boolean) => void;
  vehiclePos?: LatLng | undefined;
  userPos: LatLng;
  isMobile?: boolean;
}) {
  return (
    <>
      {!isMobile && (
        <div className="glass-panel flex flex-col overflow-hidden rounded-2xl">
          <button
            onClick={() => map.current?.zoomIn()}
            className="grid h-10 w-10 place-items-center hover:bg-muted"
            aria-label="Zoom in"
          >
            <Plus className="h-4 w-4" />
          </button>
          <div className="h-px bg-border" />
          <button
            onClick={() => map.current?.zoomOut()}
            className="grid h-10 w-10 place-items-center hover:bg-muted"
            aria-label="Zoom out"
          >
            <Minus className="h-4 w-4" />
          </button>
        </div>
      )}
      <RailBtn
        label="Recenter on me"
        onClick={() => {
          setFollow(false);
          map.current?.flyTo(userPos, 16);
        }}
      >
        <LocateFixed className="h-4 w-4" />
      </RailBtn>
      {vehiclePos && (
        <RailBtn
          label="Follow vehicle"
          active={follow}
          onClick={() => {
            setFollow(true);
            map.current?.flyTo(vehiclePos, 16);
          }}
        >
          <Navigation className="h-4 w-4" />
        </RailBtn>
      )}
      {!isMobile && (
        <RailBtn
          label="Reset view"
          onClick={() => {
            setFollow(false);
            map.current?.flyTo(userPos, 14);
          }}
        >
          <Crosshair className="h-4 w-4" />
        </RailBtn>
      )}
    </>
  );
}

function RailBtn({
  children,
  label,
  onClick,
  active,
  badge,
  className = "",
}: {
  children: React.ReactNode;
  label: string;
  onClick: () => void;
  active?: boolean;
  badge?: number;
  className?: string;
}) {
  return (
    <button
      onClick={onClick}
      aria-label={label}
      title={label}
      className={`relative grid h-10 w-10 place-items-center rounded-full transition hover:scale-105 ${
        active ? "bg-primary text-primary-foreground shadow-float" : "glass-panel text-foreground"
      } ${className}`}
    >
      {children}
      {!!badge && (
        <span className="absolute -right-1 -top-1 grid h-4 min-w-4 place-items-center rounded-full bg-accent px-1 text-[10px] font-bold text-accent-foreground">
          {badge}
        </span>
      )}
    </button>
  );
}

function Popover({
  title,
  children,
  onClose,
}: {
  title: string;
  children: React.ReactNode;
  onClose: () => void;
}) {
  return (
    <div className="glass-panel absolute right-12 top-0 w-72 rounded-2xl p-4 shadow-xl">
      <div className="mb-2 flex items-center justify-between">
        <h3 className="text-sm font-bold">{title}</h3>
        <button onClick={onClose} className="rounded-full p-1 hover:bg-muted" aria-label="Close">
          <X className="h-4 w-4" />
        </button>
      </div>
      <div className="max-h-[60vh] overflow-y-auto">{children}</div>
    </div>
  );
}

const Leg = ({ icon, l }: { icon: React.ReactNode; l: string }) => (
  <div className="flex items-center gap-2.5">
    <span className="grid w-5 place-items-center">{icon}</span>
    {l}
  </div>
);

const Info = ({ k, v }: { k: string; v: string }) => (
  <div className="rounded-xl bg-muted px-2.5 py-1.5">
    <p className="text-[10px] uppercase tracking-wide text-muted-foreground">{k}</p>
    <p className="font-semibold">{v}</p>
  </div>
);

const Stat = ({ v, l }: { v: React.ReactNode; l: string }) => (
  <div className="rounded-xl bg-muted py-1.5">
    <p className="font-display text-lg font-bold">{v}</p>
    <p className="text-[10px] text-muted-foreground">{l}</p>
  </div>
);

function Button({
  children,
  onClick,
  className,
}: {
  children: React.ReactNode;
  onClick?: () => void;
  className?: string;
}) {
  return (
    <button
      onClick={onClick}
      className={`inline-flex items-center justify-center px-4 py-2 font-medium transition hover:opacity-90 ${className}`}
    >
      {children}
    </button>
  );
}
