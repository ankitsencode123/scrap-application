#!/usr/bin/env python3
"""
===============================================================================
 ScrapMart - Shard Router  (single-file edition)
===============================================================================

Spreads ScrapMart's data over N independent PostgreSQL databases (your six
500 MB Supabase projects) and keeps them evenly filled.

WHAT IT GIVES YOU
  1. Capacity-aware placement   New users / listings go to the emptiest shard
                                (real `pg_database_size`, refreshed in the
                                background, plus in-flight "pending" accounting so
                                bursts don't pile onto one shard).
  2. Zero-lookup routing        Every ID is a UUID with its shard number stamped
                                into the last byte -> `shard_for(id)` is O(1),
                                no directory round-trip, no cache to invalidate.
  3. Email / Google lookup      A tiny `shard_directory` table, placed by
                                rendezvous hashing, gives globally unique emails
                                and "login by email" without knowing the shard.
  4. Scatter-gather             Parallel fan-out across shards with per-shard
                                timeouts, partial-failure tolerance and a k-way
                                merge helper for sorted/paginated feeds.
  5. Resilience                 Per-shard circuit breaker, pool_pre_ping, bounded
                                pools (Supabase caps connections!), statement
                                timeouts, DRAINING mode for retiring a shard.
  6. Ops                        `report()` for a health endpoint, CLI for status,
                                schema creation and a distribution simulator.

DATA PLACEMENT RULES  (this is the contract your models must follow)
  users                   -> shard chosen by `router.allocate()` at signup
  listings                -> `router.allocate()` (or `allocate(near=seller_id)`)
  images/events/bids/
  reviews/messages        -> SAME shard as their listing (`colocated_id(listing_id)`)
  shard_directory         -> shard chosen by rendezvous hash of the key

  Consequence: a foreign key may only point at a row on the same shard.
  `listing -> images/events/bids/reviews/messages` stay real FKs.
  `*.seller_id / collector_id / reviewer_id / reviewee_id / sender_id /
  recipient_id / actor_id -> users` become PLAIN UUID COLUMNS (no FK, no ORM
  relationship); hydrate them with `router.load_by_ids(User, ids)`.

ENVIRONMENT
  SHARD_URLS=<url0>,<url1>,...        one per line or comma separated; ORDER = SHARD ID
                                      (never reorder / remove; only append)
  SHARD_DB_PASSWORD='...'             raw password. Every "[YOUR-PASSWORD]" placeholder in
                                      SHARD_URLS is replaced by its percent-encoded form.
                                      Quote it in .env - it may contain "$".
  SHARD_DIRECTORY_SHARDS=0,1,2,3,4,5  FREEZE this in production (see "adding shards")
  SHARD_QUOTA_MB=500                  per-shard capacity used for balancing
  SHARD_DRAINING=                     shard ids that stop receiving new data
  SHARD_SSLMODE=require               default: require for remote hosts
  SHARD_POOL_SIZE=3 SHARD_MAX_OVERFLOW=4      (per shard, per worker process!)
  SHARD_STATEMENT_TIMEOUT_MS=15000    0 disables (needed for some poolers)
  SHARD_STATS_INTERVAL=30  SHARD_SOFT_LIMIT=0.80  SHARD_HARD_LIMIT=0.92
  SHARD_DIRECTORY_PEPPER=             optional secret mixed into directory key hashes
  (falls back to DATABASE_URL as a single shard for local development)

QUICK START (FastAPI)
    from shard_router import (init_router, close_router, get_router, Shards,
                              install_fastapi_handlers, merge_page, NS_EMAIL)

    @asynccontextmanager
    async def lifespan(app):
        router = init_router()                 # reads env, probes shards, starts stats thread
        if settings.AUTO_CREATE_TABLES:
            router.create_all(Base.metadata)   # every shard + directory table
        yield
        close_router()

    install_fastapi_handlers(app)              # ShardUnavailable -> 503, bad id -> 404

    # --- signup -----------------------------------------------------------
    router = get_router()
    alloc = router.allocate()                          # balanced shard + shard-tagged UUID
    if not router.directory.reserve(NS_EMAIL, email, alloc.entity_id):
        raise HTTPException(409, "An account with this email already exists")
    try:
        db = shards.for_shard(alloc.shard_id)          # `shards: Shards` dependency
        db.add(User(id=alloc.entity_id, email=email, ...)); db.commit()
    except Exception:
        router.directory.release(NS_EMAIL, email, alloc.entity_id)   # compensate
        raise

    # --- login ------------------------------------------------------------
    uid = router.directory.lookup(NS_EMAIL, email)
    user = shards.for_id(uid).get(User, uid) if uid else None

    # --- listing create / detail / accept bid (all single-shard) ----------
    alloc = router.allocate(near=seller.id)            # keep it near the seller if balanced enough
    bid_id = router.colocated_id(listing_id)
    listing = shards.for_id(listing_id).get(Listing, listing_id)

    # --- marketplace feed (scatter-gather + merge) -------------------------
    res = router.scatter(lambda db, sid: db.execute(sel.limit(offset + limit)).all())
    rows = merge_page(res.values(), key=lambda r: r[0].created_at, reverse=True,
                      limit=limit, offset=offset)      # res.partial -> some shard was down
    users = router.load_by_ids(User, {r[0].seller_id for r in rows})

LIMITS YOU MUST KNOW ABOUT
  * No cross-shard transactions. Use ordered steps + compensation (see signup).
  * Shard numbers are baked into IDs: never reorder, renumber or delete shards.
  * Adding shards later: append URLs. Keep SHARD_DIRECTORY_SHARDS frozen to the original
    set, otherwise rendezvous hashing would move directory keys to the new shard.
===============================================================================
"""

from __future__ import annotations

import argparse
import enum
import hashlib
import heapq
import hmac
import importlib
import itertools
import json
import logging
import os
import random
import re
import sys
import threading
import time
import uuid
from collections import defaultdict
from pathlib import Path
from dotenv import load_dotenv

_base_dir = Path(__file__).resolve().parents[2]
if (_base_dir / ".env").exists():
    load_dotenv(_base_dir / ".env")
if (_base_dir.parent / ".env").exists():
    load_dotenv(_base_dir.parent / ".env")
load_dotenv()
from concurrent.futures import ThreadPoolExecutor, wait
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from typing import Any, Callable, Generic, Iterable, Iterator, Mapping, Sequence, TypeVar
from urllib.parse import quote

from sqlalchemy import (
    Column,
    DateTime,
    Index,
    MetaData,
    String,
    Table,
    Uuid,
    create_engine,
    delete,
    event,
    func,
    select,
    text,
)
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session, sessionmaker

__all__ = [
    "Allocation", "CapacityBalancer", "ConfigError", "Directory", "NS_EMAIL", "NS_GOOGLE",
    "NoShardAvailable", "RouterSettings", "ScatterResult", "Shard", "ShardError", "ShardMode",
    "ShardRouter", "ShardSpec", "ShardStats", "ShardUnavailable", "ShardedSession", "Shards",
    "UnknownShard", "close_router", "get_router", "init_router", "install_fastapi_handlers",
    "make_id", "merge_page", "shard_id_of", "specs_from_env", "settings_from_env",
]

log = logging.getLogger("scrapmart.shards")

T = TypeVar("T")
MB = 1024 * 1024
MAX_SHARDS = 256  # shard id lives in one byte of the UUID
PASSWORD_PLACEHOLDER = "[YOUR-PASSWORD]"
NS_EMAIL = "email"
NS_GOOGLE = "google"


# =============================================================================
# 0. ERRORS
# =============================================================================
class ShardError(Exception):
    """Base class for every router error."""


class ConfigError(ShardError):
    """Bad or missing configuration."""


class UnknownShard(ShardError):
    """An ID points at a shard this deployment doesn't have (forged / foreign id)."""


class ShardUnavailable(ShardError):
    """The shard is down, its circuit is open, or a scatter call timed out on it."""

    def __init__(self, shard_id: int, reason: str = "is unavailable"):
        super().__init__(f"shard {shard_id} {reason}")
        self.shard_id = shard_id


class NoShardAvailable(ShardError):
    """Every shard is full, draining or unhealthy - nothing can take new data."""


# =============================================================================
# 1. CONFIGURATION
# =============================================================================
class ShardMode(str, enum.Enum):
    ACTIVE = "active"      # serves reads/writes and receives new placements
    DRAINING = "draining"  # serves existing data only (use before retiring a shard)


@dataclass(frozen=True)
class ShardSpec:
    shard_id: int
    url: str
    name: str = ""
    quota_bytes: int = 500 * MB
    mode: ShardMode = ShardMode.ACTIVE

    def __post_init__(self) -> None:
        if not 0 <= self.shard_id < MAX_SHARDS:
            raise ConfigError(f"shard_id must be 0..{MAX_SHARDS - 1}, got {self.shard_id}")
        if self.quota_bytes <= 0:
            raise ConfigError("quota_bytes must be positive")

    @property
    def label(self) -> str:
        return self.name or f"shard-{self.shard_id}"


@dataclass(frozen=True)
class RouterSettings:
    # connection pools - PER SHARD, PER WORKER PROCESS.
    pool_size: int = 10
    max_overflow: int = 20
    pool_timeout: float = 30.0
    pool_recycle: int = 1800
    connect_timeout: int = 15
    statement_timeout_ms: int = 30_000
    sslmode: str | None = None  # None -> "require" for remote hosts, driver default for localhost
    # placement
    soft_limit: float = 0.80          # above this a shard no longer wins "near" affinity
    hard_limit: float = 0.92          # above this a shard receives nothing new
    balance_band: float = 0.01        # shards within 1% of quota of the emptiest are "equal"
    affinity_slack: float = 0.05      # `near=` honoured while within 5% of quota of the emptiest
    placement_cost_bytes: int = 4096  # assumed growth per placement until real stats arrive
    # health & stats
    stats_interval: float = 60.0      # seconds; 0 disables the background thread
    breaker_threshold: int = 5        # consecutive connection faults before the circuit opens
    breaker_cooldown: float = 10.0
    scatter_timeout: float = 30.0
    # directory (email/google lookup) - FREEZE in production
    directory_shards: tuple[int, ...] | None = None
    directory_pepper: str = ""


_ENV_FIELDS: dict[str, tuple[str, Callable[[str], Any]]] = {
    "SHARD_POOL_SIZE": ("pool_size", int),
    "SHARD_MAX_OVERFLOW": ("max_overflow", int),
    "SHARD_POOL_TIMEOUT": ("pool_timeout", float),
    "SHARD_CONNECT_TIMEOUT": ("connect_timeout", int),
    "SHARD_STATEMENT_TIMEOUT_MS": ("statement_timeout_ms", int),
    "SHARD_SSLMODE": ("sslmode", str),
    "SHARD_SOFT_LIMIT": ("soft_limit", float),
    "SHARD_HARD_LIMIT": ("hard_limit", float),
    "SHARD_BALANCE_BAND": ("balance_band", float),
    "SHARD_AFFINITY_SLACK": ("affinity_slack", float),
    "SHARD_PLACEMENT_COST_BYTES": ("placement_cost_bytes", int),
    "SHARD_STATS_INTERVAL": ("stats_interval", float),
    "SHARD_BREAKER_THRESHOLD": ("breaker_threshold", int),
    "SHARD_BREAKER_COOLDOWN": ("breaker_cooldown", float),
    "SHARD_SCATTER_TIMEOUT": ("scatter_timeout", float),
    "SHARD_DIRECTORY_PEPPER": ("directory_pepper", str),
}


def _int_list(raw: str) -> list[int]:
    return [int(x) for x in re.split(r"[,\s]+", raw.strip()) if x]


def settings_from_env(env: Mapping[str, str] | None = None) -> RouterSettings:
    env = os.environ if env is None else env
    overrides: dict[str, Any] = {}
    for var, (fld, cast) in _ENV_FIELDS.items():
        raw = env.get(var)
        if raw not in (None, ""):
            try:
                overrides[fld] = cast(raw)  # type: ignore[arg-type]
            except ValueError as exc:
                raise ConfigError(f"{var}={raw!r} is not valid") from exc
    if env.get("SHARD_DIRECTORY_SHARDS", "").strip():
        overrides["directory_shards"] = tuple(sorted(set(_int_list(env["SHARD_DIRECTORY_SHARDS"]))))
    return replace(RouterSettings(), **overrides)


def _resolve_url(raw: str, password: str) -> str:
    """Substitute the (percent-encoded) password and normalise the driver name."""
    if PASSWORD_PLACEHOLDER in raw:
        if not password:
            raise ConfigError("SHARD_URLS contains [YOUR-PASSWORD] but SHARD_DB_PASSWORD is not set")
        raw = raw.replace(PASSWORD_PLACEHOLDER, quote(password, safe=""))
    url = make_url(raw)
    if url.drivername in ("postgres", "postgresql"):
        url = url.set(drivername="postgresql+psycopg2")
    return url.render_as_string(hide_password=False)


def safe_url(url: str) -> str:
    """Log-safe URL (password masked)."""
    return make_url(url).render_as_string(hide_password=True)


def specs_from_env(env: Mapping[str, str] | None = None) -> list[ShardSpec]:
    env = os.environ if env is None else env
    raw = env.get("SHARD_URLS", "").strip()
    if not raw:
        single = env.get("DATABASE_URL", "").strip()
        if not single:
            raise ConfigError("Set SHARD_URLS (comma/newline separated) - or DATABASE_URL for one dev shard")
        log.warning("SHARD_URLS not set - running with a single shard from DATABASE_URL")
        raw = single
    urls = [u.strip() for u in re.split(r"[,\n]+", raw) if u.strip()]
    password = env.get("SHARD_DB_PASSWORD", "")
    quota = int(float(env.get("SHARD_QUOTA_MB", "500")) * MB)
    draining = set(_int_list(env.get("SHARD_DRAINING", "")))
    return [
        ShardSpec(
            shard_id=i,
            url=_resolve_url(u, password),
            quota_bytes=quota,
            mode=ShardMode.DRAINING if i in draining else ShardMode.ACTIVE,
        )
        for i, u in enumerate(urls)
    ]


def default_engine_factory(spec: ShardSpec, st: RouterSettings) -> Engine:
    url = make_url(spec.url)
    connect_args: dict[str, Any] = {"connect_timeout": st.connect_timeout, "application_name": f"scrapmart-{spec.label}"}
    if st.sslmode:
        connect_args["sslmode"] = st.sslmode
    elif (url.host or "") not in ("", "localhost", "127.0.0.1", "::1"):
        connect_args["sslmode"] = "require"
    if st.statement_timeout_ms > 0:
        connect_args["options"] = f"-c statement_timeout={st.statement_timeout_ms}"
    return create_engine(
        url,
        pool_size=st.pool_size,
        max_overflow=st.max_overflow,
        pool_timeout=st.pool_timeout,
        pool_recycle=st.pool_recycle,
        pool_pre_ping=True,
        connect_args=connect_args,
    )


# =============================================================================
# 2. IDS - shard number stamped into the last byte of a UUIDv4
# =============================================================================
def make_id(shard_id: int) -> uuid.UUID:
    """A random, valid UUIDv4 (114 random bits) whose last byte is the shard id."""
    if not 0 <= shard_id < MAX_SHARDS:
        raise ValueError("shard_id out of range")
    raw = bytearray(uuid.uuid4().bytes)
    raw[15] = shard_id
    return uuid.UUID(bytes=bytes(raw))


def shard_id_of(entity_id: uuid.UUID | str) -> int:
    return _as_uuid(entity_id).bytes[15]


def _as_uuid(value: uuid.UUID | str) -> uuid.UUID:
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except ValueError as exc:
        raise UnknownShard(f"{value!r} is not a valid id") from exc


# =============================================================================
# 3. HEALTH - circuit breaker
# =============================================================================
class BreakerState(str, enum.Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreaker:
    """Opens after N consecutive connection faults; after a cooldown lets ONE probe through."""

    def __init__(self, threshold: int, cooldown: float):
        self.threshold, self.cooldown = max(1, threshold), cooldown
        self._lock = threading.Lock()
        self._state = BreakerState.CLOSED
        self._failures = 0
        self._opened_at = 0.0
        self._probe_at = 0.0

    @property
    def state(self) -> BreakerState:
        return self._state

    def allow(self) -> bool:
        with self._lock:
            if self._state is BreakerState.CLOSED:
                return True
            now = time.monotonic()
            if self._state is BreakerState.OPEN:
                if now - self._opened_at < self.cooldown:
                    return False
                self._state, self._probe_at = BreakerState.HALF_OPEN, now
                return True
            if now - self._probe_at >= self.cooldown:  # previous probe never reported back
                self._probe_at = now
                return True
            return False

    def record_success(self) -> None:
        with self._lock:
            self._failures = 0
            self._state = BreakerState.CLOSED

    def record_failure(self) -> None:
        with self._lock:
            self._failures += 1
            if self._state is BreakerState.HALF_OPEN or self._failures >= self.threshold:
                if self._state is not BreakerState.OPEN:
                    log.warning("circuit OPEN after %d fault(s)", self._failures)
                self._state, self._opened_at = BreakerState.OPEN, time.monotonic()


def _is_shard_fault(orig: BaseException | None, is_disconnect: bool = False) -> bool:
    """True for 'the database is unreachable / refusing connections', False for ordinary
    query errors (constraint violations, deadlocks, statement timeouts...)."""
    if is_disconnect:
        return True
    if orig is None:
        return False
    names = {c.__name__ for c in type(orig).__mro__}
    if "OperationalError" in names or "InterfaceError" in names:
        code = getattr(orig, "pgcode", None)
        return code is None or code.startswith(("08", "53")) or code in {"57P01", "57P02", "57P03"}
    return False


# =============================================================================
# 4. SHARD RUNTIME
# =============================================================================
@dataclass(frozen=True)
class ShardStats:
    db_bytes: int
    approx_rows: int = 0
    sampled_at: float = 0.0


class Shard:
    """One database: engine, session factory, breaker, cached stats and placement bookkeeping."""

    def __init__(self, spec: ShardSpec, engine: Engine, settings: RouterSettings):
        self.spec, self.engine, self.settings = spec, engine, settings
        self.breaker = CircuitBreaker(settings.breaker_threshold, settings.breaker_cooldown)
        self.session_factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
        self._lock = threading.Lock()
        self._stats: ShardStats | None = None
        self._pending = 0
        self._inflight = 0
        self._placed = 0
        self._attach_events()

    @property
    def id(self) -> int:
        return self.spec.shard_id

    def _attach_events(self) -> None:
        @event.listens_for(self.engine, "handle_error")
        def _on_error(ctx):  # noqa: ANN001
            if _is_shard_fault(ctx.original_exception, bool(ctx.is_disconnect)):
                self.breaker.record_failure()

        @event.listens_for(self.engine, "checkout")
        def _on_checkout(dbapi_conn, record, proxy):  # noqa: ANN001
            self.breaker.record_success()

    # ---- access -------------------------------------------------------------
    def _guard(self) -> None:
        if not self.breaker.allow():
            raise ShardUnavailable(self.id, "circuit is open (recent connection failures)")

    def open_session(self) -> Session:
        """Caller owns the session and must pass it to `close_session`."""
        self._guard()
        with self._lock:
            self._inflight += 1
        return self.session_factory()

    def close_session(self, db: Session) -> None:
        try:
            db.close()  # also rolls back anything uncommitted
        finally:
            with self._lock:
                self._inflight -= 1

    @contextmanager
    def session(self) -> Iterator[Session]:
        db = self.open_session()
        try:
            yield db
        finally:
            self.close_session(db)

    @contextmanager
    def connect(self):
        self._guard()
        with self.engine.connect() as conn:
            yield conn

    # ---- capacity bookkeeping -----------------------------------------------
    @property
    def stats(self) -> ShardStats | None:
        return self._stats

    @property
    def pending_bytes(self) -> int:
        return self._pending

    def measured_bytes(self) -> int | None:
        s = self._stats
        return s.db_bytes if s else None

    def update_stats(self, stats: ShardStats) -> None:
        with self._lock:
            self._stats = stats
            self._pending = 0  # real size now includes what we had only estimated

    def reserve(self, cost: int) -> None:
        with self._lock:
            self._pending += cost
            self._placed += 1

    def pool_pressure(self) -> float:
        cap = max(1, self.settings.pool_size + self.settings.max_overflow)
        return min(1.0, self._inflight / cap)

    def describe(self, utilization: float) -> dict[str, Any]:
        s = self._stats
        age = round(time.time() - s.sampled_at, 1) if s and s.sampled_at else None
        return {
            "shard_id": self.id,
            "name": self.spec.label,
            "host": make_url(self.spec.url).host,
            "mode": self.spec.mode.value,
            "breaker": self.breaker.state.value,
            "used_mb": round((s.db_bytes if s else 0) / MB, 1),
            "pending_kb": round(self._pending / 1024, 1),
            "quota_mb": round(self.spec.quota_bytes / MB, 1),
            "utilization_pct": round(utilization * 100, 2),
            "approx_rows": s.approx_rows if s else None,
            "stats_age_s": age,
            "inflight": self._inflight,
            "placed_since_start": self._placed,
        }


_STATS_SQL = text(
    "SELECT pg_database_size(current_database()), "
    "COALESCE((SELECT SUM(n_live_tup) FROM pg_stat_user_tables WHERE schemaname = 'public'), 0)::bigint"
)


def pg_stats_provider(shard: Shard) -> ShardStats:
    with shard.connect() as conn:
        size, rows = conn.execute(_STATS_SQL).one()
    return ShardStats(db_bytes=int(size), approx_rows=int(rows), sampled_at=time.time())


# =============================================================================
# 5. PLACEMENT - capacity-aware balancer
# =============================================================================
def compute_utilizations(shards: Sequence[Shard]) -> dict[int, float]:
    """(measured size + pending placements) / quota. Shards with no stats yet are assumed to
    be as full as the average of those that have."""
    measured = {s.id: s.measured_bytes() for s in shards}
    known = [measured[s.id] / s.spec.quota_bytes for s in shards if measured[s.id] is not None]
    fallback = sum(known) / len(known) if known else 0.0
    out: dict[int, float] = {}
    for s in shards:
        base = measured[s.id] / s.spec.quota_bytes if measured[s.id] is not None else fallback
        out[s.id] = base + s.pending_bytes / s.spec.quota_bytes
    return out


class CapacityBalancer:
    """
    "Emptiest-first with a tolerance band":
      1. Drop shards that are DRAINING, unhealthy, excluded or above the hard limit.
      2. Find the lowest utilisation (the floor).
      3. `near=` (affinity): keep the caller's shard if it is under the soft limit and within
         `affinity_slack` of the floor - locality without letting a shard run away.
      4. Otherwise every shard within `balance_band` of the floor is a candidate; pick one at
         random, weighted by free space and discounted by connection pressure.
      5. Record the placement as "pending bytes" so the very next call already sees it.
    Result: new data always flows to whatever is emptiest, ties spread evenly, and several
    worker processes converge on the same balance without any coordination.
    """

    def __init__(self, settings: RouterSettings, rng: random.Random | None = None):
        self.settings = settings
        self.rng = rng or random.Random()

    def choose(
        self,
        shards: Sequence[Shard],
        *,
        near: int | None = None,
        exclude: frozenset[int] = frozenset(),
        cost: int | None = None,
    ) -> Shard:
        st = self.settings
        utils = compute_utilizations(shards)
        eligible = [
            s for s in shards
            if s.id not in exclude
            and s.spec.mode is ShardMode.ACTIVE
            and s.breaker.state is BreakerState.CLOSED
            and utils[s.id] < st.hard_limit
        ]
        if not eligible:
            raise NoShardAvailable("every shard is full, draining or unhealthy")
        floor = min(utils[s.id] for s in eligible)

        pick: Shard | None = None
        if near is not None:
            for s in eligible:
                if s.id == near and utils[s.id] < st.soft_limit and utils[s.id] <= floor + st.affinity_slack:
                    pick = s
        if pick is None:
            band = [s for s in eligible if utils[s.id] <= floor + st.balance_band]
            weights = [max(1e-6, 1.0 - utils[s.id]) / (1.0 + s.pool_pressure()) for s in band]
            pick = self.rng.choices(band, weights=weights, k=1)[0]
        pick.reserve(st.placement_cost_bytes if cost is None else cost)
        return pick


# =============================================================================
# 6. DIRECTORY - globally unique keys (email, google sub) -> entity id
# =============================================================================
_directory_meta = MetaData()
shard_directory = Table(
    "shard_directory",
    _directory_meta,
    Column("namespace", String(16), primary_key=True),
    Column("key_hash", String(64), primary_key=True),
    Column("entity_id", Uuid, nullable=False),
    Column("created_at", DateTime(timezone=True), server_default=func.now(), nullable=False),
)
Index("ix_shard_directory_entity", shard_directory.c.entity_id)


def _rendezvous_order(key: str, shard_ids: Iterable[int]) -> list[int]:
    """Highest-random-weight hashing: stable ordering of shards for a key."""
    def score(sid: int) -> int:
        digest = hashlib.blake2b(f"{key}|{sid}".encode(), digest_size=8).digest()
        return int.from_bytes(digest, "big")
    return sorted(shard_ids, key=score, reverse=True)


class Directory:
    """
    Stores sha-256/HMAC hashes (never plain emails). The PRIMARY KEY (namespace, key_hash) makes
    `reserve()` an atomic global uniqueness check. The entity id returned by `lookup()` already
    contains the user's home shard.

    Failure model: signup does reserve() -> insert user. If the insert fails, call release().
    A crash between the two leaves an orphan entry; the same user retrying with the same
    entity id is idempotent, other users see "email taken" - reconcile with a periodic job.
    """

    def __init__(self, router: "ShardRouter"):
        self.router = router

    def _locate(self, namespace: str, key: str) -> tuple[Shard, str]:
        key = key.strip()
        if namespace == NS_EMAIL:
            key = key.lower()
        pepper = self.router.settings.directory_pepper.encode()
        digest = hmac.new(pepper, f"{namespace}:{key}".encode(), hashlib.sha256).hexdigest()
        sid = _rendezvous_order(digest, self.router.directory_shard_ids)[0]
        return self.router.get_shard(sid), digest

    def lookup(self, namespace: str, key: str) -> uuid.UUID | None:
        shard, digest = self._locate(namespace, key)
        cache_key = f"sd:{namespace}:{digest}"
        try:
            from app.core.redis_client import cache_get, cache_set
            cached = cache_get(cache_key)
            if cached:
                return uuid.UUID(str(cached))
        except Exception:
            cache_set = None

        with shard.session() as db:
            entity_id = db.scalar(
                select(shard_directory.c.entity_id).where(
                    shard_directory.c.namespace == namespace, shard_directory.c.key_hash == digest
                )
            )

        if entity_id and cache_set:
            try:
                cache_set(cache_key, str(entity_id), ttl=3600)
            except Exception:
                pass
        return entity_id

    def reserve(self, namespace: str, key: str, entity_id: uuid.UUID) -> bool:
        """True if the key is now yours (or already was); False if another entity owns it."""
        shard, digest = self._locate(namespace, key)
        cache_key = f"sd:{namespace}:{digest}"
        with shard.session() as db:
            try:
                db.execute(shard_directory.insert().values(namespace=namespace, key_hash=digest, entity_id=entity_id))
                db.commit()
                try:
                    from app.core.redis_client import cache_set
                    cache_set(cache_key, str(entity_id), ttl=3600)
                except Exception:
                    pass
                return True
            except IntegrityError:
                db.rollback()
        return self.lookup(namespace, key) == entity_id

    def release(self, namespace: str, key: str, entity_id: uuid.UUID | None = None) -> None:
        shard, digest = self._locate(namespace, key)
        cache_key = f"sd:{namespace}:{digest}"
        cond = [shard_directory.c.namespace == namespace, shard_directory.c.key_hash == digest]
        if entity_id is not None:
            cond.append(shard_directory.c.entity_id == entity_id)
        with shard.session() as db:
            db.execute(delete(shard_directory).where(*cond))
            db.commit()
        try:
            from app.core.redis_client import cache_delete
            cache_delete(cache_key)
        except Exception:
            pass


# =============================================================================
# 7. SESSIONS, SCATTER-GATHER, MERGE
# =============================================================================
@dataclass(frozen=True)
class Allocation:
    shard_id: int
    entity_id: uuid.UUID


class ShardedSession:
    """Request-scoped bag of lazily opened Sessions - one per shard the request touches.
    You commit exactly like before (`db.commit()`); anything uncommitted is rolled back on close."""

    def __init__(self, router: "ShardRouter"):
        self._router = router
        self._sessions: dict[int, Session] = {}

    def for_shard(self, shard_id: int) -> Session:
        db = self._sessions.get(shard_id)
        if db is None:
            db = self._router.get_shard(shard_id).open_session()
            self._sessions[shard_id] = db
        return db

    def for_id(self, entity_id: uuid.UUID | str) -> Session:
        return self.for_shard(self._router.shard_for(entity_id).id)

    @property
    def default(self) -> Session:
        return self.for_shard(self._router.shard_ids[0])

    def commit(self) -> None:
        """Commit every open session touched in this unit of work."""
        for db in list(self._sessions.values()):
            db.commit()

    def rollback(self) -> None:
        """Roll back every open session touched in this unit of work."""
        for db in list(self._sessions.values()):
            db.rollback()

    def get(self, model: type[T], entity_id: uuid.UUID | str) -> T | None:
        """Convenience: routes to the shard stamped in entity_id and loads the entity."""
        uid = _as_uuid(entity_id)
        return self.for_id(uid).get(model, uid)

    def add(self, entity: Any) -> None:
        """Convenience: routes to the shard stamped in entity.id (if set) and adds it."""
        entity_id = getattr(entity, "id", None)
        if entity_id is not None:
            self.for_id(entity_id).add(entity)
        else:
            self.default.add(entity)

    def delete(self, entity: Any) -> None:
        """Convenience: routes to the shard stamped in entity.id (if set) and deletes it."""
        entity_id = getattr(entity, "id", None)
        if entity_id is not None:
            self.for_id(entity_id).delete(entity)
        else:
            self.default.delete(entity)

    def close(self) -> None:
        sessions, self._sessions = self._sessions, {}
        for sid, db in sessions.items():
            try:
                self._router.get_shard(sid).close_session(db)
            except Exception:  # noqa: BLE001 - never mask the real error
                log.exception("error closing session for shard %s", sid)


@dataclass
class ScatterResult(Generic[T]):
    ok: dict[int, T] = field(default_factory=dict)
    failed: dict[int, BaseException] = field(default_factory=dict)

    @property
    def partial(self) -> bool:
        return bool(self.failed)

    def values(self) -> list[T]:
        """Successful per-shard results ordered by shard id."""
        return [self.ok[k] for k in sorted(self.ok)]

    def raise_if_failed(self) -> None:
        if self.failed:
            sid, exc = next(iter(sorted(self.failed.items())))
            raise ShardUnavailable(sid, f"failed during scatter: {exc!r}") from exc


def merge_page(
    per_shard: Iterable[Sequence[T]],
    key: Callable[[T], Any],
    *,
    limit: int,
    offset: int = 0,
    reverse: bool = False,
) -> list[T]:
    """k-way merge of per-shard lists that are ALREADY sorted by `key` (descending if
    reverse=True) and were each fetched with LIMIT (offset + limit). Returns the global page.
    For nullable sort keys use e.g. key=lambda r: (r.dist is None, r.dist)."""
    merged = heapq.merge(*per_shard, key=key, reverse=reverse)
    return list(itertools.islice(merged, offset, offset + limit))


# =============================================================================
# 8. THE ROUTER
# =============================================================================
class ShardRouter:
    def __init__(
        self,
        specs: Iterable[ShardSpec],
        settings: RouterSettings | None = None,
        *,
        engine_factory: Callable[[ShardSpec, RouterSettings], Engine] | None = None,
        stats_provider: Callable[[Shard], ShardStats] | None = None,
        balancer: CapacityBalancer | None = None,
    ):
        specs = list(specs)
        if not specs:
            raise ConfigError("at least one shard is required")
        self.settings = settings or RouterSettings()
        if len({s.shard_id for s in specs}) != len(specs):
            raise ConfigError("duplicate shard_id")
        self._check_distinct_databases(specs)

        factory = engine_factory or default_engine_factory
        self._by_id: dict[int, Shard] = {
            s.shard_id: Shard(s, factory(s, self.settings), self.settings)
            for s in sorted(specs, key=lambda s: s.shard_id)
        }
        self._shards = list(self._by_id.values())
        self._stats_provider = stats_provider or pg_stats_provider
        self.balancer = balancer or CapacityBalancer(self.settings)

        dir_ids = tuple(self.settings.directory_shards or self._by_id)
        unknown = set(dir_ids) - set(self._by_id)
        if unknown:
            raise ConfigError(f"directory_shards references unknown shard(s) {sorted(unknown)}")
        self.directory_shard_ids: tuple[int, ...] = tuple(sorted(dir_ids))
        self.directory = Directory(self)

        self._pool = ThreadPoolExecutor(max_workers=max(16, len(self._shards) * 8), thread_name_prefix="shard-io")
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._started = False

    @staticmethod
    def _check_distinct_databases(specs: Sequence[ShardSpec]) -> None:
        seen: dict[tuple, int] = {}
        for s in specs:
            u = make_url(s.url)
            ident = (u.host, u.port, u.database, u.username)
            if ident in seen:
                raise ConfigError(f"shards {seen[ident]} and {s.shard_id} point at the same database")
            seen[ident] = s.shard_id

    # ---- lifecycle ----------------------------------------------------------
    def start(self, *, background: bool = True) -> "ShardRouter":
        if self._started:
            return self
        self._started = True
        self.refresh_stats()
        if background and self.settings.stats_interval > 0:
            self._thread = threading.Thread(target=self._refresh_loop, name="shard-stats", daemon=True)
            self._thread.start()
        for s in self._shards:
            log.info("shard %s -> %s [%s]", s.id, safe_url(s.spec.url), s.spec.mode.value)
        return self

    def close(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)
        self._pool.shutdown(wait=False, cancel_futures=True)
        for s in self._shards:
            s.engine.dispose()

    def __enter__(self) -> "ShardRouter":
        return self.start()

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _refresh_loop(self) -> None:
        while not self._stop.wait(self.settings.stats_interval * random.uniform(0.8, 1.2)):  # jitter
            try:
                self.refresh_stats()
            except Exception:  # noqa: BLE001
                log.exception("stats refresh failed")

    # ---- topology -----------------------------------------------------------
    @property
    def shards(self) -> list[Shard]:
        return list(self._shards)

    @property
    def shard_ids(self) -> list[int]:
        return sorted(self._by_id)

    def get_shard(self, shard_id: int) -> Shard:
        try:
            return self._by_id[shard_id]
        except KeyError:
            raise UnknownShard(f"no shard {shard_id}") from None

    def shard_for(self, entity_id: uuid.UUID | str) -> Shard:
        return self.get_shard(shard_id_of(entity_id))

    # ---- placement ----------------------------------------------------------
    def allocate(
        self,
        *,
        near: uuid.UUID | str | None = None,
        exclude: Iterable[int] = (),
        cost: int | None = None,
    ) -> Allocation:
        """Pick a shard for a NEW root entity and mint an id that points at it.
        `near=<entity id>` asks for the same shard as that entity when balance allows."""
        near_sid = None
        if near is not None:
            sid = shard_id_of(near)
            near_sid = sid if sid in self._by_id else None
        shard = self.balancer.choose(self._shards, near=near_sid, exclude=frozenset(exclude), cost=cost)
        return Allocation(shard.id, make_id(shard.id))

    def colocated_id(self, parent_id: uuid.UUID | str) -> uuid.UUID:
        """New id on the same shard as `parent_id` (bids, images, events, reviews, messages)."""
        return make_id(self.shard_for(parent_id).id)

    # ---- sessions -----------------------------------------------------------
    @contextmanager
    def session(self, shard_id: int) -> Iterator[Session]:
        with self.get_shard(shard_id).session() as db:
            yield db

    @contextmanager
    def session_for(self, entity_id: uuid.UUID | str) -> Iterator[Session]:
        with self.shard_for(entity_id).session() as db:
            yield db

    @contextmanager
    def unit_of_work(self) -> Iterator[ShardedSession]:
        uow = ShardedSession(self)
        try:
            yield uow
        finally:
            uow.close()

    # ---- scatter-gather -----------------------------------------------------
    def _run_one(self, shard: Shard, fn: Callable[[Session, int], T]) -> T:
        with shard.session() as db:
            return fn(db, shard.id)

    def scatter(
        self,
        fn: Callable[[Session, int], T],
        shard_ids: Iterable[int] | None = None,
        *,
        timeout: float | None = None,
    ) -> ScatterResult[T]:
        """Run `fn(db, shard_id)` on shards in parallel (each in its own session). Never raises
        for a single sick shard - inspect `.failed` / `.partial` (or `.raise_if_failed()`)."""
        targets = [self.get_shard(i) for i in (self.shard_ids if shard_ids is None else shard_ids)]
        futures = {self._pool.submit(self._run_one, s, fn): s for s in targets}
        done, not_done = wait(futures, timeout=timeout or self.settings.scatter_timeout)
        result: ScatterResult[T] = ScatterResult()
        for fut in done:
            sid = futures[fut].id
            try:
                result.ok[sid] = fut.result()
            except Exception as exc:  # noqa: BLE001
                result.failed[sid] = exc
                log.warning("scatter: shard %s failed: %r", sid, exc)
        for fut in not_done:
            fut.cancel()
            sid = futures[fut].id
            result.failed[sid] = TimeoutError(f"shard {sid} did not answer in time")
            log.warning("scatter: shard %s timed out", sid)
        return result

    def load_by_ids(self, model: Any, ids: Iterable[uuid.UUID | str | None], *, id_attr: str = "id", chunk: int = 500) -> dict[uuid.UUID, Any]:
        """Batched cross-shard hydration: replaces ORM relationships that used to cross shards,
        e.g. `users = router.load_by_ids(User, {l.collector_id for l in listings})`."""
        by_shard: dict[int, list[uuid.UUID]] = defaultdict(list)
        for raw in {i for i in ids if i is not None}:
            uid = _as_uuid(raw)
            if shard_id_of(uid) in self._by_id:
                by_shard[shard_id_of(uid)].append(uid)
        if not by_shard:
            return {}
        col = getattr(model, id_attr)

        def fetch(db: Session, sid: int) -> list[Any]:
            wanted = by_shard[sid]
            rows: list[Any] = []
            for i in range(0, len(wanted), chunk):
                rows.extend(db.scalars(select(model).where(col.in_(wanted[i:i + chunk]))))
            return rows

        res = self.scatter(fetch, by_shard.keys())
        if res.failed:
            failed_sids = [sid for sid, exc in res.failed.items() if isinstance(exc, (TimeoutError, DBAPIError))]
            if failed_sids:
                log.info("retrying load_by_ids on shard(s) %s", failed_sids)
                retry_res = self.scatter(fetch, failed_sids)
                for sid, r in retry_res.ok.items():
                    res.ok[sid] = r
                    res.failed.pop(sid, None)
        res.raise_if_failed()  # a missing user would silently blank a name - fail loudly instead
        return {getattr(r, id_attr): r for rows in res.values() for r in rows}

    # ---- ops ----------------------------------------------------------------
    def _refresh_one(self, shard: Shard) -> bool:
        try:
            shard.update_stats(self._stats_provider(shard))
            return True
        except ShardUnavailable:
            return False
        except Exception as exc:  # noqa: BLE001
            log.warning("stats for shard %s failed: %s", shard.id, exc)
            return False

    def refresh_stats(self) -> dict[int, bool]:
        futures = {self._pool.submit(self._refresh_one, s): s for s in self._shards}
        done, not_done = wait(futures, timeout=self.settings.connect_timeout + 5)
        out = {futures[f].id: f.result() for f in done}
        out.update({futures[f].id: False for f in not_done})
        return dict(sorted(out.items()))

    def create_all(self, metadata: MetaData | None = None, *, strict: bool = False) -> list[int]:
        """Create tables on every shard (and the directory table on directory shards).
        Returns the ids of shards that failed (raises instead when strict=True)."""
        failed: list[int] = []
        for s in self._shards:
            try:
                if metadata is not None:
                    metadata.create_all(s.engine)
                if s.id in self.directory_shard_ids:
                    _directory_meta.create_all(s.engine)
            except Exception as exc:  # noqa: BLE001
                failed.append(s.id)
                log.error("create_all failed on shard %s: %s", s.id, exc)
        if failed and strict:
            raise ShardError(f"create_all failed on shard(s) {failed}")
        return failed

    def report(self) -> dict[str, Any]:
        """JSON-able snapshot for a health/admin endpoint (protect it with admin auth!)."""
        utils = compute_utilizations(self._shards)
        shards = [s.describe(utils[s.id]) for s in self._shards]
        values = list(utils.values())
        warnings: list[str] = []
        for s in self._shards:
            u = utils[s.id]
            if s.breaker.state is not BreakerState.CLOSED:
                warnings.append(f"shard {s.id}: circuit {s.breaker.state.value}")
            if u >= self.settings.hard_limit:
                warnings.append(f"shard {s.id}: FULL ({u:.0%}) - no new data")
            elif u >= self.settings.soft_limit:
                warnings.append(f"shard {s.id}: above soft limit ({u:.0%}) - plan capacity")
            if s.stats is None:
                warnings.append(f"shard {s.id}: no stats yet")
        return {
            "shards": shards,
            "spread_pct": round((max(values) - min(values)) * 100, 2),
            "total_used_mb": round(sum(x["used_mb"] for x in shards), 1),
            "total_quota_mb": round(sum(s.spec.quota_bytes for s in self._shards) / MB, 1),
            "healthy": sum(1 for s in self._shards if s.breaker.state is BreakerState.CLOSED),
            "warnings": warnings,
        }


# =============================================================================
# 9. PROCESS-WIDE SINGLETON + FASTAPI GLUE
# =============================================================================
_router: ShardRouter | None = None
_router_lock = threading.Lock()


def init_router(
    specs: Iterable[ShardSpec] | None = None, settings: RouterSettings | None = None, **kwargs: Any
) -> ShardRouter:
    """Create + start the global router (idempotent). Reads the environment unless given specs."""
    global _router
    with _router_lock:
        if _router is None:
            _router = ShardRouter(
                specs if specs is not None else specs_from_env(),
                settings or settings_from_env(),
                **kwargs,
            ).start()
        return _router


def get_router() -> ShardRouter:
    return _router or init_router()


def close_router() -> None:
    global _router
    with _router_lock:
        if _router is not None:
            _router.close()
            _router = None


def get_uow() -> Iterator[ShardedSession]:
    with get_router().unit_of_work() as uow:
        yield uow


try:  # FastAPI is optional - the router works without it
    from typing import Annotated

    from fastapi import Depends, FastAPI
    from fastapi.responses import JSONResponse

    Shards = Annotated[ShardedSession, Depends(get_uow)]

    def install_fastapi_handlers(app: FastAPI) -> None:
        """Map router errors to proper HTTP responses."""

        @app.exception_handler(ShardUnavailable)
        async def _unavailable(_, exc: ShardUnavailable):  # noqa: ANN001
            log.warning("503: %s", exc)
            return JSONResponse({"detail": "Service temporarily unavailable, please retry"}, 503, headers={"Retry-After": "5"})

        @app.exception_handler(NoShardAvailable)
        async def _full(_, exc: NoShardAvailable):  # noqa: ANN001
            log.error("503: %s", exc)
            return JSONResponse({"detail": "Service is at capacity, please try again later"}, 503, headers={"Retry-After": "60"})

        @app.exception_handler(UnknownShard)
        async def _unknown(_, exc: UnknownShard):  # noqa: ANN001
            return JSONResponse({"detail": "Not found"}, 404)

        from sqlalchemy.exc import TimeoutError as SQLATimeoutError

        @app.exception_handler(TimeoutError)
        async def _timeout(_, exc: TimeoutError):  # noqa: ANN001
            log.warning("503 timeout: %s", exc)
            return JSONResponse({"detail": "Database request timed out, please retry"}, 503, headers={"Retry-After": "3"})

        @app.exception_handler(SQLATimeoutError)
        async def _sqla_timeout(_, exc: SQLATimeoutError):  # noqa: ANN001
            log.warning("503 pool timeout: %s", exc)
            return JSONResponse({"detail": "Database connection pool busy, please retry"}, 503, headers={"Retry-After": "3"})

        @app.exception_handler(DBAPIError)
        async def _db(_, exc: DBAPIError):  # noqa: ANN001
            if _is_shard_fault(exc.orig, bool(exc.connection_invalidated)):
                return JSONResponse({"detail": "Service temporarily unavailable, please retry"}, 503, headers={"Retry-After": "5"})
            log.exception("unhandled database error", exc_info=exc)
            return JSONResponse({"detail": "Internal server error"}, 500)

except ImportError:  # pragma: no cover
    Shards = None  # type: ignore[assignment]

    def install_fastapi_handlers(app: Any) -> None:  # type: ignore[misc]
        raise RuntimeError("fastapi is not installed")


# =============================================================================
# 10. CLI  (python shard_router.py status | init-schema app.db.models:Base | simulate)
# =============================================================================
def simulate(
    users: int = 40_000, shards: int = 6, quota_mb: int = 500, bytes_per_entity: int = 12_000,
    initial_mb: Sequence[float] | None = None, seed: int = 7,
) -> dict[str, Any]:
    """Offline placement test (no database): feeds `users` allocations through the real balancer
    against a fake size model that starts SKEWED, refreshing stats every 500 placements like a
    busy deployment would. Shows that the emptiest shards fill first and the spread collapses."""
    rng = random.Random(seed + 1)  # size noise; must NOT share a seed with the balancer's RNG
    initial = list(initial_mb or [40, 200, 90, 60, 310, 120][:shards])
    initial += [0.0] * (shards - len(initial))
    sizes = {i: int(initial[i] * MB) for i in range(shards)}
    specs = [ShardSpec(i, f"postgresql+psycopg2://sim:sim@sim-{i}/db", quota_bytes=quota_mb * MB) for i in range(shards)]
    st = replace(RouterSettings(), stats_interval=0)
    router = ShardRouter(
        specs, st,
        engine_factory=lambda spec, s: create_engine("sqlite://"),
        stats_provider=lambda sh: ShardStats(db_bytes=sizes[sh.id], sampled_at=time.time()),
        balancer=CapacityBalancer(st, random.Random(seed)),
    )
    router.refresh_stats()
    before = router.report()
    counts: dict[int, int] = defaultdict(int)
    for n in range(users):
        a = router.allocate(cost=bytes_per_entity)
        counts[a.shard_id] += 1
        sizes[a.shard_id] += int(bytes_per_entity * rng.uniform(0.5, 1.5))
        if n % 500 == 0:
            router.refresh_stats()
    router.refresh_stats()
    after = router.report()
    router.close()
    return {
        "placements_per_shard": dict(sorted(counts.items())),
        "before_pct": [s["utilization_pct"] for s in before["shards"]],
        "after_pct": [s["utilization_pct"] for s in after["shards"]],
        "spread_before_pct": before["spread_pct"],
        "spread_after_pct": after["spread_pct"],
    }


def _cli(argv: Sequence[str]) -> int:
    p = argparse.ArgumentParser(prog="shard_router", description="ScrapMart shard router tools")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status", help="probe every shard and print a JSON report")
    ini = sub.add_parser("init-schema", help="create tables on all shards, e.g. app.db.models:Base")
    ini.add_argument("target", help="module:Attribute of your declarative Base")
    sim = sub.add_parser("simulate", help="offline distribution test (no database needed)")
    sim.add_argument("--users", type=int, default=40_000)
    sim.add_argument("--bytes-per-entity", type=int, default=12_000)
    args = p.parse_args(list(argv))
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    if args.cmd == "simulate":
        print(json.dumps(simulate(args.users, bytes_per_entity=args.bytes_per_entity), indent=2))
        return 0
    router = init_router()
    try:
        if args.cmd == "init-schema":
            module, _, attr = args.target.partition(":")
            base = getattr(importlib.import_module(module), attr or "Base")
            failed = router.create_all(base.metadata)
            print("created on all shards" if not failed else f"FAILED on shards {failed}")
            return 1 if failed else 0
        print(json.dumps(router.report(), indent=2))
        return 0
    finally:
        close_router()


if __name__ == "__main__":
    sys.exit(_cli(sys.argv[1:]))
