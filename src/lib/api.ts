// Thin typed client for the ScrapMart FastAPI backend (backend/app).
// Handles JWT storage, Authorization header, one-shot token refresh and error normalisation.

const RAW_URL = (import.meta.env["VITE_API_URL"] as string | undefined) ?? "http://localhost:8000/api/v1";
export const API_URL = RAW_URL.replace(/\/+$/, "");
export const WS_CHAT_URL = API_URL.replace(/^http/, "ws") + "/chat/ws";

/** Normalizes media/avatar URLs to point to the current production backend if saved with localhost/relative path */
export function resolveMediaUrl(url?: string | null): string | null {
  if (!url) return null;
  const raw = url.trim();
  if (!raw) return null;

  let backendBase = API_URL.replace(/\/api\/v1\/?$/, "");
  const isBrowserRemote =
    typeof window !== "undefined" &&
    window.location.hostname &&
    !window.location.hostname.includes("localhost") &&
    !window.location.hostname.includes("127.0.0.1");

  if (isBrowserRemote && (!backendBase || backendBase.includes("localhost") || backendBase.includes("127.0.0.1"))) {
    backendBase = "https://scrap-application.onrender.com";
  }

  // Upgrade insecure render URLs
  if (raw.startsWith("http://scrap-application.onrender.com")) {
    return raw.replace(/^http:\/\//, "https://");
  }

  // If pointing to localhost / 127.0.0.1 from local dev, rewrite to the active backend base
  if (raw.includes("localhost:8000") || raw.includes("127.0.0.1:8000")) {
    return raw.replace(/^https?:\/\/(localhost|127\.0\.0\.1):8000/, backendBase);
  }

  // If a relative /media path was stored
  if (raw.startsWith("/media/") || raw.startsWith("media/")) {
    const cleanPath = raw.startsWith("/") ? raw : `/${raw}`;
    return `${backendBase}${cleanPath}`;
  }

  return raw;
}


// ---------------------------------------------------------------- types ----
export type Role = "seller" | "collector";
export type ListingStatus = "posted" | "offer_accepted" | "on_the_way" | "arrived" | "completed" | "cancelled";
export type BidStatus = "pending" | "accepted" | "rejected" | "withdrawn";

export type ApiUser = {
  id: string;
  email: string;
  role: Role;
  display_name: string;
  phone: string | null;
  city: string | null;
  latitude: number | null;
  longitude: number | null;
  avatar_url: string | null;
  rating_avg: number;
  rating_count: number;
  created_at: string;
};

export type UserBrief = {
  id: string;
  display_name: string;
  avatar_url: string | null;
  city: string | null;
  rating_avg: number;
  rating_count: number;
  phone: string | null;
};

export type PublicProfile = UserBrief;

export type TokenResponse = {
  access_token: string;
  refresh_token: string;
  token_type: string;
  expires_in: number;
  is_new_user: boolean;
  user: ApiUser;
};

export type ApiListing = {
  id: string;
  title: string;
  category: string;
  category_label: string;
  weight_kg: number;
  expected_price: number;
  address: string;
  description: string | null;
  latitude: number | null;
  longitude: number | null;
  distance_km: number | null;
  status: ListingStatus;
  current_step: number;
  images: { id: string; url: string }[];
  seller: UserBrief;
  collector: UserBrief | null;
  bid_count: number;
  my_bid: { id: string; amount: number; note: string | null; status: BidStatus } | null;
  final_price: number | null;
  is_owner: boolean;
  is_assigned_collector: boolean;
  can_review: boolean;
  has_reviewed: boolean;
  pickup_time_slot?: string | null;
  floor_access?: string | null;
  is_negotiable?: boolean;
  packaging_type?: string | null;
  created_at: string;
  accepted_at: string | null;
  completed_at: string | null;
};

export type ApiBid = {
  id: string;
  listing_id: string;
  amount: number;
  note: string | null;
  status: BidStatus;
  is_highest: boolean;
  created_at: string;
  collector: UserBrief;
};

export type ApiTracker = {
  listing_id: string;
  status: ListingStatus;
  current_step: number;
  total_steps: number;
  cancelled: boolean;
  steps: { step: number; status: ListingStatus; label: string; done: boolean; current: boolean; timestamp: string | null }[];
  next_status: ListingStatus | null;
  next_label: string | null;
  can_advance: boolean;
  final_price: number | null;
  seller: UserBrief;
  collector: UserBrief | null;
};

export type ApiReview = {
  id: string;
  rating: number;
  comment: string | null;
  created_at: string;
  listing_id: string;
  listing_title: string;
  reviewer: UserBrief;
  reviewer_role: Role;
};

export type MonthlyBreakdown = {
  month: string;
  amount: number;
};

export type CategoryWeight = {
  category: string;
  weight_kg: number;
};

export type RateItem = {
  name: string;
  price: number;
  unit: string;
  trend: string;
};

export type ApiStats = {
  role: Role;
  completed_pickups: number;
  total_amount: number;
  amount_label: "earned" | "paid";
  active_pickups: number;
  open_items: number;
  rating_avg: number;
  rating_count: number;
  recycled_kg?: number;
  co2_saved_kg?: number;
  wallet_balance?: number;
  monthly_earnings?: MonthlyBreakdown[];
  saved_addresses?: string[];
  van_inventory?: CategoryWeight[];
};

export type ApiMessage = {
  id: string;
  listing_id: string;
  listing_title: string;
  sender_id: string;
  sender_name: string;
  recipient_id: string;
  recipient_name: string;
  body: string;
  created_at: string;
};

export type Page<T> = {
  items: T[];
  total?: number | null;
  limit: number;
  offset?: number;
  next_cursor?: string | null;
  has_more?: boolean;
  partial?: boolean;
};
export type CursorPage<T> = Page<T>;

export type TrackPoint = {
  lat: number;
  lng: number;
  accuracy_m: number | null;
  heading_deg: number | null;
  speed_kmh: number | null;
  age_s: number;
  live: boolean;
};

export type TrackingOut = {
  listing_id: string;
  status: ListingStatus;
  tracking_active: boolean;
  reason: string | null;
  collector: TrackPoint | null;
  destination: { lat: number; lng: number; address: string } | null;
  distance_m: number | null;
  eta_s: number | null;
  eta_source: string | null;
  route: [number, number][] | null;
  trail: [number, number][];
  geofence: string | null;
};

export type NearbyCollector = {
  id: string;
  display_name: string;
  avatar_url: string | null;
  rating_avg: number;
  rating_count: number;
  lat: number;
  lng: number;
  distance_km: number;
  last_seen_s: number;
};

export type HeatCell = {
  lat: number;
  lng: number;
  count: number;
  total_weight_kg: number;
  avg_price_per_kg: number | null;
  intensity: number;
  top_categories: { category: string; count: number }[];
};

export type HeatmapOut = {
  kind: string;
  days: number | null;
  cell_deg: number;
  cells: HeatCell[];
  partial: boolean;
};

export type MapListingItem = {
  id: string;
  title: string;
  category: string;
  category_label: string;
  weight_kg: number;
  expected_price: number;
  price_per_kg: number | null;
  lat: number;
  lng: number;
  exact: boolean;
  distance_km: number | null;
  status: ListingStatus;
  is_mine: boolean;
  cover_image: string | null;
  created_at: string;
};

export type MapListingsOut = {
  items: MapListingItem[];
  clusters: {
    id: string;
    lat: number;
    lng: number;
    count: number;
    total_weight_kg: number;
    avg_price_per_kg: number | null;
    categories: Record<string, number>;
    listing_ids: string[];
  }[];
  total_in_view: number;
  truncated: boolean;
  partial: boolean;
  center: [number, number] | null;
};

export type LocationStrategy = {
  ladder: { name: string; options: PositionOptions }[];
  watch: PositionOptions;
  send_interval_s: { idle: number; tracking: number };
  default_center: { lat: number; lng: number };
};

export type ResolveOut = {
  lat: number;
  lng: number;
  accuracy_m: number;
  source: string;
  confidence: number;
  zoom: number;
  hint: string | null;
  needs_confirmation: boolean;
};

// ---------------------------------------------------------------- errors ---
export class ApiError extends Error {
  status: number;
  code?: string | undefined;
  constructor(status: number, message: string, code?: string) {
    super(message);
    this.status = status;
    this.code = code;
  }
}

function parseDetail(body: unknown, fallback: string): { message: string; code?: string | undefined } {
  const detail = (body as { detail?: unknown } | null)?.detail;
  if (typeof detail === "string") return { message: detail };
  if (Array.isArray(detail)) {
    // FastAPI validation errors: [{loc: [...], msg: "..."}]
    const msg = detail
      .map((d) => {
        const loc = Array.isArray(d?.loc) ? d.loc.filter((x: unknown) => x !== "body").join(".") : "";
        return loc ? `${loc}: ${d?.msg}` : String(d?.msg ?? "");
      })
      .filter(Boolean)
      .join("; ");
    return { message: msg || fallback };
  }
  if (detail && typeof detail === "object") {
    const d = detail as { code?: string; message?: string };
    return { message: d.message ?? fallback, code: d.code };
  }
  return { message: fallback };
}

// ---------------------------------------------------------------- tokens ---
const STORAGE_KEY = "scrapmart.tokens";
type Tokens = { access: string; refresh: string };

const isBrowser = () => typeof window !== "undefined";

export function getTokens(): Tokens | null {
  if (!isBrowser()) return null;
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    return raw ? (JSON.parse(raw) as Tokens) : null;
  } catch {
    return null;
  }
}

export function setTokens(t: Tokens | null) {
  if (!isBrowser()) return;
  if (t) window.localStorage.setItem(STORAGE_KEY, JSON.stringify(t));
  else window.localStorage.removeItem(STORAGE_KEY);
}

export const storeTokenResponse = (r: TokenResponse) => setTokens({ access: r.access_token, refresh: r.refresh_token });

/** Fired when the session can no longer be refreshed so the UI can sign the user out. */
export const SESSION_EXPIRED_EVENT = "scrapmart:session-expired";

let refreshing: Promise<boolean> | null = null;
function refreshTokens(): Promise<boolean> {
  refreshing ??= (async () => {
    const t = getTokens();
    if (!t) return false;
    try {
      const res = await fetch(`${API_URL}/auth/refresh`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ refresh_token: t.refresh }),
      });
      if (!res.ok) return false;
      storeTokenResponse((await res.json()) as TokenResponse);
      return true;
    } catch {
      return false;
    }
  })().finally(() => {
    refreshing = null;
  });
  return refreshing;
}

// ---------------------------------------------------------------- fetcher --
type Query = Record<string, string | number | boolean | null | undefined>;
type Opts = { method?: string; body?: unknown; form?: FormData; query?: Query; auth?: boolean };

function buildUrl(path: string, query?: Query) {
  const qs = new URLSearchParams();
  for (const [k, v] of Object.entries(query ?? {})) if (v !== undefined && v !== null && v !== "") qs.set(k, String(v));
  const s = qs.toString();
  return `${API_URL}${path}${s ? `?${s}` : ""}`;
}

export async function api<T = unknown>(path: string, opts: Opts = {}): Promise<T> {
  const { method = "GET", body, form, query, auth = true } = opts;
  const send = () => {
    const headers: Record<string, string> = {};
    const tok = auth ? getTokens() : null;
    if (tok) headers["Authorization"] = `Bearer ${tok.access}`;
    if (body !== undefined) headers["Content-Type"] = "application/json";
    return fetch(buildUrl(path, query), {
      method,
      headers,
      body: form ?? (body !== undefined ? JSON.stringify(body) : null),
    });
  };

  let res: Response;
  try {
    res = await send();
  } catch {
    throw new ApiError(0, "Can't reach the server. Check your connection and try again.");
  }
  if (res.status === 401 && auth && getTokens()) {
    if (await refreshTokens()) res = await send();
    else {
      setTokens(null);
      if (isBrowser()) window.dispatchEvent(new Event(SESSION_EXPIRED_EVENT));
    }
  }
  if (res.status === 204) return undefined as T;
  const data: unknown = await res.json().catch(() => null);
  if (!res.ok) {
    const { message, code } = parseDetail(data, `Request failed (${res.status})`);
    throw new ApiError(res.status, message, code);
  }
  return data as T;
}

export const errorMessage = (e: unknown) => (e instanceof Error ? e.message : "Something went wrong");

import { compressImage } from "@/lib/image";

// ---------------------------------------------------------------- helpers --
/** Fast batch upload with client-side compression. Drops upload time from 15-20s to <1s. */
export async function uploadImages(files: File[]): Promise<string[]> {
  if (files.length === 0) return [];
  const compressed = await Promise.all(files.map((f) => compressImage(f)));
  const form = new FormData();
  compressed.forEach((file) => form.append("files", file));
  const out = await api<{ url: string; key: string }[]>("/uploads/images", { method: "POST", form });
  return out.map((o) => o.url).filter(Boolean);
}

/** Upload avatar for either seller or collector and automatically link to profile. */
export async function uploadAvatar(file: File): Promise<string> {
  const compressed = await compressImage(file, 640, 0.85);
  const form = new FormData();
  form.append("file", compressed);
  const out = await api<{ url: string; key: string }>("/uploads/avatar", { method: "POST", form });
  return out.url;
}
