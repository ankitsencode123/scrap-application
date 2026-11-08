#!/usr/bin/env python3
"""
===============================================================================
 ScrapMart - Map System  (single-file edition)
===============================================================================
Drop-in location:  backend/app/api/v1/map_system.py

WHAT YOU GET
  1. Robust location pipeline   Validation (null-island, stale, out-of-order, teleport
                                rejection), per-user Kalman smoothing, accuracy scoring,
                                rate limiting, throttled write-back to the user profile.
  2. "Position timeout" cure    POST /map/location/resolve never fails: it ranks
                                client fix > last server fix > IP geolocation > profile >
                                city centroid > configured default, and returns a confidence
                                + hint, so the UI always has a sensible map centre.
                                GET /map/location/strategy gives the client a retry ladder.
  3. Live pickup tracking       Collector pings (REST or WebSocket) -> geofence state machine
                                (far/near/arrived) -> ETA -> pushed to the seller in real time.
  4. Marketplace map            Bounding-box listing search with server-side clustering,
                                privacy masking, and a price/demand heatmap by area.
  5. Collectors nearby          Opt-in presence; sellers see online collectors around them.
  6. Real-time fan-out          WebSocket hub. Backbone is Kafka (cloud) when configured,
                                in-process otherwise. Latest state lives in Redis (cloud) or memory.
  7. Resilience                 Every external dependency (Redis, Kafka, OSRM, IP-geo) degrades
                                to a local fallback instead of failing the request.

WHY "POSITION TIMEOUT" HAPPENS (and what this file does about it)
  navigator.geolocation error 3 = the browser did not get a fix inside `timeout`. Usual causes:
    * enableHighAccuracy:true with a short timeout (GPS cold start takes 20-60 s),
    * desktop browsers with no location provider (Firefox/Chromium on Linux without GeoClue
      or Wi-Fi positioning often NEVER return a fix),
    * location permission denied (code 1) or OS location services off (code 2).
  A server cannot make the browser faster, so the contract is: ask with a *ladder* of
  progressively stricter options, and on ANY error call /map/location/resolve, which answers
  from IP / profile / city so the map still opens in the right place and the user can drag the
  pin (send it back with source="manual").

CLIENT CONTRACT (copy once into the frontend; no UI involved)
    async function getPosition(api) {
      const cfg = await api.get('/map/location/strategy');
      for (const step of cfg.ladder) {                       // cached -> network -> gps
        try {
          const p = await new Promise((ok, no) => navigator.geolocation.getCurrentPosition(ok, no, step.options));
          return api.post('/map/location/resolve', { fix: {
            lat: p.coords.latitude, lng: p.coords.longitude, accuracy_m: p.coords.accuracy,
            source: p.coords.accuracy > 200 ? 'network' : 'gps', captured_at: p.timestamp } });
        } catch (e) { last = e; }                            // code 1/2/3 -> try next rung
      }
      return api.post('/map/location/resolve', { client_error: { code: last.code, message: last.message } });
    }
    // live tracking (collector):   ws = new WebSocket(WS_URL)
    // ws.onopen  -> ws.send(JSON.stringify({type:'auth', token}))
    // seller     -> ws.send({type:'subscribe', listing_id})   -> events "collector.location", "geofence"
    // collector  -> navigator.geolocation.watchPosition(p => ws.send({type:'location', lat, lng,
    //                 accuracy_m, speed_mps, heading_deg, captured_at, listing_id}), null, cfg.watch)
    // map screen -> ws.send({type:'subscribe_area', bbox:[south,west,north,east]})  -> "listing.created"

INTEGRATION (3 small patches)
  1) app/api/v1/router.py
        from app.api.v1.map_system import map_router         # add to the tuple of routers
  2) app/main.py  (right after `app = FastAPI(...)`)
        from app.api.v1.map_system import install_map_system
        install_map_system(app)                               # wraps lifespan: start/stop Redis, Kafka, hub
  3) Emit events after DB commits (all sync-safe, fire-and-forget):
        from app.api.v1.map_system import emit_listing_event, emit_user_event
        create_listing  -> emit_listing_event("listing.created", listing)
        cancel_listing / accept_bid / advance_pickup -> emit_listing_event("listing.status", listing)
        place_bid       -> emit_user_event(listing.seller_id, "bid.placed", {"listing_id": str(listing.id)})
  requirements:  redis>=5 (cloud Redis/Upstash)   aiokafka (cloud Kafka)   geoip2 (optional)

ENVIRONMENT  (all optional; sensible dev defaults)
  MAP_REDIS_URL / REDIS_URL        latest fixes, presence geo-index, trails. Empty -> in-memory
  MAP_KAFKA_BOOTSTRAP_SERVERS      host:port,host:port. Empty -> in-process bus (use 1 worker!)
  MAP_KAFKA_TOPIC=scrapmart.map.events      create it first: 6 partitions, retention 1h
  MAP_KAFKA_SECURITY_PROTOCOL=SASL_SSL      MAP_KAFKA_SASL_MECHANISM=PLAIN|SCRAM-SHA-256|SCRAM-SHA-512
  MAP_KAFKA_SASL_USERNAME / MAP_KAFKA_SASL_PASSWORD     MAP_KAFKA_COMPRESSION=gzip
  MAP_DEFAULT_CENTER=22.5726,88.3639        last-resort map centre (Kolkata)
  MAP_PRESENCE_TTL_S=120  MAP_MAX_LIVE_ACCURACY_M=300  MAP_MAX_SPEED_KMH=160
  MAP_AUTO_ARRIVE=false            true -> geofence "arrived" auto-advances ON_THE_WAY -> ARRIVED
  MAP_OSRM_URL=                    e.g. https://router.project-osrm.org (self-host for production)
  MAP_IP_GEO_URL=                  e.g. https://ipwho.is/{ip}  (optional HTTP provider)
  MAP_GEOIP_DB=                    path to GeoLite2-City.mmdb (optional, best option)
  MAP_TRUST_PROXY_HEADERS=false    true behind Cloudflare/Vercel/etc (enables CDN geo headers + XFF)
  MAP_PRESENCE_PRECISION=2         decimals for collector presence pins (2 ~ 1 km)
  MAP_HEAT_MIN_COUNT=1             raise to 3+ for k-anonymity once traffic grows

DESIGN NOTES
  * Live pings are NOT written to Postgres (your shards are 500 MB). Latest fix / trail / presence
    live in Redis with TTLs; Kafka retention is the audit log. Only a throttled "home location"
    write-back touches the user row (>=15 min apart and >=300 m moved).
  * Privacy: non-parties only ever receive ~1 km masked coordinates (same rule as listings.py).
    SQL bbox pre-filters are widened and the exact test runs on the *masked* point, so tiny
    bbox probing cannot binary-search a doorstep.
  * Only the seller and the assigned collector may subscribe to / read a listing's live channel.
===============================================================================
"""

import asyncio
import contextlib
import ipaddress
import json
import logging
import math
import os
import threading
import time
import uuid
from collections import Counter, OrderedDict, defaultdict, deque
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from enum import Enum
from typing import Annotated, Any, Callable, Literal

import jwt
from fastapi import APIRouter, FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import func, or_, select, text
from starlette.concurrency import run_in_threadpool

from app.core.config import EARTH_RADIUS_KM, normalize_redis_url, settings
from app.core.security import CollectorUser, CurrentUser, SellerUser
from app.core.shard_router import ShardError, get_router
from app.db.enums import ACTIVE_PICKUP_STATUSES, CATEGORY_LABELS, Category, ListingStatus, Role
from app.db.models import Listing, ListingImage, User
from app.services.common import add_event, lock_listing

__all__ = ["map_router", "install_map_system", "init_map_system", "close_map_system",
           "emit_listing_event", "emit_user_event", "MapSettings", "RedisBus"]

log = logging.getLogger("scrapmart.map")

map_router = APIRouter(prefix="/map", tags=["Map"])

AREA_CELL_DEG = 0.1        # pub/sub cell size for "new listing in my area" (~11 km)
MAX_AREA_CELLS = 256
NEAR_M, ARRIVE_M = 500.0, 100.0   # geofence radii


# =============================================================================
# 0. SETTINGS
# =============================================================================
def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def _env_bool(name: str, default: bool = False) -> bool:
    return _env(name, str(default)).lower() in {"1", "true", "yes", "on"}


def _env_num(name: str, default: float) -> float:
    try:
        return float(_env(name, str(default)))
    except ValueError:
        return default


@dataclass(frozen=True)
class MapSettings:
    redis_url: str = ""
    redis_fallback_url: str = ""
    kafka_servers: str = ""
    kafka_topic: str = "scrapmart.map.events"
    kafka_protocol: str = "PLAINTEXT"
    kafka_sasl_mechanism: str = ""
    kafka_sasl_username: str = ""
    kafka_sasl_password: str = ""
    kafka_compression: str = "gzip"
    presence_ttl_s: int = 120
    fix_ttl_s: int = 6 * 3600
    max_live_accuracy_m: float = 300.0
    max_speed_kmh: float = 160.0
    min_ping_interval_s: float = 0.8
    max_fix_age_s: float = 600.0
    trail_max: int = 200
    auto_arrive: bool = False
    osrm_url: str = ""
    ip_geo_url: str = ""
    geoip_db: str = ""
    trust_proxy: bool = False
    default_lat: float = 22.5726
    default_lng: float = 88.3639
    presence_precision: int = 2
    max_bbox_deg: float = 5.0
    max_rows_per_shard: int = 3000
    cluster_px: int = 64
    heat_grid_per_deg: int = 100
    heat_min_count: int = 1
    ws_heartbeat_s: float = 25.0
    ws_queue_size: int = 256
    ws_max_topics: int = 300

    @classmethod
    def from_env(cls) -> "MapSettings":
        try:
            dlat, dlng = (float(x) for x in _env("MAP_DEFAULT_CENTER", "22.5726,88.3639").split(","))
        except ValueError:
            dlat, dlng = 22.5726, 88.3639
        return cls(
            redis_url=normalize_redis_url(_env("MAP_REDIS_URL") or _env("REDIS_URL")),
            redis_fallback_url=normalize_redis_url(_env("MAP_REDIS_FALLBACK_URL") or _env("REDIS_FALLBACK_URL")),
            kafka_servers=_env("MAP_KAFKA_BOOTSTRAP_SERVERS"),
            kafka_topic=_env("MAP_KAFKA_TOPIC", "scrapmart.map.events"),
            kafka_protocol=_env("MAP_KAFKA_SECURITY_PROTOCOL", "PLAINTEXT").upper(),
            kafka_sasl_mechanism=_env("MAP_KAFKA_SASL_MECHANISM"),
            kafka_sasl_username=_env("MAP_KAFKA_SASL_USERNAME"),
            kafka_sasl_password=_env("MAP_KAFKA_SASL_PASSWORD"),
            kafka_compression=_env("MAP_KAFKA_COMPRESSION", "gzip"),
            presence_ttl_s=int(_env_num("MAP_PRESENCE_TTL_S", 120)),
            max_live_accuracy_m=_env_num("MAP_MAX_LIVE_ACCURACY_M", 300),
            max_speed_kmh=_env_num("MAP_MAX_SPEED_KMH", 160),
            auto_arrive=_env_bool("MAP_AUTO_ARRIVE", False),
            osrm_url=_env("MAP_OSRM_URL").rstrip("/"),
            ip_geo_url=_env("MAP_IP_GEO_URL"),
            geoip_db=_env("MAP_GEOIP_DB"),
            trust_proxy=_env_bool("MAP_TRUST_PROXY_HEADERS", False),
            default_lat=dlat,
            default_lng=dlng,
            presence_precision=int(_env_num("MAP_PRESENCE_PRECISION", 2)),
            heat_min_count=int(_env_num("MAP_HEAT_MIN_COUNT", 1)),
        )


# =============================================================================
# 1. GEO + SMALL UTILITIES
# =============================================================================
def haversine_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lng2 - lng1) / 2) ** 2
    return 2 * EARTH_RADIUS_KM * 1000 * math.asin(min(1.0, math.sqrt(a)))


@dataclass(frozen=True)
class BBox:
    south: float
    west: float
    north: float
    east: float

    @classmethod
    def around(cls, lat: float, lng: float, radius_km: float) -> "BBox":
        dlat = radius_km / 111.32
        dlng = radius_km / (111.32 * max(math.cos(math.radians(lat)), 0.01))
        return cls(max(-90.0, lat - dlat), max(-180.0, lng - dlng), min(90.0, lat + dlat), min(180.0, lng + dlng))

    @property
    def center(self) -> tuple[float, float]:
        mid_lng = (self.west + self.east) / 2 if self.west <= self.east else ((self.west + self.east + 360) / 2 + 180) % 360 - 180
        return (self.south + self.north) / 2, mid_lng

    @property
    def lng_span(self) -> float:
        return self.east - self.west if self.west <= self.east else self.east - self.west + 360

    def contains(self, lat: float, lng: float) -> bool:
        if not self.south <= lat <= self.north:
            return False
        return self.west <= lng <= self.east if self.west <= self.east else (lng >= self.west or lng <= self.east)

    def expanded(self, deg: float) -> "BBox":
        return BBox(max(-90.0, self.south - deg), max(-180.0, self.west - deg), min(90.0, self.north + deg), min(180.0, self.east + deg))

    def lng_clause(self):
        if self.west <= self.east:
            return Listing.longitude.between(self.west, self.east)
        return or_(Listing.longitude >= self.west, Listing.longitude <= self.east)


def cell_key(lat: float, lng: float, size: float = AREA_CELL_DEG) -> str:
    return f"{math.floor(lat / size)}:{math.floor(lng / size)}"


def cells_for_bbox(b: BBox, size: float = AREA_CELL_DEG) -> list[str] | None:
    """All pub/sub cells covering the bbox, or None when it would exceed MAX_AREA_CELLS."""
    lat_range = range(math.floor(b.south / size), math.floor(b.north / size) + 1)
    spans = [(b.west, b.east)] if b.west <= b.east else [(b.west, 180.0), (-180.0, b.east)]
    cells: list[str] = []
    for lo, hi in spans:
        for i in lat_range:
            for j in range(math.floor(lo / size), math.floor(hi / size) + 1):
                cells.append(f"{i}:{j}")
                if len(cells) > MAX_AREA_CELLS:
                    return None
    return cells


def mask(v: float | None, digits: int = 2) -> float | None:
    return None if v is None else round(v, digits)


def estimate_eta_s(dist_m: float, speed_mps: float | None = None, road_factor: float = 1.35) -> int:
    """Offline ETA: road distance ~ 1.35 x straight line; speed = observed (clamped) or 20 km/h urban default."""
    v = speed_mps if speed_mps and speed_mps > 1.5 else 20 / 3.6
    v = min(max(v, 8 / 3.6), 45 / 3.6)
    return int(dist_m * road_factor / v)


_MISS = object()


class TTLCache:
    """Small thread-safe LRU+TTL cache (used from both the event loop and worker threads)."""

    def __init__(self, ttl: float, maxsize: int = 10_000):
        self.ttl, self.maxsize = ttl, maxsize
        self._d: OrderedDict[Any, tuple[float, Any]] = OrderedDict()
        self._lock = threading.Lock()

    def get(self, key: Any) -> tuple[bool, Any]:
        with self._lock:
            item = self._d.get(key)
            if item is None or item[0] < time.monotonic():
                self._d.pop(key, None)
                return False, None
            self._d.move_to_end(key)
            return True, item[1]

    def set(self, key: Any, value: Any, ttl: float | None = None) -> None:
        with self._lock:
            self._d[key] = (time.monotonic() + (self.ttl if ttl is None else ttl), value)
            self._d.move_to_end(key)
            while len(self._d) > self.maxsize:
                self._d.popitem(last=False)

    def pop(self, key: Any) -> None:
        with self._lock:
            self._d.pop(key, None)


class RateLimiter:
    """Token bucket per key (in-process; each worker enforces its own share)."""

    def __init__(self, rate: float, burst: float):
        self.rate, self.burst = rate, burst
        self._b: dict[str, tuple[float, float]] = {}

    def allow(self, key: str, cost: float = 1.0) -> tuple[bool, float]:
        now = time.monotonic()
        tokens, ts = self._b.get(key, (self.burst, now))
        tokens = min(self.burst, tokens + (now - ts) * self.rate)
        if tokens >= cost:
            self._b[key] = (tokens - cost, now)
            return True, 0.0
        self._b[key] = (tokens, now)
        return False, (cost - tokens) / self.rate

    def forget(self, key: str) -> None:
        self._b.pop(key, None)

    def gc(self, max_idle: float = 300.0) -> None:
        cutoff = time.monotonic() - max_idle
        for k in [k for k, (_, ts) in self._b.items() if ts < cutoff]:
            self._b.pop(k, None)


# =============================================================================
# 2. LOCATION QUALITY: models, validation, Kalman smoothing
# =============================================================================
class FixSource(str, Enum):
    GPS = "gps"
    NETWORK = "network"
    CACHED = "cached"
    MANUAL = "manual"   # user dragged the pin
    IP = "ip"
    PROFILE = "profile"
    DEFAULT = "default"


_DEFAULT_ACC = {FixSource.GPS: 30.0, FixSource.NETWORK: 800.0, FixSource.CACHED: 1500.0, FixSource.MANUAL: 50.0,
                FixSource.IP: 5000.0, FixSource.PROFILE: 3000.0, FixSource.DEFAULT: 50000.0}
_SERVER_ONLY_SOURCES = {FixSource.IP, FixSource.PROFILE, FixSource.DEFAULT}


class FixIn(BaseModel):
    lat: float = Field(ge=-90, le=90)
    lng: float = Field(ge=-180, le=180)
    accuracy_m: float | None = Field(default=None, ge=0, le=1e7, description="coords.accuracy (metres, 68% radius)")
    source: FixSource = FixSource.GPS
    captured_at: float | None = Field(default=None, description="GeolocationPosition.timestamp (ms) or epoch seconds")
    speed_mps: float | None = Field(default=None, ge=0, le=120)
    heading_deg: float | None = Field(default=None, ge=0, le=360)
    listing_id: uuid.UUID | None = Field(default=None, description="Collector: pickup being driven (optional hint)")


class FixResult(BaseModel):
    accepted: bool
    reason: str | None = None
    lat: float | None = None
    lng: float | None = None
    accuracy_m: float | None = None
    quality: Literal["good", "fair", "poor"] | None = None
    tracking: list[uuid.UUID] = Field(default_factory=list)
    online: bool = False
    retry_after_s: float | None = None


@dataclass
class Assessment:
    ok: bool
    reason: str | None
    acc: float
    cap_ts: float | None


def _norm_ts(v: float | None) -> float | None:
    if v is None:
        return None
    return v / 1000.0 if v > 1e11 else float(v)


def assess_fix(fx: FixIn, now: float, cfg: MapSettings) -> Assessment:
    acc = fx.accuracy_m if fx.accuracy_m else _DEFAULT_ACC[fx.source]
    if abs(fx.lat) < 1e-3 and abs(fx.lng) < 1e-3:
        return Assessment(False, "null_island", acc, None)  # classic bogus (0,0) from broken providers
    if not (math.isfinite(fx.lat) and math.isfinite(fx.lng)):
        return Assessment(False, "not_finite", acc, None)
    if fx.source in _SERVER_ONLY_SOURCES:
        return Assessment(False, "low_quality_source", acc, None)
    if acc > 100_000:
        return Assessment(False, "accuracy_absurd", acc, None)
    cap = _norm_ts(fx.captured_at)
    if cap is not None:
        if now - cap > cfg.max_fix_age_s:
            return Assessment(False, "stale_fix", acc, cap)
        cap = min(cap, now)  # tolerate client clock running ahead
    return Assessment(True, None, acc, cap)


def kalman_update(prev: dict | None, lat: float, lng: float, acc: float, now: float,
                  q_m_per_s: float = 3.0) -> tuple[float, float, float]:
    """1-state-per-axis Kalman filter in metres-variance (the standard 'GPS smoothing' filter).
    Variance grows with elapsed time (we may have moved) and shrinks with each measurement
    weighted by its reported accuracy: precise fixes dominate, noisy ones barely move the pin."""
    acc = max(acc, 3.0)
    if prev is None or now - prev["rx"] > 600:
        return lat, lng, acc * acc
    var = prev["var"] + max(0.0, now - prev["rx"]) * q_m_per_s ** 2
    k = var / (var + acc * acc)
    return prev["lat"] + k * (lat - prev["lat"]), prev["lng"] + k * (lng - prev["lng"]), (1 - k) * var


def _plausible(prev: dict, lat: float, lng: float, acc: float, now: float, cfg: MapSettings) -> bool:
    """Reject teleports: implied speed (after discounting both accuracies) must be vehicle-possible."""
    if prev.get("acc", 0) > 1000 or acc > 1000:
        return True  # coarse fixes can't prove a jump
    dt = max(now - prev["rx"], 0.5)
    dist = haversine_m(prev["lat"], prev["lng"], lat, lng)
    slack = acc + math.sqrt(max(prev.get("var", 0.0), 0.0))
    return max(0.0, dist - slack) / dt <= cfg.max_speed_kmh / 3.6


def _quality(acc: float) -> str:
    return "good" if acc <= 30 else "fair" if acc <= 150 else "poor"


# =============================================================================
# 3. STORES: latest fix / presence geo-index / trails  (memory | redis)
# =============================================================================
class LocationStore:
    kind = "abstract"

    async def get_fix(self, uid: str) -> dict | None: raise NotImplementedError
    async def put_fix(self, uid: str, fix: dict, ttl: int) -> None: raise NotImplementedError
    async def presence_set(self, uid: str, lat: float, lng: float, ts: float) -> None: raise NotImplementedError
    async def presence_remove(self, uid: str) -> None: raise NotImplementedError

    async def presence_search(self, lat: float, lng: float, radius_km: float, limit: int, max_age: float
                              ) -> list[tuple[str, float, float, float, float]]:
        """-> [(uid, lat, lng, distance_km, last_seen_epoch)] nearest first"""
        raise NotImplementedError

    async def trail_push(self, key: str, point: list, maxlen: int, ttl: int) -> None: raise NotImplementedError
    async def trail_get(self, key: str, n: int) -> list[list]: raise NotImplementedError
    async def kv_get(self, key: str) -> Any: raise NotImplementedError
    async def kv_set(self, key: str, value: Any, ttl: int) -> None: raise NotImplementedError
    async def kv_del(self, key: str) -> None: raise NotImplementedError
    async def ping(self) -> bool: return True
    async def gc(self) -> None: return None
    async def close(self) -> None: return None


class MemoryStore(LocationStore):
    kind = "memory"

    def __init__(self) -> None:
        self._fix: dict[str, tuple[float, dict]] = {}
        self._presence: dict[str, tuple[float, float, float]] = {}
        self._trail: dict[str, tuple[float, deque]] = {}
        self._kv: dict[str, tuple[float, Any]] = {}

    async def get_fix(self, uid):
        item = self._fix.get(uid)
        return dict(item[1]) if item and item[0] > time.time() else None

    async def put_fix(self, uid, fix, ttl):
        self._fix[uid] = (time.time() + ttl, dict(fix))

    async def presence_set(self, uid, lat, lng, ts):
        self._presence[uid] = (lat, lng, ts)

    async def presence_remove(self, uid):
        self._presence.pop(uid, None)

    async def presence_search(self, lat, lng, radius_km, limit, max_age):
        cutoff, out = time.time() - max_age, []
        for uid, (la, lo, ts) in list(self._presence.items()):
            if ts < cutoff:
                continue
            d = haversine_m(lat, lng, la, lo) / 1000
            if d <= radius_km:
                out.append((uid, la, lo, d, ts))
        out.sort(key=lambda x: x[3])
        return out[:limit]

    async def trail_push(self, key, point, maxlen, ttl):
        _, dq = self._trail.get(key) or (0, deque(maxlen=maxlen))
        dq.append(point)
        self._trail[key] = (time.time() + ttl, dq)

    async def trail_get(self, key, n):
        item = self._trail.get(key)
        return list(item[1])[-n:] if item and item[0] > time.time() else []

    async def kv_get(self, key):
        item = self._kv.get(key)
        return item[1] if item and item[0] > time.time() else None

    async def kv_set(self, key, value, ttl):
        self._kv[key] = (time.time() + ttl, value)

    async def kv_del(self, key):
        self._kv.pop(key, None)

    async def gc(self):
        now = time.time()
        for table in (self._fix, self._trail, self._kv):
            for k in [k for k, v in table.items() if v[0] < now]:
                table.pop(k, None)
        for k in [k for k, v in self._presence.items() if v[2] < now - 3600]:
            self._presence.pop(k, None)


class RedisStore(LocationStore):
    """Works with any Redis >= 6.2 (GEOSEARCH): Upstash, ElastiCache, Redis Cloud, Memorystore..."""

    kind = "redis"
    P_GEO, P_TS = "mp:presence", "mp:presence:ts"

    def __init__(self, url: str):
        import redis.asyncio as aioredis  # lazy: only needed when configured

        self.url = normalize_redis_url(url)
        self.r = aioredis.from_url(self.url, decode_responses=True, socket_timeout=3, socket_connect_timeout=3,
                                   health_check_interval=30, retry_on_timeout=True)

    async def ping(self):
        return bool(await self.r.ping())

    async def get_fix(self, uid):
        v = await self.r.get(f"mp:fix:{uid}")
        return json.loads(v) if v else None

    async def put_fix(self, uid, fix, ttl):
        await self.r.set(f"mp:fix:{uid}", json.dumps(fix), ex=ttl)

    async def presence_set(self, uid, lat, lng, ts):
        async with self.r.pipeline(transaction=False) as p:
            p.geoadd(self.P_GEO, (lng, lat, uid))
            p.zadd(self.P_TS, {uid: ts})
            await p.execute()

    async def presence_remove(self, uid):
        async with self.r.pipeline(transaction=False) as p:
            p.zrem(self.P_GEO, uid)
            p.zrem(self.P_TS, uid)
            await p.execute()

    async def presence_search(self, lat, lng, radius_km, limit, max_age):
        stale = await self.r.zrangebyscore(self.P_TS, "-inf", time.time() - max_age)
        if stale:
            async with self.r.pipeline(transaction=False) as p:
                p.zrem(self.P_GEO, *stale)
                p.zrem(self.P_TS, *stale)
                await p.execute()
        hits = await self.r.geosearch(self.P_GEO, longitude=lng, latitude=lat, radius=radius_km, unit="km",
                                      sort="ASC", count=limit, withdist=True, withcoord=True)
        if not hits:
            return []
        async with self.r.pipeline(transaction=False) as p:
            for h in hits:
                p.zscore(self.P_TS, h[0])
            stamps = await p.execute()
        return [(h[0], h[2][1], h[2][0], float(h[1]), float(ts or 0)) for h, ts in zip(hits, stamps) if ts]

    async def trail_push(self, key, point, maxlen, ttl):
        k = f"mp:trail:{key}"
        async with self.r.pipeline(transaction=False) as p:
            p.rpush(k, json.dumps(point))
            p.ltrim(k, -maxlen, -1)
            p.expire(k, ttl)
            await p.execute()

    async def trail_get(self, key, n):
        return [json.loads(x) for x in await self.r.lrange(f"mp:trail:{key}", -n, -1)]

    async def kv_get(self, key):
        v = await self.r.get(f"mp:kv:{key}")
        return json.loads(v) if v else None

    async def kv_set(self, key, value, ttl):
        await self.r.set(f"mp:kv:{key}", json.dumps(value), ex=ttl)

    async def kv_del(self, key):
        await self.r.delete(f"mp:kv:{key}")

    async def close(self):
        with contextlib.suppress(Exception):
            await self.r.aclose()


# =============================================================================
# 4. EVENTS: hub (websockets) + bus (local | kafka)
# =============================================================================
@dataclass
class Event:
    topic: str   # listing:{id} | cell:{i:j} | user:{id}
    type: str    # collector.location | geofence | listing.created | listing.status | bid.placed ...
    data: dict
    ts: int = field(default_factory=lambda: int(time.time() * 1000))

    def wire(self) -> str:
        return json.dumps({"type": self.type, "topic": self.topic, "data": self.data, "ts": self.ts},
                          separators=(",", ":"), default=str)

    @staticmethod
    def parse(raw: bytes | str) -> "Event":
        d = json.loads(raw)
        return Event(d["topic"], d["type"], d.get("data") or {}, int(d.get("ts") or 0))


def _msg(type_: str, **fields: Any) -> str:
    return json.dumps({"type": type_, **fields}, separators=(",", ":"), default=str)


class Connection:
    def __init__(self, ws: WebSocket, user: User, exp: float, qsize: int):
        self.id = uuid.uuid4().hex
        self.ws, self.user, self.exp = ws, user, exp
        self.topics: set[str] = set()
        self.area_topics: set[str] = set()
        self.queue: asyncio.Queue[str] = asyncio.Queue(maxsize=qsize)
        self.dropped = 0

    def offer(self, payload: str, droppable: bool = False) -> None:
        """Never blocks the producer. A slow client loses stale location frames, not status events."""
        try:
            self.queue.put_nowait(payload)
        except asyncio.QueueFull:
            self.dropped += 1
            if droppable:
                return
            with contextlib.suppress(asyncio.QueueEmpty, asyncio.QueueFull):
                self.queue.get_nowait()
                self.queue.put_nowait(payload)

    async def run_sender(self, heartbeat_s: float) -> None:
        try:
            while True:
                try:
                    payload = await asyncio.wait_for(self.queue.get(), timeout=heartbeat_s)
                except asyncio.TimeoutError:
                    payload = _msg("hb", t=time.time())  # keeps proxies/LBs from idling the socket out
                await asyncio.wait_for(self.ws.send_text(payload), timeout=10)
        except (asyncio.CancelledError, WebSocketDisconnect):
            raise
        except Exception:  # noqa: BLE001 - slow/broken client: let the receive loop notice and clean up
            with contextlib.suppress(Exception):
                await self.ws.close(code=1011)


class Hub:
    def __init__(self) -> None:
        self._topics: dict[str, set[Connection]] = defaultdict(set)
        self._conns: dict[str, Connection] = {}

    @property
    def size(self) -> int:
        return len(self._conns)

    def add(self, c: Connection) -> None:
        self._conns[c.id] = c

    def subscribe(self, c: Connection, topic: str) -> None:
        c.topics.add(topic)
        self._topics[topic].add(c)

    def unsubscribe(self, c: Connection, topic: str) -> None:
        c.topics.discard(topic)
        subs = self._topics.get(topic)
        if subs:
            subs.discard(c)
            if not subs:
                self._topics.pop(topic, None)

    def remove(self, c: Connection) -> None:
        for t in list(c.topics):
            self.unsubscribe(c, t)
        self._conns.pop(c.id, None)

    async def dispatch(self, ev: Event) -> None:
        subs = self._topics.get(ev.topic)
        if not subs:
            return
        payload, droppable = ev.wire(), ev.type == "collector.location"
        for c in list(subs):
            c.offer(payload, droppable)

    async def close_all(self) -> None:
        for c in list(self._conns.values()):
            with contextlib.suppress(Exception):
                await c.ws.close(code=1001)


class EventBus:
    kind = "abstract"

    async def start(self) -> None: ...
    async def stop(self) -> None: ...
    async def publish(self, ev: Event) -> None: raise NotImplementedError


class LocalBus(EventBus):
    """In-process fan-out. Correct for ONE worker; use Kafka for several workers/instances."""

    kind = "local"

    def __init__(self, hub: Hub):
        self.hub = hub

    async def publish(self, ev):
        await self.hub.dispatch(ev)


class KafkaBus(EventBus):
    """Cloud Kafka (Confluent, Upstash, Aiven, MSK...). Every worker consumes the whole topic with its own
    ephemeral consumer group and dispatches to ITS websocket clients. Message key = topic string, so all
    events of one listing/cell stay ordered within a partition."""

    kind = "kafka"

    def __init__(self, cfg: MapSettings, hub: Hub):
        self.cfg, self.hub = cfg, hub
        self.producer: Any = None
        self._task: asyncio.Task | None = None
        self._stopping = False

    def _common(self) -> dict[str, Any]:
        c = self.cfg
        kw: dict[str, Any] = {"bootstrap_servers": c.kafka_servers, "client_id": f"scrapmart-map-{os.getpid()}"}
        if c.kafka_protocol != "PLAINTEXT":
            kw["security_protocol"] = c.kafka_protocol
            if "SSL" in c.kafka_protocol:
                from aiokafka.helpers import create_ssl_context
                kw["ssl_context"] = create_ssl_context()
            if c.kafka_sasl_mechanism:
                kw.update(sasl_mechanism=c.kafka_sasl_mechanism, sasl_plain_username=c.kafka_sasl_username,
                          sasl_plain_password=c.kafka_sasl_password)
        return kw

    async def start(self) -> None:
        from aiokafka import AIOKafkaProducer

        self.producer = AIOKafkaProducer(**self._common(), acks=1, linger_ms=20,
                                         compression_type=self.cfg.kafka_compression or None,
                                         request_timeout_ms=8000)
        await self.producer.start()
        self._task = asyncio.create_task(self._consume(), name="map-kafka-consumer")

    async def stop(self) -> None:
        self._stopping = True
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._task
        if self.producer:
            with contextlib.suppress(Exception):
                await self.producer.stop()

    async def publish(self, ev: Event) -> None:
        try:
            fut = await self.producer.send(self.cfg.kafka_topic, value=ev.wire().encode(), key=ev.topic.encode())
        except Exception as exc:  # noqa: BLE001 - broker down: degrade to same-worker delivery
            log.warning("kafka publish failed (%s); delivering locally", exc)
            await self.hub.dispatch(ev)
            return

        def _done(f: asyncio.Future) -> None:
            if not f.cancelled() and f.exception():
                log.warning("kafka delivery failed (%s); delivering locally", f.exception())
                asyncio.ensure_future(self.hub.dispatch(ev))

        fut.add_done_callback(_done)

    async def _consume(self) -> None:
        from aiokafka import AIOKafkaConsumer

        backoff = 1.0
        while not self._stopping:
            consumer = AIOKafkaConsumer(self.cfg.kafka_topic, group_id=f"scrapmart-map-{uuid.uuid4().hex[:12]}",
                                        auto_offset_reset="latest", enable_auto_commit=False, **self._common())
            try:
                await consumer.start()
                backoff = 1.0
                log.info("kafka consumer attached to %s", self.cfg.kafka_topic)
                async for msg in consumer:
                    try:
                        ev = Event.parse(msg.value)
                        if ev.ts and time.time() * 1000 - ev.ts > 60_000:
                            continue  # stale replay after a reconnect
                        await self.hub.dispatch(ev)
                    except Exception:  # noqa: BLE001
                        log.exception("bad kafka message skipped")
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                log.warning("kafka consumer error: %s (retry in %.0fs)", exc, backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30.0)
            finally:
                with contextlib.suppress(Exception):
                    await consumer.stop()


class RedisBus(EventBus):
    """Cloud Redis pub/sub event bus (Upstash, ElastiCache, etc.).
    Allows multiple worker processes to broadcast websocket events to each other's clients."""

    kind = "redis"

    def __init__(self, url: str, hub: Hub, channel: str = "scrapmart:map:events"):
        import redis.asyncio as aioredis

        self.url = normalize_redis_url(url)
        self.hub = hub
        self.channel = channel
        self.r = aioredis.from_url(
            self.url,
            decode_responses=True,
            socket_timeout=5,
            socket_connect_timeout=5,
            health_check_interval=30,
            retry_on_timeout=True,
        )
        self._task: asyncio.Task | None = None
        self._stopping = False
        self._ready = asyncio.Event()

    async def start(self) -> None:
        self._stopping = False
        self._task = asyncio.create_task(self._consume(), name="map-redis-consumer")
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(self._ready.wait(), timeout=5.0)

    async def stop(self) -> None:
        self._stopping = True
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._task
        with contextlib.suppress(Exception):
            await self.r.aclose()

    async def publish(self, ev: Event) -> None:
        try:
            await self.r.publish(self.channel, ev.wire())
        except Exception as exc:  # noqa: BLE001
            log.warning("redis pub/sub publish failed (%s); delivering locally", exc)
            await self.hub.dispatch(ev)

    async def _consume(self) -> None:
        import redis.asyncio as aioredis

        backoff = 1.0
        while not self._stopping:
            sub_r = None
            pubsub = None
            try:
                sub_r = aioredis.from_url(
                    self.url,
                    decode_responses=True,
                    socket_timeout=15,
                    socket_connect_timeout=5,
                    health_check_interval=30,
                    retry_on_timeout=True,
                )
                pubsub = sub_r.pubsub()
                await pubsub.subscribe(self.channel)
                backoff = 1.0
                self._ready.set()
                log.info("redis pubsub subscriber attached to %s", self.channel)
                while not self._stopping:
                    try:
                        msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
                        if msg and msg.get("type") == "message":
                            ev = Event.parse(msg["data"])
                            if ev.ts and time.time() * 1000 - ev.ts > 60_000:
                                continue  # stale replay
                            await self.hub.dispatch(ev)
                    except asyncio.TimeoutError:
                        pass
                    await asyncio.sleep(0.01)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                if self._stopping:
                    break
                log.warning("redis pubsub consumer error: %s (retry in %.0fs)", exc, backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30.0)
            finally:
                if pubsub:
                    with contextlib.suppress(Exception):
                        await pubsub.unsubscribe(self.channel)
                if sub_r:
                    with contextlib.suppress(Exception):
                        await sub_r.aclose()


# =============================================================================
# 5. ROUTING / ETA  (OSRM optional, offline estimate always available)
# =============================================================================
@dataclass
class RouteInfo:
    distance_m: float
    duration_s: int
    polyline: list[list[float]] | None
    source: str  # osrm | estimate


class RoutingService:
    def __init__(self, cfg: MapSettings):
        self.cfg = cfg
        self.cache = TTLCache(20, 5000)
        self._down_until = 0.0

    def _osrm_sync(self, a: tuple[float, float], b: tuple[float, float]) -> RouteInfo:
        import requests

        url = f"{self.cfg.osrm_url}/route/v1/driving/{a[1]:.6f},{a[0]:.6f};{b[1]:.6f},{b[0]:.6f}"
        resp = requests.get(url, params={"overview": "simplified", "geometries": "geojson"}, timeout=(1.5, 3.0))
        resp.raise_for_status()
        route = resp.json()["routes"][0]
        pts = [[c[1], c[0]] for c in route["geometry"]["coordinates"]]
        if len(pts) > 300:
            step = math.ceil(len(pts) / 300)
            pts = pts[::step] + [pts[-1]]
        return RouteInfo(float(route["distance"]), int(route["duration"]), pts, "osrm")

    async def route(self, a: tuple[float, float], b: tuple[float, float], speed_mps: float | None = None,
                    want_polyline: bool = False) -> RouteInfo:
        key = (round(a[0], 4), round(a[1], 4), round(b[0], 4), round(b[1], 4))
        if self.cfg.osrm_url and time.monotonic() >= self._down_until:
            hit, val = self.cache.get(key)
            if hit and (val.polyline or not want_polyline):
                return val
            try:
                info = await run_in_threadpool(self._osrm_sync, a, b)
                self.cache.set(key, info)
                return info
            except Exception as exc:  # noqa: BLE001
                self._down_until = time.monotonic() + 30  # circuit breaker
                log.warning("OSRM unavailable (%s); using estimate for 30s", exc)
        d = haversine_m(a[0], a[1], b[0], b[1])
        return RouteInfo(d * 1.35, estimate_eta_s(d, speed_mps), [[a[0], a[1]], [b[0], b[1]]] if want_polyline else None, "estimate")


# =============================================================================
# 6. RUNTIME SINGLETON + LIFECYCLE
# =============================================================================
class _Runtime:
    def __init__(self, cfg: MapSettings):
        self.cfg = cfg
        self.hub = Hub()
        self.store: LocationStore = MemoryStore()
        self.bus: EventBus = LocalBus(self.hub)
        self.routing = RoutingService(cfg)
        self.rate = RateLimiter(rate=1.0 / cfg.min_ping_interval_s, burst=3)
        self.ws_rate = RateLimiter(rate=20, burst=40)
        self.assign_cache = TTLCache(10, 20_000)
        self.active_cache = TTLCache(10, 20_000)
        self.ip_cache = TTLCache(3600, 20_000)
        self.geoip_reader: Any = None
        self.loop: asyncio.AbstractEventLoop | None = None
        self.tasks: set[asyncio.Task] = set()

    def spawn(self, coro) -> None:
        t = asyncio.create_task(coro)
        self.tasks.add(t)
        t.add_done_callback(self.tasks.discard)


_rt: _Runtime | None = None


def rt() -> _Runtime:
    if _rt is None:
        raise HTTPException(503, "Map system is not running")
    return _rt


_INDEX_SQL = (
    "CREATE INDEX IF NOT EXISTS ix_listings_open_geo ON listings (latitude, longitude) "
    "WHERE status = 'posted' AND latitude IS NOT NULL",
    "CREATE INDEX IF NOT EXISTS ix_listings_done_at ON listings (completed_at) WHERE status = 'completed'",
)


def _ensure_indexes_sync() -> None:
    def run(db, sid: int) -> int:
        if db.get_bind().dialect.name != "postgresql":
            return 0
        for stmt in _INDEX_SQL:
            db.execute(text(stmt))
        db.commit()
        return len(_INDEX_SQL)

    res = get_router().scatter(run)
    if res.failed:
        log.warning("map index creation failed on shards %s", sorted(res.failed))


async def init_map_system(cfg: MapSettings | None = None) -> _Runtime:
    global _rt
    if _rt is not None:
        return _rt
    cfg = cfg or MapSettings.from_env()
    r = _Runtime(cfg)
    r.loop = asyncio.get_running_loop()
    if cfg.redis_url:
        try:
            store = RedisStore(cfg.redis_url)
            await store.ping()
            r.store = store
            log.info("Map system: RedisStore connected to primary Redis")
        except Exception as exc:  # noqa: BLE001
            log.error("Primary Redis unavailable (%s)", exc)
            if cfg.redis_fallback_url:
                try:
                    store = RedisStore(cfg.redis_fallback_url)
                    await store.ping()
                    r.store = store
                    log.info("Map system: RedisStore fell back to secondary Redis")
                except Exception as fb_exc:
                    log.error("Secondary Redis also unavailable (%s) - falling back to in-memory store", fb_exc)
            else:
                log.warning("Falling back to in-memory store")

    if cfg.kafka_servers:
        bus = KafkaBus(cfg, r.hub)
        try:
            await bus.start()
            r.bus = bus
            if r.store.kind == "memory":
                log.warning("Kafka bus with in-memory store: latest positions are NOT shared between workers. Set MAP_REDIS_URL.")
        except Exception as exc:  # noqa: BLE001
            log.error("Kafka unavailable (%s) - falling back to in-process bus", exc)
            with contextlib.suppress(Exception):
                await bus.stop()
    elif cfg.redis_url:
        bus = RedisBus(cfg.redis_url, r.hub)
        try:
            await bus.start()
            r.bus = bus
            log.info("Map system: RedisBus pub/sub connected to primary Redis")
        except Exception as exc:  # noqa: BLE001
            log.error("Primary Redis bus unavailable (%s)", exc)
            if cfg.redis_fallback_url:
                try:
                    bus = RedisBus(cfg.redis_fallback_url, r.hub)
                    await bus.start()
                    r.bus = bus
                    log.info("Map system: RedisBus fell back to secondary Redis")
                except Exception as fb_exc:
                    log.error("Secondary Redis bus also unavailable (%s) - falling back to local bus", fb_exc)
    await run_in_threadpool(_ensure_indexes_sync)
    r.spawn(_gc_loop(r))
    _rt = r
    log.info("Map system up (store=%s bus=%s osrm=%s)", r.store.kind, r.bus.kind, bool(cfg.osrm_url))
    return r


async def close_map_system() -> None:
    global _rt
    r, _rt = _rt, None
    if r is None:
        return
    for t in list(r.tasks):
        t.cancel()
    await r.hub.close_all()
    await r.bus.stop()
    await r.store.close()
    if r.geoip_reader is not None:
        with contextlib.suppress(Exception):
            r.geoip_reader.close()


async def _gc_loop(r: _Runtime) -> None:
    while True:
        await asyncio.sleep(30)
        try:
            await r.store.gc()
            r.rate.gc()
            r.ws_rate.gc()
        except Exception:  # noqa: BLE001
            log.exception("map gc failed")


def install_map_system(app: FastAPI) -> None:
    """Wrap the app lifespan so the map runtime starts after the shard router and stops before it."""
    original = app.router.lifespan_context

    @asynccontextmanager
    async def wrapped(a: FastAPI):
        async with original(a) as state:
            await init_map_system()
            try:
                yield state
            finally:
                await close_map_system()

    app.router.lifespan_context = wrapped


# ---- sync-safe emitters for the existing (sync) endpoints ---------------------
def _submit(events: list[Event]) -> None:
    r = _rt
    if r is None or r.loop is None or r.loop.is_closed():
        return

    async def _go() -> None:
        for ev in events:
            await r.bus.publish(ev)

    try:
        running = asyncio.get_running_loop()
    except RuntimeError:
        running = None
    if running is r.loop:
        r.spawn(_go())
        return

    fut = asyncio.run_coroutine_threadsafe(_go(), r.loop)
    fut.add_done_callback(lambda f: f.exception() and log.warning("map emit failed: %s", f.exception()))


def emit_listing_event(kind: str, listing: Listing) -> None:
    """Call AFTER commit. Private details go to the two parties; masked public info to the area channel."""
    try:
        r = _rt
        if r is None:
            return
        r.assign_cache.pop(str(listing.id))
        if listing.collector_id:
            r.active_cache.pop(str(listing.collector_id))
        lid = str(listing.id)
        status_val = str(getattr(listing.status, "value", listing.status))
        category_val = str(getattr(listing.category, "value", listing.category)) if getattr(listing, "category", None) else "mixed"
        events = [Event(f"listing:{lid}", kind, {
            "listing_id": lid, "status": status_val, "collector_id": str(listing.collector_id) if listing.collector_id else None,
            "final_price": float(listing.final_price) if listing.final_price is not None else None})]
        if listing.latitude is not None and listing.longitude is not None:
            events.append(Event(f"cell:{cell_key(listing.latitude, listing.longitude)}", kind, {
                "id": lid, "status": status_val, "title": listing.title, "category": category_val,
                "weight_kg": float(listing.weight_kg), "expected_price": float(listing.expected_price),
                "lat": mask(listing.latitude), "lng": mask(listing.longitude)}))
        _submit(events)
    except Exception:  # noqa: BLE001 - map events must never break the business transaction
        log.exception("emit_listing_event failed")


def emit_user_event(user_id: uuid.UUID, kind: str, data: dict | None = None) -> None:
    _submit([Event(f"user:{user_id}", kind, data or {})])


# =============================================================================
# 7. ASSIGNMENT LOOKUPS (who may see/track what) - sync DB code run in threadpool
# =============================================================================
def _assignment_sync(listing_id: uuid.UUID) -> dict | None:
    try:
        with get_router().session_for(listing_id) as s:
            row = s.execute(select(Listing.id, Listing.seller_id, Listing.collector_id, Listing.status,
                                   Listing.latitude, Listing.longitude, Listing.address)
                            .where(Listing.id == listing_id)).first()
    except ShardError:
        return None
    return dict(row._mapping) if row else None


async def get_assignment(listing_id: uuid.UUID, fresh: bool = False) -> dict | None:
    r, key = rt(), str(listing_id)
    if not fresh:
        hit, val = r.assign_cache.get(key)
        if hit:
            return val
    val = await run_in_threadpool(_assignment_sync, listing_id)
    r.assign_cache.set(key, val, 10 if val else 5)
    return val


def _active_for_collector_sync(collector_id: uuid.UUID) -> list[dict]:
    stmt = select(Listing.id, Listing.seller_id, Listing.collector_id, Listing.status, Listing.latitude,
                  Listing.longitude, Listing.address).where(
        Listing.collector_id == collector_id, Listing.status.in_([ListingStatus.ON_THE_WAY, ListingStatus.ARRIVED]))
    res = get_router().scatter(lambda s, sid: [dict(x._mapping) for x in s.execute(stmt).all()])
    if res.failed:
        log.warning("active pickup lookup partial; shards down: %s", sorted(res.failed))
    return [row for rows in res.values() for row in rows]


async def active_pickups(collector_id: uuid.UUID, hint: uuid.UUID | None) -> list[dict]:
    r = rt()
    live = (ListingStatus.ON_THE_WAY, ListingStatus.ARRIVED)
    if hint:
        a = await get_assignment(hint)
        return [a] if a and a["collector_id"] == collector_id and a["status"] in live else []
    hit, val = r.active_cache.get(str(collector_id))
    if hit:
        return val
    val = await run_in_threadpool(_active_for_collector_sync, collector_id)
    r.active_cache.set(str(collector_id), val, 10)
    return val


def _write_profile_location_sync(uid: uuid.UUID, lat: float, lng: float) -> None:
    with get_router().session_for(uid) as s:
        u = s.get(User, uid)
        if u:
            u.latitude, u.longitude = round(lat, 5), round(lng, 5)
            s.commit()


def _load_user_sync(uid: uuid.UUID) -> User | None:
    try:
        with get_router().session_for(uid) as s:
            u = s.get(User, uid)
    except ShardError:
        return None
    return u if u and u.is_active else None


def _auto_arrive_sync(listing_id: uuid.UUID, collector_id: uuid.UUID) -> Listing | None:
    with get_router().session_for(listing_id) as s:
        listing = lock_listing(s, listing_id)
        if listing.status != ListingStatus.ON_THE_WAY or listing.collector_id != collector_id:
            return None
        listing.status = ListingStatus.ARRIVED
        add_event(listing, ListingStatus.ARRIVED, collector_id)
        s.commit()
        return listing


# =============================================================================
# 8. INGESTION PIPELINE (REST + WebSocket share this)
# =============================================================================
async def _geofence(r: _Runtime, a: dict, lat: float, lng: float, acc: float, dist_m: float) -> str:
    key = f"geo:{a['id']}"
    st = await r.store.kv_get(key) or {"s": "far", "n": 0}
    rank = {"far": 0, "near": 1, "arrived": 2}
    state, before = st["s"], st["s"]
    if dist_m - min(acc, 100.0) <= ARRIVE_M:
        cand = "arrived"
    elif dist_m <= NEAR_M:
        cand = "near"
    else:
        cand = "far"
    if rank[cand] > rank[state]:
        st["n"] += 1          # require two consecutive confirmations: one noisy fix can't trigger it
        if st["n"] >= 2:
            state, st["n"] = cand, 0
    else:
        st["n"] = 0
    if state == "arrived" and dist_m > ARRIVE_M * 2.5:     # hysteresis on the way back out
        state = "near"
    if state == "near" and dist_m > NEAR_M * 1.3:
        state = "far"
    st["s"] = state
    await r.store.kv_set(key, st, 6 * 3600)
    if state != before:
        await r.bus.publish(Event(f"listing:{a['id']}", "geofence", {"listing_id": str(a["id"]), "state": state, "distance_m": round(dist_m)}))
        if state == "arrived" and r.cfg.auto_arrive:
            listing = await run_in_threadpool(_auto_arrive_sync, a["id"], a["collector_id"])
            if listing:
                emit_listing_event("listing.status", listing)
    return state


async def _publish_tracking(r: _Runtime, fix: dict, listings: list[dict], moved: bool) -> list[uuid.UUID]:
    out: list[uuid.UUID] = []
    now = time.time()
    for a in listings:
        lid = str(a["id"])
        dist_m = eta_s = None
        if a["latitude"] is not None and a["longitude"] is not None:
            dist_m = haversine_m(fix["lat"], fix["lng"], a["latitude"], a["longitude"])
            eta_s = estimate_eta_s(dist_m, fix.get("spd"))
        if moved:
            await r.store.trail_push(lid, [round(fix["lat"], 6), round(fix["lng"], 6), int(now)], r.cfg.trail_max, r.cfg.fix_ttl_s)
        await r.bus.publish(Event(f"listing:{lid}", "collector.location", {
            "listing_id": lid, "lat": round(fix["lat"], 6), "lng": round(fix["lng"], 6), "accuracy_m": round(fix["acc"], 1),
            "heading_deg": fix.get("hdg"), "speed_mps": round(fix["spd"], 2) if fix.get("spd") is not None else None,
            "distance_m": round(dist_m) if dist_m is not None else None, "eta_s": eta_s}))
        if dist_m is not None:
            await _geofence(r, a, fix["lat"], fix["lng"], fix["acc"], dist_m)
        out.append(a["id"])
    return out


async def ingest_fix(user: User, fx: FixIn) -> FixResult:
    r = rt()
    cfg, now, uid = r.cfg, time.time(), str(user.id)

    allowed, wait = r.rate.allow(f"fix:{uid}")
    if not allowed:
        return FixResult(accepted=False, reason="rate_limited", retry_after_s=round(wait, 2))
    a = assess_fix(fx, now, cfg)
    if not a.ok:
        return FixResult(accepted=False, reason=a.reason)

    prev = await r.store.get_fix(uid)
    if prev:
        if a.cap_ts and prev.get("ts") and a.cap_ts < prev["ts"] - 1:
            return FixResult(accepted=False, reason="out_of_order")
        if not _plausible(prev, fx.lat, fx.lng, a.acc, now, cfg):
            rej = prev.get("rej", 0) + 1
            if rej < 3:
                prev["rej"] = rej
                await r.store.put_fix(uid, prev, cfg.fix_ttl_s)
                return FixResult(accepted=False, reason="implausible_jump")
            prev = None  # three consistent "jumps" in a row: the user genuinely is there now - re-baseline

    lat, lng, var = kalman_update(prev, fx.lat, fx.lng, a.acc, now)
    acc_s = min(a.acc, math.sqrt(var))
    spd = fx.speed_mps
    if spd is None and prev and now - prev["rx"] >= 1.0:
        d = haversine_m(prev["lat"], prev["lng"], lat, lng)
        spd = d / (now - prev["rx"]) if d > 2 * math.sqrt(var) else 0.0
    prev_ema = prev.get("spd") if prev else None
    ema = prev_ema if spd is None else spd if prev_ema is None else 0.7 * prev_ema + 0.3 * spd

    fix = {"lat": lat, "lng": lng, "acc": round(acc_s, 1), "src": fx.source.value, "ts": a.cap_ts or now, "rx": now,
           "hdg": fx.heading_deg, "spd": ema, "var": var, "rej": 0}
    await r.store.put_fix(uid, fix, cfg.fix_ttl_s)
    moved = prev is None or haversine_m(prev["lat"], prev["lng"], lat, lng) >= 15

    tracking: list[uuid.UUID] = []
    online, reason = False, None
    if user.role == Role.COLLECTOR:
        if acc_s <= cfg.max_live_accuracy_m:
            if await r.store.kv_get(f"online:{uid}"):
                await r.store.presence_set(uid, lat, lng, now)
                online = True
            listings = await active_pickups(user.id, fx.listing_id)
            if listings:
                tracking = await _publish_tracking(r, fix, listings, moved)
        else:
            reason = "accuracy_too_low_for_live"   # stored as last-known, just not broadcast

    # throttled write-back so feed distance / default map centre follow the real user
    if fx.source in (FixSource.GPS, FixSource.MANUAL) and acc_s <= 100 and not await r.store.kv_get(f"pw:{uid}"):
        if user.latitude is None or user.longitude is None or haversine_m(user.latitude, user.longitude, lat, lng) >= 300:
            r.spawn(run_in_threadpool(_write_profile_location_sync, user.id, lat, lng))
            await r.store.kv_set(f"pw:{uid}", 1, 900)

    return FixResult(accepted=True, reason=reason, lat=round(lat, 6), lng=round(lng, 6), accuracy_m=round(acc_s, 1),
                     quality=_quality(acc_s), tracking=tracking, online=online)


# =============================================================================
# 9. LOCATION RESOLUTION  (the anti-"position timeout" endpoint)
# =============================================================================
CITY_CENTROIDS: dict[str, tuple[float, float]] = {
    "kolkata": (22.5726, 88.3639), "calcutta": (22.5726, 88.3639), "howrah": (22.5958, 88.2636),
    "siliguri": (26.7271, 88.3953), "durgapur": (23.5204, 87.3119), "asansol": (23.6739, 86.9524),
    "delhi": (28.6139, 77.2090), "new delhi": (28.6139, 77.2090), "noida": (28.5355, 77.3910),
    "gurugram": (28.4595, 77.0266), "gurgaon": (28.4595, 77.0266), "mumbai": (19.0760, 72.8777),
    "bombay": (19.0760, 72.8777), "pune": (18.5204, 73.8567), "bengaluru": (12.9716, 77.5946),
    "bangalore": (12.9716, 77.5946), "chennai": (13.0827, 80.2707), "hyderabad": (17.3850, 78.4867),
    "ahmedabad": (23.0225, 72.5714), "surat": (21.1702, 72.8311), "jaipur": (26.9124, 75.7873),
    "lucknow": (26.8467, 80.9462), "patna": (25.5941, 85.1376), "bhubaneswar": (20.2961, 85.8245),
    "guwahati": (26.1445, 91.7362),
}


def city_centroid(city: str | None) -> tuple[float, float] | None:
    if not city:
        return None
    c = city.strip().lower()
    for name, coords in CITY_CENTROIDS.items():
        if name in c:
            return coords
    return None


@dataclass
class Candidate:
    lat: float
    lng: float
    acc: float
    src: FixSource
    age_s: float = 0.0

    @property
    def conf(self) -> float:
        return round((1.0 / (1.0 + self.acc / 200.0)) * math.exp(-self.age_s / 1800.0), 4)


def _is_public_ip(s: str | None) -> bool:
    try:
        return bool(s) and ipaddress.ip_address(s).is_global
    except ValueError:
        return False


def _client_ip(request: Request, trust: bool) -> str | None:
    ip = None
    if trust:
        for part in (request.headers.get("x-forwarded-for") or "").split(","):
            if _is_public_ip(part.strip()):
                ip = part.strip()
                break
        ip = ip or request.headers.get("cf-connecting-ip")
    if not ip and request.client:
        ip = request.client.host
    return ip if _is_public_ip(ip) else None


def _header_geo(request: Request) -> tuple[float, float] | None:
    """CDN-provided geo headers (only trusted behind your own edge)."""
    h = request.headers
    pairs = (("cf-iplatitude", "cf-iplongitude"), ("x-vercel-ip-latitude", "x-vercel-ip-longitude"),
             ("cloudfront-viewer-latitude", "cloudfront-viewer-longitude"))
    for la, lo in pairs:
        if h.get(la) and h.get(lo):
            try:
                return float(h[la]), float(h[lo])
            except ValueError:
                pass
    if h.get("x-appengine-citylatlong"):
        with contextlib.suppress(ValueError):
            la, lo = h["x-appengine-citylatlong"].split(",")
            return float(la), float(lo)
    return None


def _ip_lookup_sync(r: _Runtime, ip: str) -> tuple[float, float, float] | None:
    cfg = r.cfg
    if cfg.geoip_db:
        try:
            if r.geoip_reader is None:
                import geoip2.database
                r.geoip_reader = geoip2.database.Reader(cfg.geoip_db)
            loc = r.geoip_reader.city(ip).location
            if loc.latitude is not None and loc.longitude is not None:
                return loc.latitude, loc.longitude, float(loc.accuracy_radius or 50) * 1000
        except Exception as exc:  # noqa: BLE001
            log.debug("geoip2 lookup failed: %s", exc)
    if cfg.ip_geo_url:
        try:
            import requests

            d = requests.get(cfg.ip_geo_url.format(ip=ip), timeout=(1.5, 2.5)).json()
            if d.get("success") is False or d.get("status") == "fail":
                return None
            lat, lng = d.get("latitude", d.get("lat")), d.get("longitude", d.get("lon", d.get("lng")))
            if lat is not None and lng is not None:
                return float(lat), float(lng), 5000.0
        except Exception as exc:  # noqa: BLE001
            log.debug("ip geo http lookup failed: %s", exc)
    return None


async def ip_candidate(request: Request) -> Candidate | None:
    r = rt()
    if r.cfg.trust_proxy:
        g = _header_geo(request)
        if g:
            return Candidate(g[0], g[1], 5000.0, FixSource.IP)
    ip = _client_ip(request, r.cfg.trust_proxy)
    if not ip:
        return None
    hit, val = r.ip_cache.get(ip)
    if not hit:
        val = await run_in_threadpool(_ip_lookup_sync, r, ip)
        r.ip_cache.set(ip, val, 3600 if val else 300)  # negative results cached briefly: no hammering a dead provider
    return Candidate(val[0], val[1], val[2], FixSource.IP) if val else None


class ClientGeoError(BaseModel):
    code: int = Field(ge=0, le=3, description="GeolocationPositionError.code: 1 denied, 2 unavailable, 3 timeout")
    message: str | None = Field(default=None, max_length=200)


class ResolveIn(BaseModel):
    fix: FixIn | None = None
    client_error: ClientGeoError | None = None


class CandidateOut(BaseModel):
    source: FixSource
    accuracy_m: float
    confidence: float


class ResolveOut(BaseModel):
    lat: float
    lng: float
    accuracy_m: float
    source: FixSource
    confidence: float
    age_s: float
    zoom: int
    bounds: list[float] = Field(description="[south, west, north, east] covering the accuracy radius")
    needs_confirmation: bool = Field(description="True when the position is only a guess: ask the user to drag the pin")
    hint: str | None = None
    candidates: list[CandidateOut]


_ERR_HINTS = {
    1: "Location permission is blocked. Allow it in the browser's site settings, or drop the pin manually.",
    2: "Your device could not determine its position (location services off, or no Wi-Fi/GPS provider). Drop the pin manually.",
    3: "Getting a GPS fix took too long. Showing an approximate position; move the pin if it is off.",
}


def _zoom_for(acc_m: float) -> int:
    for limit, z in ((50, 17), (200, 16), (1000, 14), (5000, 12), (20000, 11)):
        if acc_m < limit:
            return z
    return 10


@map_router.post("/location/resolve", response_model=ResolveOut)
async def resolve_location(payload: ResolveIn, request: Request, user: CurrentUser):
    """Always returns a usable position. Rank: client fix > last server fix > IP > profile > city > default."""
    r, now = rt(), time.time()
    cands: list[Candidate] = []
    if payload.fix:
        a = assess_fix(payload.fix, now, r.cfg)
        if a.ok:
            cands.append(Candidate(payload.fix.lat, payload.fix.lng, a.acc, payload.fix.source,
                                   max(0.0, now - a.cap_ts) if a.cap_ts else 0.0))
            r.spawn(ingest_fix(user, payload.fix))      # feed the smoother / last-known store, don't block the answer
    prev = await r.store.get_fix(str(user.id))
    if prev and now - prev["rx"] < 1800:
        cands.append(Candidate(prev["lat"], prev["lng"], prev["acc"], FixSource.CACHED, now - prev["rx"]))
    ipc = await ip_candidate(request)
    if ipc:
        cands.append(ipc)
    if user.latitude is not None and user.longitude is not None:
        cands.append(Candidate(user.latitude, user.longitude, 3000.0, FixSource.PROFILE))
    cc = city_centroid(user.city)
    if cc:
        cands.append(Candidate(cc[0], cc[1], 15000.0, FixSource.DEFAULT))
    cands.append(Candidate(r.cfg.default_lat, r.cfg.default_lng, 50000.0, FixSource.DEFAULT))

    best = max(cands, key=lambda c: c.conf)
    b = BBox.around(best.lat, best.lng, max(best.acc, 500.0) / 1000.0)
    hint = _ERR_HINTS.get(payload.client_error.code) if payload.client_error else None
    needs = best.conf < 0.1
    if needs and hint is None:
        hint = "Only an approximate location is available. Drag the pin to your exact spot."
    return ResolveOut(lat=round(best.lat, 6), lng=round(best.lng, 6), accuracy_m=round(best.acc, 1), source=best.src,
                      confidence=best.conf, age_s=round(best.age_s, 1), zoom=_zoom_for(best.acc),
                      bounds=[round(b.south, 5), round(b.west, 5), round(b.north, 5), round(b.east, 5)],
                      needs_confirmation=needs, hint=hint,
                      candidates=[CandidateOut(source=c.src, accuracy_m=round(c.acc, 1), confidence=c.conf) for c in cands])


@map_router.get("/location/strategy")
async def location_strategy(user: CurrentUser):
    """Retry ladder for navigator.geolocation. Cheap & cached first, GPS last; never one short high-accuracy attempt."""
    r = rt()
    return {
        "ladder": [
            {"name": "cached", "options": {"enableHighAccuracy": False, "timeout": 4000, "maximumAge": 600000}},
            {"name": "network", "options": {"enableHighAccuracy": False, "timeout": 15000, "maximumAge": 120000}},
            {"name": "gps", "options": {"enableHighAccuracy": True, "timeout": 25000, "maximumAge": 15000}},
        ],
        "watch": {"enableHighAccuracy": True, "timeout": 30000, "maximumAge": 5000},
        "send_interval_s": {"idle": 30, "tracking": 3},
        "min_interval_s": r.cfg.min_ping_interval_s,
        "max_live_accuracy_m": r.cfg.max_live_accuracy_m,
        "on_error": {"1": "show permission help then /location/resolve", "2": "/location/resolve + manual pin",
                     "3": "next ladder rung, then /location/resolve"},
        "default_center": {"lat": r.cfg.default_lat, "lng": r.cfg.default_lng},
        "ws_heartbeat_s": r.cfg.ws_heartbeat_s,
    }


@map_router.post("/location/fix", response_model=FixResult)
async def post_fix(fx: FixIn, user: CurrentUser):
    """REST ping (use the WebSocket 'location' message when a socket is open - same pipeline)."""
    return await ingest_fix(user, fx)


class PresenceIn(BaseModel):
    online: bool


@map_router.post("/presence")
async def set_presence(payload: PresenceIn, user: CollectorUser):
    """Collector opt-in: appear on sellers' 'collectors nearby' map while online."""
    r, uid = rt(), str(user.id)
    if payload.online:
        await r.store.kv_set(f"online:{uid}", 1, 12 * 3600)
        fix = await r.store.get_fix(uid)
        if fix and time.time() - fix["rx"] < r.cfg.presence_ttl_s and fix["acc"] <= r.cfg.max_live_accuracy_m:
            await r.store.presence_set(uid, fix["lat"], fix["lng"], time.time())
    else:
        await r.store.kv_del(f"online:{uid}")
        await r.store.presence_remove(uid)
    return {"online": payload.online}


# =============================================================================
# 10. MAP DATA: listings (+clusters), heatmap, collectors nearby
# =============================================================================
async def _view_bbox(user: User, south, west, north, east, lat, lng, radius_km) -> tuple[BBox, tuple[float, float] | None]:
    r = rt()
    if None not in (south, west, north, east):
        if south >= north:
            raise HTTPException(422, "south must be < north")
        box = BBox(south, west, north, east)
        if (north - south) > r.cfg.max_bbox_deg or box.lng_span > r.cfg.max_bbox_deg:
            raise HTTPException(422, f"Viewport too large (max {r.cfg.max_bbox_deg} degrees): zoom in")
        return box, (lat, lng) if lat is not None and lng is not None else None
    if lat is None or lng is None:
        fix = await r.store.get_fix(str(user.id))
        if fix:
            lat, lng = fix["lat"], fix["lng"]
        elif user.latitude is not None and user.longitude is not None:
            lat, lng = user.latitude, user.longitude
        else:
            lat, lng = r.cfg.default_lat, r.cfg.default_lng
    return BBox.around(lat, lng, radius_km), (lat, lng)


class MapListing(BaseModel):
    id: uuid.UUID
    title: str
    category: Category
    category_label: str
    weight_kg: float
    expected_price: float
    price_per_kg: float | None
    lat: float
    lng: float
    exact: bool
    distance_km: float | None
    status: ListingStatus
    is_mine: bool
    cover_image: str | None
    created_at: Any


class MapCluster(BaseModel):
    id: str
    lat: float
    lng: float
    count: int
    total_weight_kg: float
    avg_price_per_kg: float | None
    categories: dict[str, int]
    listing_ids: list[uuid.UUID]


class MapListingsOut(BaseModel):
    items: list[MapListing]
    clusters: list[MapCluster]
    total_in_view: int
    truncated: bool
    partial: bool
    center: list[float] | None


def _listings_sync(viewer: User, box: BBox, scope: str, cats: list[Category] | None, min_price: float | None,
                   max_price: float | None, center: tuple[float, float] | None, cap: int
                   ) -> tuple[list[MapListing], bool, bool]:
    masking = settings.MASK_ADDRESS_FOR_PUBLIC
    wide = box.expanded(0.006 if masking else 0.0)   # masked point can sit up to ~0.005 deg from the real one
    cover = (select(ListingImage.url).where(ListingImage.listing_id == Listing.id, ListingImage.position == 0)
             .correlate(Listing).limit(1).scalar_subquery())
    filters = [Listing.latitude.is_not(None), Listing.longitude.is_not(None),
               Listing.latitude.between(wide.south, wide.north), wide.lng_clause()]
    if scope == "open":
        filters.append(Listing.status == ListingStatus.POSTED)
    else:
        filters += [or_(Listing.seller_id == viewer.id, Listing.collector_id == viewer.id),
                    Listing.status.in_([ListingStatus.POSTED, *ACTIVE_PICKUP_STATUSES])]
    if cats:
        filters.append(Listing.category.in_(cats))
    if min_price is not None:
        filters.append(Listing.expected_price >= min_price)
    if max_price is not None:
        filters.append(Listing.expected_price <= max_price)
    stmt = (select(Listing.id, Listing.title, Listing.category, Listing.weight_kg, Listing.expected_price,
                   Listing.latitude, Listing.longitude, Listing.status, Listing.created_at, Listing.seller_id,
                   Listing.collector_id, cover.label("cover"))
            .where(*filters).order_by(Listing.created_at.desc()).limit(cap))
    res = get_router().scatter(lambda s, sid: s.execute(stmt).all())
    truncated = any(len(rows) >= cap for rows in res.values())

    out: list[MapListing] = []
    for rows in res.values():
        for row in rows:
            party = viewer.id in (row.seller_id, row.collector_id)
            exact = party or not masking
            lat, lng = (row.latitude, row.longitude) if exact else (round(row.latitude, 2), round(row.longitude, 2))
            if not box.contains(lat, lng):      # exact test on the MASKED point (see privacy note)
                continue
            w, p = float(row.weight_kg), float(row.expected_price)
            out.append(MapListing(
                id=row.id, title=row.title, category=row.category, category_label=CATEGORY_LABELS[row.category],
                weight_kg=w, expected_price=p, price_per_kg=round(p / w, 2) if w > 0 else None, lat=lat, lng=lng,
                exact=exact, distance_km=round(haversine_m(center[0], center[1], lat, lng) / 1000, 1) if center else None,
                status=row.status, is_mine=party, cover_image=row.cover, created_at=row.created_at))
    out.sort(key=(lambda m: m.distance_km) if center else (lambda m: m.created_at), reverse=not center)
    return out, truncated, bool(res.failed)


def cluster_points(points: list[MapListing], zoom: int, px: int) -> tuple[list[MapListing], list[MapCluster]]:
    cell = 360.0 / (2 ** zoom) * (px / 256.0)
    buckets: dict[tuple[int, int], list[MapListing]] = defaultdict(list)
    for p in points:
        buckets[(math.floor(p.lat / cell), math.floor(p.lng / cell))].append(p)
    singles: list[MapListing] = []
    clusters: list[MapCluster] = []
    for (i, j), pts in buckets.items():
        if len(pts) == 1:
            singles.append(pts[0])
            continue
        n, tw = len(pts), sum(p.weight_kg for p in pts)
        value = sum(p.expected_price for p in pts)
        clusters.append(MapCluster(
            id=f"c{zoom}:{i}:{j}", lat=round(sum(p.lat for p in pts) / n, 6), lng=round(sum(p.lng for p in pts) / n, 6),
            count=n, total_weight_kg=round(tw, 2), avg_price_per_kg=round(value / tw, 2) if tw > 0 else None,
            categories=dict(Counter(p.category.value for p in pts)), listing_ids=[p.id for p in pts[:5]]))
    return singles, clusters


@map_router.get("/listings", response_model=MapListingsOut)
async def map_listings(
    user: CurrentUser,
    south: Annotated[float | None, Query(ge=-90, le=90)] = None,
    west: Annotated[float | None, Query(ge=-180, le=180)] = None,
    north: Annotated[float | None, Query(ge=-90, le=90)] = None,
    east: Annotated[float | None, Query(ge=-180, le=180)] = None,
    lat: Annotated[float | None, Query(ge=-90, le=90)] = None,
    lng: Annotated[float | None, Query(ge=-180, le=180)] = None,
    radius_km: Annotated[float, Query(gt=0, le=200)] = 10,
    zoom: Annotated[int, Query(ge=1, le=20)] = 13,
    scope: Literal["open", "mine"] = "open",
    category: Annotated[list[Category] | None, Query()] = None,
    min_price: Annotated[float | None, Query(ge=0)] = None,
    max_price: Annotated[float | None, Query(ge=0)] = None,
    cluster: Literal["auto", "always", "never"] = "auto",
    limit: Annotated[int, Query(ge=1, le=1000)] = 500,
):
    """Everything on the map: pass the viewport (south/west/north/east) or centre+radius. Zoomed-out
    views come back clustered (singletons in `items`, groups in `clusters`)."""
    r = rt()
    box, center = await _view_bbox(user, south, west, north, east, lat, lng, radius_km)
    points, truncated, partial = await run_in_threadpool(
        _listings_sync, user, box, scope, category, min_price, max_price, center, r.cfg.max_rows_per_shard)
    do_cluster = cluster == "always" or (cluster == "auto" and len(points) > 150)
    if do_cluster and zoom < 18:
        items, clusters = cluster_points(points, zoom, r.cfg.cluster_px)
    else:
        items, clusters = points, []
    if len(items) > limit:
        items, truncated = items[:limit], True
    return MapListingsOut(items=items, clusters=clusters, total_in_view=len(points), truncated=truncated,
                          partial=partial, center=list(center) if center else None)


class HeatCell(BaseModel):
    lat: float
    lng: float
    count: int
    total_weight_kg: float
    avg_price_per_kg: float | None
    intensity: float
    top_categories: list[dict[str, Any]]


class HeatmapOut(BaseModel):
    kind: str
    days: int | None
    cell_deg: float
    cells: list[HeatCell]
    partial: bool


def _heat_sync(box: BBox, kind: str, days: int, grid: int, cutoff) -> tuple[dict, bool]:
    cx = func.floor(Listing.latitude * grid).label("cx")
    cy = func.floor(Listing.longitude * grid).label("cy")
    price_col = Listing.expected_price if kind == "open" else func.coalesce(Listing.final_price, Listing.expected_price)
    filters = [Listing.latitude.is_not(None), Listing.longitude.is_not(None),
               Listing.latitude.between(box.south, box.north), box.lng_clause()]
    if kind == "open":
        filters.append(Listing.status == ListingStatus.POSTED)
    else:
        filters += [Listing.status == ListingStatus.COMPLETED, Listing.completed_at >= cutoff]
    stmt = (select(cx, cy, Listing.category, func.count(Listing.id), func.coalesce(func.sum(Listing.weight_kg), 0),
                   func.coalesce(func.sum(price_col), 0))
            .where(*filters).group_by(cx, cy, Listing.category))
    res = get_router().scatter(lambda s, sid: s.execute(stmt).all())
    cells: dict[tuple[int, int], dict] = {}
    for rows in res.values():
        for x, y, cat, n, w, v in rows:
            c = cells.setdefault((int(x), int(y)), {"n": 0, "w": 0.0, "v": 0.0, "cats": Counter()})
            c["n"] += int(n)
            c["w"] += float(w)
            c["v"] += float(v)
            c["cats"][cat] += int(n)
    return cells, bool(res.failed)


@map_router.get("/heatmap", response_model=HeatmapOut)
async def heatmap(
    user: CurrentUser,
    south: Annotated[float | None, Query(ge=-90, le=90)] = None,
    west: Annotated[float | None, Query(ge=-180, le=180)] = None,
    north: Annotated[float | None, Query(ge=-90, le=90)] = None,
    east: Annotated[float | None, Query(ge=-180, le=180)] = None,
    lat: Annotated[float | None, Query(ge=-90, le=90)] = None,
    lng: Annotated[float | None, Query(ge=-180, le=180)] = None,
    radius_km: Annotated[float, Query(gt=0, le=200)] = 25,
    kind: Literal["open", "sold"] = "open",
    days: Annotated[int, Query(ge=1, le=180)] = 30,
    metric: Literal["count", "weight", "value"] = "count",
    category: Category | None = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 500,
):
    """Where scrap is being listed (open) or actually sold (sold, with realised Rs/kg), aggregated to ~1 km cells."""
    from datetime import datetime, timedelta, timezone

    r = rt()
    box, _ = await _view_bbox(user, south, west, north, east, lat, lng, radius_km)
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    cells, partial = await run_in_threadpool(_heat_sync, box, kind, days, r.cfg.heat_grid_per_deg, cutoff)
    grid = r.cfg.heat_grid_per_deg

    def score(c: dict) -> float:
        return float(c["n"] if metric == "count" else c["w"] if metric == "weight" else c["v"])

    kept = [(k, c) for k, c in cells.items() if c["n"] >= r.cfg.heat_min_count and (category is None or c["cats"].get(category))]
    kept.sort(key=lambda kc: score(kc[1]), reverse=True)
    kept = kept[:limit]
    top = max((score(c) for _, c in kept), default=1.0) or 1.0
    out = [HeatCell(
        lat=round((i + 0.5) / grid, 5), lng=round((j + 0.5) / grid, 5), count=c["n"], total_weight_kg=round(c["w"], 2),
        avg_price_per_kg=round(c["v"] / c["w"], 2) if c["w"] > 0 else None, intensity=round(score(c) / top, 3),
        top_categories=[{"category": k.value, "label": CATEGORY_LABELS[k], "count": n} for k, n in c["cats"].most_common(3)])
        for (i, j), c in kept]
    return HeatmapOut(kind=kind, days=days if kind == "sold" else None, cell_deg=1.0 / grid, cells=out, partial=partial)


class NearbyCollector(BaseModel):
    id: uuid.UUID
    display_name: str
    avatar_url: str | None
    rating_avg: float
    rating_count: int
    lat: float
    lng: float
    distance_km: float
    last_seen_s: int


@map_router.get("/collectors/nearby", response_model=list[NearbyCollector])
async def collectors_nearby(
    user: SellerUser,
    lat: Annotated[float | None, Query(ge=-90, le=90)] = None,
    lng: Annotated[float | None, Query(ge=-180, le=180)] = None,
    radius_km: Annotated[float, Query(gt=0, le=50)] = 10,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
):
    """Opt-in online collectors around you. Pins are snapped to ~1 km (MAP_PRESENCE_PRECISION) - not live tracking."""
    r = rt()
    box, center = await _view_bbox(user, None, None, None, None, lat, lng, radius_km)
    hits = await r.store.presence_search(center[0], center[1], radius_km, limit, r.cfg.presence_ttl_s)
    if not hits:
        return []
    ids = [uuid.UUID(h[0]) for h in hits]
    try:
        users = await run_in_threadpool(get_router().load_by_ids, User, ids)
    except ShardError:
        users = {}
    now, prec, out = time.time(), r.cfg.presence_precision, []
    for uid, la, lo, _d, ts in hits:
        u = users.get(uuid.UUID(uid))
        if u is None or not u.is_active:
            continue
        ml, mo = round(la, prec), round(lo, prec)
        out.append(NearbyCollector(id=u.id, display_name=u.display_name, avatar_url=u.avatar_url,
                                   rating_avg=round(u.rating_avg or 0.0, 2), rating_count=u.rating_count or 0, lat=ml, lng=mo,
                                   distance_km=round(haversine_m(center[0], center[1], ml, mo) / 1000, 1),
                                   last_seen_s=int(now - ts)))
    return sorted(out, key=lambda c: c.distance_km)


# =============================================================================
# 11. TRACKING SNAPSHOT (REST; the WebSocket pushes the same data live)
# =============================================================================
class TrackPoint(BaseModel):
    lat: float
    lng: float
    accuracy_m: float | None
    heading_deg: float | None
    speed_kmh: float | None
    age_s: int
    live: bool


class TrackingOut(BaseModel):
    listing_id: uuid.UUID
    status: ListingStatus
    tracking_active: bool
    reason: str | None = None
    collector: TrackPoint | None = None
    destination: dict[str, Any] | None = None
    distance_m: int | None = None
    eta_s: int | None = None
    eta_source: str | None = None
    route: list[list[float]] | None = None
    trail: list[list[float]] = Field(default_factory=list)
    geofence: str | None = None


@map_router.get("/tracking/{listing_id}", response_model=TrackingOut)
async def get_tracking(listing_id: uuid.UUID, user: CurrentUser, include_route: bool = False):
    """Seller and assigned collector only. Use on screen open / reconnect; then follow the WebSocket."""
    r = rt()
    a = await get_assignment(listing_id, fresh=True)
    if not a or user.id not in (a["seller_id"], a["collector_id"]):
        raise HTTPException(404, "Listing not found")
    base = TrackingOut(listing_id=listing_id, status=a["status"], tracking_active=False)
    if a["status"] not in (ListingStatus.ON_THE_WAY, ListingStatus.ARRIVED):
        base.reason = "Live tracking starts when the collector is on the way" if a["status"] == ListingStatus.OFFER_ACCEPTED else "Pickup is not active"
        return base
    if a["latitude"] is not None and a["longitude"] is not None:
        base.destination = {"lat": a["latitude"], "lng": a["longitude"], "address": a["address"]}
    fix = await r.store.get_fix(str(a["collector_id"]))
    if not fix:
        base.reason = "Waiting for the collector's location"
        return base
    age = time.time() - fix["rx"]
    base.tracking_active = True
    base.collector = TrackPoint(lat=round(fix["lat"], 6), lng=round(fix["lng"], 6), accuracy_m=fix["acc"], heading_deg=fix.get("hdg"),
                                speed_kmh=round(fix["spd"] * 3.6, 1) if fix.get("spd") is not None else None,
                                age_s=int(age), live=age <= r.cfg.presence_ttl_s)
    if not base.collector.live:
        base.reason = "Collector location is stale (no signal)"
    base.trail = await r.store.trail_get(str(listing_id), 100)
    gf = await r.store.kv_get(f"geo:{listing_id}")
    base.geofence = gf["s"] if gf else "far"
    if base.destination:
        info = await r.routing.route((fix["lat"], fix["lng"]), (a["latitude"], a["longitude"]), fix.get("spd"), include_route)
        base.distance_m, base.eta_s, base.eta_source = int(info.distance_m), info.duration_s, info.source
        base.route = info.polyline if include_route else None
    return base


@map_router.get("/health")
async def map_health(user: CurrentUser):
    r = rt()
    return {"store": r.store.kind, "bus": r.bus.kind, "store_ok": await r.store.ping(), "ws_connections": r.hub.size,
            "osrm": bool(r.cfg.osrm_url), "auto_arrive": r.cfg.auto_arrive}


# =============================================================================
# 12. WEBSOCKET
# =============================================================================
class _WsAuthError(Exception):
    pass


def _origin_allowed(ws: WebSocket) -> bool:
    origin = ws.headers.get("origin")
    if not origin or origin in settings.CORS_ORIGINS:
        return True
    import re

    return settings.ENV != "production" and bool(re.match(r"^https?://(localhost|127\.0\.0\.1)(:[0-9]+)?$", origin))


async def _ws_authenticate(token: str) -> tuple[User, float]:
    try:
        payload = jwt.decode(token, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM])
        if payload.get("type") != "access":
            raise ValueError("wrong token type")
        uid, exp = uuid.UUID(payload["sub"]), float(payload["exp"])
    except (jwt.PyJWTError, ValueError, KeyError, TypeError):
        raise _WsAuthError("invalid or expired token")
    user = await run_in_threadpool(_load_user_sync, uid)
    if user is None:
        raise _WsAuthError("user not found or inactive")
    return user, exp


@map_router.websocket("/ws")
async def map_ws(ws: WebSocket):
    """Protocol (JSON text frames).  client -> server:
         {"type":"auth","token":"<access jwt>"}            first frame (<=10 s); re-send before token expiry
         {"type":"subscribe","listing_id":"..."}           seller / assigned collector only
         {"type":"unsubscribe","listing_id":"..."}
         {"type":"subscribe_area","bbox":[s,w,n,e]}        new/changed listings in view (replaces previous area)
         {"type":"location", ...FixIn fields..., "ack":true}   collector ping
         {"type":"ping"}
       server -> client: ready | snapshot | collector.location | geofence | listing.created | listing.status |
                         bid.placed | fix_ack (rejections / when ack requested) | error | pong | hb | auth_ok"""
    r = _rt
    if r is None:
        await ws.close(code=1013)
        return
    if not _origin_allowed(ws):
        await ws.close(code=4403)
        return
    await ws.accept()
    conn: Connection | None = None
    sender: asyncio.Task | None = None
    try:
        try:
            first = json.loads(await asyncio.wait_for(ws.receive_text(), timeout=10))
            if not isinstance(first, dict) or first.get("type") != "auth":
                raise _WsAuthError("auth required")
            user, exp = await _ws_authenticate(str(first.get("token", "")))
        except (ValueError, asyncio.TimeoutError):
            raise _WsAuthError("auth required")
        conn = Connection(ws, user, exp, r.cfg.ws_queue_size)
        r.hub.add(conn)
        r.hub.subscribe(conn, f"user:{user.id}")
        sender = asyncio.create_task(conn.run_sender(r.cfg.ws_heartbeat_s))
        conn.offer(_msg("ready", user_id=str(user.id), role=user.role.value, server_time=time.time(),
                        heartbeat_s=r.cfg.ws_heartbeat_s, token_expires_in=int(exp - time.time())))
        while True:
            raw = await asyncio.wait_for(ws.receive_text(), timeout=r.cfg.ws_heartbeat_s * 3)
            if len(raw) > 8192:
                conn.offer(_msg("error", code="message_too_large"))
                continue
            ok, _ = r.ws_rate.allow(conn.id)
            if not ok:
                conn.offer(_msg("error", code="rate_limited"))
                continue
            try:
                msg = json.loads(raw)
            except ValueError:
                conn.offer(_msg("error", code="bad_json"))
                continue
            if isinstance(msg, dict):
                await _ws_handle(r, conn, msg)
    except _WsAuthError as exc:
        with contextlib.suppress(Exception):
            await ws.send_text(_msg("error", code="auth_failed", message=str(exc)))
            await ws.close(code=4401)
    except (WebSocketDisconnect, asyncio.TimeoutError, asyncio.CancelledError):
        pass
    except Exception:  # noqa: BLE001
        log.exception("websocket crashed")
    finally:
        if sender:
            sender.cancel()
        if conn:
            r.hub.remove(conn)
            r.ws_rate.forget(conn.id)
        with contextlib.suppress(Exception):
            await ws.close()


async def _ws_handle(r: _Runtime, conn: Connection, msg: dict) -> None:
    t = msg.get("type")
    if t == "ping":
        conn.offer(_msg("pong", t=time.time()))
        return
    if t == "auth":                                   # token refresh on a live socket
        try:
            user, exp = await _ws_authenticate(str(msg.get("token", "")))
        except _WsAuthError as exc:
            conn.offer(_msg("error", code="auth_failed", message=str(exc)))
            return
        if user.id != conn.user.id:
            conn.offer(_msg("error", code="auth_failed", message="token belongs to another user"))
            return
        conn.user, conn.exp = user, exp
        conn.offer(_msg("auth_ok", token_expires_in=int(exp - time.time())))
        return
    if time.time() >= conn.exp:
        conn.offer(_msg("error", code="token_expired", message="send a fresh {type:'auth'} frame"))
        return

    if t in ("subscribe", "unsubscribe"):
        try:
            lid = uuid.UUID(str(msg.get("listing_id")))
        except ValueError:
            conn.offer(_msg("error", code="bad_listing_id"))
            return
        topic = f"listing:{lid}"
        if t == "unsubscribe":
            r.hub.unsubscribe(conn, topic)
            return
        a = await get_assignment(lid, fresh=True)
        if not a or conn.user.id not in (a["seller_id"], a["collector_id"]):
            conn.offer(_msg("error", code="not_found", listing_id=str(lid)))
            return
        if len(conn.topics) >= r.cfg.ws_max_topics:
            conn.offer(_msg("error", code="too_many_subscriptions"))
            return
        r.hub.subscribe(conn, topic)
        snap: dict[str, Any] = {"listing_id": str(lid), "status": a["status"].value}
        if a["collector_id"] and a["status"] in (ListingStatus.ON_THE_WAY, ListingStatus.ARRIVED):
            fix = await r.store.get_fix(str(a["collector_id"]))
            if fix:
                snap["collector"] = {"lat": round(fix["lat"], 6), "lng": round(fix["lng"], 6), "accuracy_m": fix["acc"],
                                     "heading_deg": fix.get("hdg"), "age_s": int(time.time() - fix["rx"])}
                snap["trail"] = await r.store.trail_get(str(lid), 100)
            gf = await r.store.kv_get(f"geo:{lid}")
            snap["geofence"] = gf["s"] if gf else "far"
        conn.offer(_msg("snapshot", **snap))

    elif t == "subscribe_area":
        try:
            s, w, n, e = (float(x) for x in msg["bbox"])
            box = BBox(s, w, n, e)
            cells = cells_for_bbox(box) if -90 <= s < n <= 90 and -180 <= w <= 180 and -180 <= e <= 180 else None
        except (KeyError, TypeError, ValueError):
            cells = None
        if not cells:
            conn.offer(_msg("error", code="bad_bbox", message=f"bbox invalid or covers more than {MAX_AREA_CELLS} cells"))
            return
        for old in list(conn.area_topics):
            r.hub.unsubscribe(conn, old)
        conn.area_topics = {f"cell:{c}" for c in cells}
        for topic in conn.area_topics:
            r.hub.subscribe(conn, topic)
        conn.offer(_msg("area_ok", cells=len(cells)))

    elif t == "location":
        try:
            fx = FixIn.model_validate(msg.get("fix") or {k: v for k, v in msg.items() if k not in ("type", "ack")})
        except ValidationError as exc:
            conn.offer(_msg("fix_ack", accepted=False, reason="invalid_fix", detail=exc.errors()[0]["loc"][-1] if exc.errors() else None))
            return
        res = await ingest_fix(conn.user, fx)
        if not res.accepted or msg.get("ack"):
            conn.offer(_msg("fix_ack", **res.model_dump(mode="json", exclude_none=True)))
    else:
        conn.offer(_msg("error", code="unknown_type"))


# =============================================================================
# 13. SELF-TEST   python -m app.api.v1.map_system     (pure logic, no DB / network)
# =============================================================================
def _selftest() -> None:  # pragma: no cover
    import random

    # Kalman: smoothed track must be closer to the truth than the raw 20 m-noise fixes
    random.seed(1)
    prev, now, raw_err, filt_err = None, 1000.0, [], []
    for i in range(80):
        now += 3
        la = 22.5 + random.gauss(0, 20) / 111_320
        lo = 88.3 + random.gauss(0, 20) / (111_320 * math.cos(math.radians(22.5)))
        fl, fo, var = kalman_update(prev, la, lo, 20.0, now)
        prev = {"lat": fl, "lng": fo, "var": var, "rx": now}
        if i > 8:
            raw_err.append(haversine_m(22.5, 88.3, la, lo))
            filt_err.append(haversine_m(22.5, 88.3, fl, fo))
    assert sum(filt_err) < sum(raw_err), "kalman did not reduce jitter"

    cfg = MapSettings()
    base = {"lat": 22.5, "lng": 88.3, "acc": 10.0, "var": 100.0, "rx": 1000.0}
    assert not _plausible(base, 23.5, 88.3, 10.0, 1002.0, cfg), "110 km in 2 s must be rejected"
    assert _plausible(base, 22.5003, 88.3, 10.0, 1010.0, cfg)
    assert assess_fix(FixIn(lat=0, lng=0), 1.0, cfg).reason == "null_island"
    assert assess_fix(FixIn(lat=22.5, lng=88.3, source=FixSource.IP), 1.0, cfg).reason == "low_quality_source"

    b = BBox(22.4, 88.2, 22.7, 88.5)
    assert b.contains(22.5, 88.3) and not b.contains(22.8, 88.3)
    assert BBox(10, 170, 20, -170).contains(15, 179) and BBox(10, 170, 20, -170).contains(15, -179)
    cells = cells_for_bbox(b)
    assert cells and cell_key(22.5, 88.3) in cells
    assert cells_for_bbox(BBox(0, 0, 40, 40)) is None

    assert Candidate(22.5, 88.3, 30, FixSource.GPS).conf > Candidate(22.5, 88.3, 5000, FixSource.IP).conf
    assert city_centroid("Salt Lake, Kolkata") == CITY_CENTROIDS["kolkata"]
    assert estimate_eta_s(2000) > 0
    print("map_system self-test passed")


if __name__ == "__main__":
    _selftest()
