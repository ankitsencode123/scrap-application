#!/usr/bin/env python3
"""
===============================================================================
 ScrapMart - Cursor (keyset) pagination for the sharded database layer
===============================================================================

Drop-in location:  app/core/cursor_pagination.py        (no other file changes it)

WHY THIS EXISTS
  The current feeds use LIMIT/OFFSET.  With S shards, page N costs every shard a
  scan of (offset + limit) rows, plus a k-way merge over S * (offset + limit)
  rows, plus a COUNT(*) per shard per request.  Deep pages get slower linearly,
  and rows shift between pages whenever something is inserted while the user
  scrolls (duplicates / skipped items).

WHAT THIS MODULE DOES
  1. Keyset seek          Each shard answers "give me the next `limit + 1` rows AFTER
                          this (sort values..., id) tuple" with an index range scan.
                          Cost per shard is O(limit) no matter how deep the user is.
  2. Stable ordering      Every sort ends with the unique `id` column, so ordering is
                          total: no duplicates, no gaps, even with thousands of ties.
  3. Opaque signed cursor base64url(JSON) + truncated HMAC-SHA256.  Tampering, a cursor
                          from another sort/filter/user, or an expired cursor -> HTTP 400.
  4. No COUNT(*) by default  `has_more` comes from the `limit + 1` probe row.  A total is
                          computed only on request, on the first page only, and then
                          carried inside the cursor (one count per scroll session).
  5. Shard aware          Works with `ShardRouter.scatter`; merges per-shard pages with a
                          heap (`heapq.merge`), supports DESC/ASC and NULLS LAST columns,
                          and fails loudly (default) or flags `partial=true` when a shard
                          is down - never silently skips rows.
  6. Index friendly       When all sort columns are NOT NULL and share a direction the
                          seek is ONE row-value comparison  (a, b) < (x, y)  which
                          PostgreSQL turns into a single index range scan.  Otherwise it
                          falls back to the equivalent OR-expansion (still correct).

RULES FOR SORT SPECS (read once, they are enforced where possible)
  * The LAST column of a SortSpec must be unique and NOT NULL (use the primary key).
  * Use types whose DB ordering equals Python ordering: int, float, Decimal, datetime,
    UUID.  Avoid text columns (collation differences break the cross-shard merge).
  * NULLs always sort LAST (matches `.nulls_last()` in the existing nearest-sort).
  * For best speed keep every column in one direction and index it that way.

RECOMMENDED INDEXES (PostgreSQL, run per shard; CREATE INDEX CONCURRENTLY in prod)
  Each shard has a small quota, so prefer PARTIAL indexes for the open feed:
    -- marketplace feed, newest first (replaces most of ix_listings_feed's job)
    CREATE INDEX CONCURRENTLY ix_listings_open_newest
        ON listings (created_at DESC, id DESC) WHERE status = 'posted';
    -- price / weight sorts on the open feed
    CREATE INDEX CONCURRENTLY ix_listings_open_price
        ON listings (expected_price, id) WHERE status = 'posted';
    CREATE INDEX CONCURRENTLY ix_listings_open_weight
        ON listings (weight_kg DESC, id DESC) WHERE status = 'posted';
    -- "my scrap" / "my pickups"
    CREATE INDEX CONCURRENTLY ix_listings_seller_newest
        ON listings (seller_id, created_at DESC, id DESC);
    CREATE INDEX CONCURRENTLY ix_listings_collector_newest
        ON listings (collector_id, created_at DESC, id DESC);
    -- bids / reviews / messages
    CREATE INDEX CONCURRENTLY ix_bids_collector_newest ON bids (collector_id, created_at DESC, id DESC);
    CREATE INDEX CONCURRENTLY ix_reviews_reviewee_newest ON reviews (reviewee_id, created_at DESC, id DESC);
    CREATE INDEX CONCURRENTLY ix_messages_listing_newest ON messages (listing_id, created_at DESC, id DESC);
  (Adjust 'posted' to the exact label your `pg_enum(ListingStatus)` stores.)
  Distance sort orders by a computed expression, so it cannot be index-seeked; it is still
  correct and stays bounded by `max_distance_km`, filters and `limit`.  If that feed grows
  large, add a bounding-box prefilter or PostGIS/earthdistance + GiST.

-------------------------------------------------------------------------------
 INTEGRATION COOKBOOK
-------------------------------------------------------------------------------
 # app/api/listings.py ------------------------------------------------------
 from app.core.cursor_pagination import (
     CursorPage, CursorQuery, LimitQuery, SortColumn, SortSpec,
     DATETIME, DECIMAL, UUID_, FLOAT, make_fingerprint, paginate_sharded,
 )

 def _sorts(dist):                                   # dist = 2nd column of listing_select()
     ident = lambda desc: SortColumn("id", Listing.id, UUID_, lambda r: r[0].id, descending=desc)
     created = SortColumn("created_at", Listing.created_at, DATETIME, lambda r: r[0].created_at, descending=True)
     price = lambda desc: SortColumn("expected_price", Listing.expected_price, DECIMAL,
                                     lambda r: r[0].expected_price, descending=desc)
     weight = SortColumn("weight_kg", Listing.weight_kg, DECIMAL, lambda r: r[0].weight_kg, descending=True)
     near = SortColumn("distance", dist, FLOAT, lambda r: r[1], nullable=True)
     return {
         "newest":      SortSpec("newest",      [created, ident(True)]),
         "price_high":  SortSpec("price_high",  [price(True), ident(True)]),
         "price_low":   SortSpec("price_low",   [price(False), ident(False)]),
         "weight_high": SortSpec("weight_high", [weight, ident(True)]),
         "nearest":     SortSpec("nearest",     [near, ident(False)]),
     }

 @listings_router.get("", response_model=CursorPage[ListingOut])
 def list_listings(db: DB, user: CurrentUser,
                   ...same filter params as today..., sort: Literal[...] = "newest",
                   limit: LimitQuery = 20, cursor: CursorQuery = None,
                   include_total: bool = False):
     lat, lng = resolve_coords(user, lat, lng)
     has_coords = lat is not None and lng is not None
     sel, dist = listing_select(lat, lng)
     filters = [...]                                 # EXACTLY the filters you build today
     effective = "nearest" if (sort == "nearest" and has_coords) else ("newest" if sort == "nearest" else sort)
     spec = _sorts(dist)[effective]
     fp = make_fingerprint(tab=tab, category=category, status=status_filter, q=q, min=min_price,
                           max=max_price, lat=lat, lng=lng, dist=max_distance_km, viewer=user.id)
     result = paginate_sharded(
         get_router(), base=sel, spec=spec, filters=filters, limit=limit, cursor=cursor,
         fingerprint=fp, with_total=include_total,
         count_stmt=select(func.count(Listing.id)),
     )
     return CursorPage[ListingOut](items=rows_to_out(db, user, result.rows), **result.meta(limit))

 # reviews:  base = select(Review); rows are Row objects, so getters read r[0]:
 #           SortSpec("newest", [SortColumn("created_at", Review.created_at, DATETIME,
 #                                          lambda r: r[0].created_at, descending=True),
 #                               SortColumn("id", Review.id, UUID_, lambda r: r[0].id, descending=True)])
 #           filters = [Review.reviewee_id == user_id]; count_stmt = select(func.count(Review.id))
 # bids:     same shape on Bid (filters = [Bid.collector_id == me, Bid.status != WITHDRAWN]).
 # messages: same shape on Message; return the page reversed for chat display
 #           (`reversed(result.rows)`); the client passes `next_cursor` to load OLDER messages.
 # install in app startup:   install_cursor_handlers(app)
 # environment:              CURSOR_SECRET=<long random string>  (MUST be identical on every worker)
===============================================================================
"""

import base64
import binascii
import hashlib
import hmac
import heapq
import itertools
import json
import logging
import math
import os
import secrets
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any, Callable, Generic, Sequence, TypeVar

from pydantic import BaseModel
from sqlalchemy import and_, false, literal, or_, select, tuple_
from sqlalchemy.sql.elements import ColumnElement
from sqlalchemy.sql.selectable import Select
from sqlalchemy.types import NullType

__all__ = [
    "CursorError", "InvalidCursor", "Codec", "INT", "FLOAT", "DECIMAL", "DATETIME", "UUID_",
    "SortColumn", "SortSpec", "CursorCodec", "configure_cursors", "get_cursor_codec",
    "make_fingerprint", "PageResult", "CursorPage", "paginate_sharded", "CursorQuery",
    "LimitQuery", "DEFAULT_LIMIT", "MAX_LIMIT", "install_cursor_handlers",
]

log = logging.getLogger("scrapmart.cursor")

T = TypeVar("T")
DEFAULT_LIMIT = 20
MAX_LIMIT = 100
MAX_TOKEN_LEN = 1536  # reject absurd tokens before doing any work
_CURSOR_VERSION = 1


# =============================================================================
# 0. ERRORS
# =============================================================================
class CursorError(ValueError):
    """Programming / configuration error around cursors."""


class InvalidCursor(CursorError):
    """The client sent a cursor that is malformed, forged, expired or for another query."""


# =============================================================================
# 1. VALUE CODECS - how each sort value travels inside the cursor
# =============================================================================
@dataclass(frozen=True)
class Codec:
    name: str
    encode: Callable[[Any], Any]
    decode: Callable[[Any], Any]


def _dec_int(v: Any) -> int:
    if isinstance(v, bool) or not isinstance(v, int):
        raise TypeError("expected int")
    return v


def _enc_float(v: Any) -> float:
    f = float(v)
    if not math.isfinite(f):
        raise CursorError("cannot paginate on NaN/inf sort values")
    return f  # json uses repr() -> exact round trip


def _dec_float(v: Any) -> float:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise TypeError("expected number")
    f = float(v)
    if not math.isfinite(f):
        raise ValueError("non-finite")
    return f


def _dec_decimal(v: Any) -> Decimal:
    if not isinstance(v, str):
        raise TypeError("expected str")
    d = Decimal(v)
    if not d.is_finite():
        raise ValueError("non-finite")
    return d


def _dec_datetime(v: Any) -> datetime:
    if not isinstance(v, str):
        raise TypeError("expected str")
    return datetime.fromisoformat(v)


def _dec_uuid(v: Any) -> uuid.UUID:
    if not isinstance(v, str):
        raise TypeError("expected str")
    return uuid.UUID(v)


INT = Codec("int", int, _dec_int)
FLOAT = Codec("float", _enc_float, _dec_float)
DECIMAL = Codec("decimal", lambda v: str(Decimal(v)), _dec_decimal)  # str: no float rounding
DATETIME = Codec("datetime", lambda v: v.isoformat(), _dec_datetime)  # keeps microseconds + tz
UUID_ = Codec("uuid", lambda v: str(v), _dec_uuid)


# =============================================================================
# 2. SORT SPECIFICATION
# =============================================================================
class _Rev:
    """Wrapper that inverts ordering so a DESC column can be merged in ascending key order."""

    __slots__ = ("v",)

    def __init__(self, v: Any):
        self.v = v

    def __lt__(self, other: "_Rev") -> bool:
        return other.v < self.v

    def __eq__(self, other: object) -> bool:
        return isinstance(other, _Rev) and self.v == other.v

    __hash__ = None  # type: ignore[assignment]


@dataclass(frozen=True)
class SortColumn:
    """One ORDER BY term.

    name        stable identifier (documentation / debugging)
    expr        SQLAlchemy column or expression used in ORDER BY and in the seek predicate
    codec       how the value is serialised into the cursor
    getter      row -> python value of this column (rows come straight from `session.execute`)
    descending  sort direction
    nullable    True if the expression can be NULL (NULLs sort LAST in both directions)
    """

    name: str
    expr: ColumnElement
    codec: Codec
    getter: Callable[[Any], Any]
    descending: bool = False
    nullable: bool = False

    def order_term(self) -> ColumnElement:
        term = self.expr.desc() if self.descending else self.expr.asc()
        return term.nulls_last() if self.nullable else term

    def literal(self, value: Any) -> ColumnElement:
        t = getattr(self.expr, "type", None)
        if t is None or isinstance(t, NullType):
            return literal(value)
        return literal(value, t)

    def merge_part(self, value: Any) -> tuple:
        if value is None:
            return (1, None)  # NULLs last; two NULLs compare equal -> tie broken by next column
        return (0, _Rev(value) if self.descending else value)


class SortSpec:
    """A total ordering over rows: (col_1, ..., col_n) where col_n is the unique id."""

    def __init__(self, name: str, columns: Sequence[SortColumn]):
        if not name:
            raise CursorError("SortSpec needs a name")
        if not columns:
            raise CursorError("SortSpec needs at least one column")
        if columns[-1].nullable:
            raise CursorError("the last (tie-breaker) column must be unique and NOT NULL")
        self.name = name
        self.columns: tuple[SortColumn, ...] = tuple(columns)
        # one row-value comparison is possible only if the order is uniform and NULL-free
        self._uniform = (
            not any(c.nullable for c in self.columns)
            and len({c.descending for c in self.columns}) == 1
        )

    # ---- SQL side -----------------------------------------------------------
    def order_by(self) -> list[ColumnElement]:
        return [c.order_term() for c in self.columns]

    def seek(self, values: Sequence[Any]) -> ColumnElement:
        """Predicate selecting exactly the rows that sort STRICTLY AFTER `values`."""
        cols = self.columns
        if len(values) != len(cols):
            raise InvalidCursor("cursor does not match the sort")

        if self._uniform:
            if len(cols) == 1:
                c = cols[0]
                lit = c.literal(values[0])
                return c.expr < lit if c.descending else c.expr > lit
            lhs = tuple_(*[c.expr for c in cols])
            rhs = tuple_(*[c.literal(v) for c, v in zip(cols, values)])
            return lhs < rhs if cols[0].descending else lhs > rhs

        # General form:  OR_i ( c1=v1 AND .. AND c(i-1)=v(i-1) AND c_i  is-after  v_i )
        branches: list[ColumnElement] = []
        equal_prefix: list[ColumnElement] = []
        for c, v in zip(cols, values):
            if v is None:
                after: ColumnElement = false()  # nothing sorts after NULL except ties (next column)
                equal = c.expr.is_(None)
            else:
                lit = c.literal(v)
                after = c.expr < lit if c.descending else c.expr > lit
                if c.nullable:
                    after = or_(after, c.expr.is_(None))
                equal = c.expr == lit
            branches.append(and_(*equal_prefix, after) if equal_prefix else after)
            equal_prefix.append(equal)
        return or_(*branches)

    # ---- python side --------------------------------------------------------
    def values(self, row: Any) -> tuple:
        return tuple(c.getter(row) for c in self.columns)

    def merge_key(self, row: Any) -> tuple:
        """Key that makes `heapq.merge` (ascending) reproduce the SQL ORDER BY exactly."""
        return tuple(c.merge_part(c.getter(row)) for c in self.columns)


# =============================================================================
# 3. SIGNED OPAQUE CURSOR
# =============================================================================
def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64d(text: str) -> bytes:
    try:
        return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))
    except (binascii.Error, ValueError) as exc:
        raise InvalidCursor("malformed cursor") from exc


class CursorCodec:
    """`<b64url(json)>.<b64url(hmac16)>` - compact, URL safe, tamper evident (NOT encrypted)."""

    MAC_BYTES = 16

    def __init__(self, secret: str | bytes, *, max_age: int | None = None):
        raw = secret.encode() if isinstance(secret, str) else secret
        if len(raw) < 16:
            raise CursorError("cursor secret must be at least 16 bytes")
        self._key = hashlib.blake2b(raw, digest_size=32, person=b"scrapmart-cursor").digest()
        self.max_age = max_age

    def _mac(self, body: str) -> str:
        return _b64e(hmac.new(self._key, body.encode("ascii"), hashlib.sha256).digest()[: self.MAC_BYTES])

    def dumps(self, payload: dict[str, Any]) -> str:
        payload = {"v": _CURSOR_VERSION, "i": int(time.time()), **payload}
        body = _b64e(json.dumps(payload, separators=(",", ":"), allow_nan=False).encode())
        return f"{body}.{self._mac(body)}"

    def loads(self, token: str) -> dict[str, Any]:
        if not isinstance(token, str) or not token or len(token) > MAX_TOKEN_LEN:
            raise InvalidCursor("malformed cursor")
        body, sep, mac = token.partition(".")
        if not sep or not (body.isascii() and mac.isascii()) or not hmac.compare_digest(mac, self._mac(body)):
            raise InvalidCursor("invalid cursor signature")
        try:
            payload = json.loads(_b64d(body))
        except (ValueError, UnicodeDecodeError) as exc:
            raise InvalidCursor("malformed cursor") from exc
        if not isinstance(payload, dict) or payload.get("v") != _CURSOR_VERSION:
            raise InvalidCursor("unsupported cursor version")
        if self.max_age is not None and time.time() - int(payload.get("i", 0)) > self.max_age:
            raise InvalidCursor("cursor expired")
        return payload


_codec: CursorCodec | None = None
_codec_lock = threading.Lock()


def configure_cursors(secret: str | bytes | None = None, *, max_age: int | None = None) -> CursorCodec:
    """Explicit setup (optional). Without it the secret comes from CURSOR_SECRET / SECRET_KEY / JWT_SECRET."""
    global _codec
    with _codec_lock:
        if secret is None:
            secret = (
                os.environ.get("CURSOR_SECRET")
                or os.environ.get("SECRET_KEY")
                or os.environ.get("JWT_SECRET")
            )
        if not secret:
            secret = secrets.token_bytes(32)
            log.warning(
                "CURSOR_SECRET is not set: using a random per-process secret. Cursors will break "
                "across workers and restarts - set CURSOR_SECRET in production."
            )
        _codec = CursorCodec(secret, max_age=max_age)
        return _codec


def get_cursor_codec() -> CursorCodec:
    return _codec or configure_cursors()


def make_fingerprint(**parts: Any) -> str:
    """Stable digest of everything that defines 'the same query' (filters, sort inputs, viewer).
    A cursor is only accepted together with an identical fingerprint."""
    blob = json.dumps(parts, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.blake2b(blob.encode(), digest_size=8).hexdigest()


# =============================================================================
# 4. RESULT + RESPONSE SCHEMA
# =============================================================================
@dataclass(frozen=True)
class PageResult(Generic[T]):
    rows: list[T]
    next_cursor: str | None
    has_more: bool
    total: int | None = None
    partial: bool = False

    def meta(self, limit: int) -> dict[str, Any]:
        """Everything for `CursorPage(...)` except `items`."""
        return {
            "next_cursor": self.next_cursor,
            "has_more": self.has_more,
            "limit": limit,
            "total": self.total,
            "partial": self.partial,
        }


class CursorPage(BaseModel, Generic[T]):
    items: list[T]
    next_cursor: str | None = None  # pass back as ?cursor= to get the next page; null on the last page
    has_more: bool = False
    limit: int
    offset: int = 0  # backwards compatibility with legacy offset-based consumers
    total: int | None = None  # only with include_total=true (snapshot taken on the first page)
    partial: bool = False  # true only if a shard failed AND allow_partial was set


# =============================================================================
# 5. THE ENGINE
# =============================================================================
def _decode_state(spec: SortSpec, fingerprint: str, token: str, codec: CursorCodec) -> tuple[list[Any], dict]:
    payload = codec.loads(token)
    if payload.get("s") != spec.name or payload.get("f") != fingerprint:
        raise InvalidCursor("cursor does not belong to this query")
    raw = payload.get("k")
    if not isinstance(raw, list) or len(raw) != len(spec.columns):
        raise InvalidCursor("malformed cursor")
    values: list[Any] = []
    for col, item in zip(spec.columns, raw):
        if item is None:
            if not col.nullable:
                raise InvalidCursor("malformed cursor")
            values.append(None)
            continue
        try:
            values.append(col.codec.decode(item))
        except Exception as exc:  # noqa: BLE001 - any decode failure means a bad cursor
            raise InvalidCursor("malformed cursor") from exc
    return values, payload


def _encode_state(spec: SortSpec, fingerprint: str, row: Any, total: int | None, codec: CursorCodec) -> str:
    encoded: list[Any] = []
    for col, value in zip(spec.columns, spec.values(row)):
        if value is None:
            if not col.nullable:
                raise CursorError(f"sort column {col.name!r} is NULL but not declared nullable")
            encoded.append(None)
        else:
            encoded.append(col.codec.encode(value))
    payload: dict[str, Any] = {"s": spec.name, "f": fingerprint, "k": encoded}
    if total is not None:
        payload["t"] = total
    return codec.dumps(payload)


def paginate_sharded(
    router: Any,
    *,
    base: Select,
    spec: SortSpec,
    filters: Sequence[ColumnElement] = (),
    limit: int = DEFAULT_LIMIT,
    cursor: str | None = None,
    fingerprint: str = "",
    count_stmt: Select | None = None,
    with_total: bool = False,
    allow_partial: bool = False,
    codec: CursorCodec | None = None,
) -> PageResult[Any]:
    """Keyset-paginate `base` across every shard and merge into one global page.

    router        object with `.scatter(fn) -> ScatterResult` (your ShardRouter)
    base          SELECT that returns the rows (joins / extra columns welcome); do NOT add
                  WHERE/ORDER BY/LIMIT that this function manages
    spec          total ordering; the same spec + fingerprint must be used for every page
    filters       WHERE clauses WITHOUT the seek predicate (it is added here)
    fingerprint   `make_fingerprint(...)` of filters / sort inputs / viewer
    count_stmt    e.g. select(func.count(Listing.id)); only used when with_total=True and
                  this is the first page (later pages echo the total stored in the cursor)
    allow_partial return what the healthy shards have (flagged `partial=True`) instead of
                  raising when a shard fails. Rows of the failed shard are skipped for that
                  request only; the cursor stays valid, so a retry can still see them.
    """
    if not 1 <= limit <= 1000:
        raise ValueError("limit out of range")
    codec = codec or get_cursor_codec()

    seek: ColumnElement | None = None
    carried_total: int | None = None
    if cursor:
        values, payload = _decode_state(spec, fingerprint, cursor, codec)
        seek = spec.seek(values)
        t = payload.get("t")
        carried_total = t if isinstance(t, int) and not isinstance(t, bool) and t >= 0 else None

    need_count = bool(with_total and cursor is None and count_stmt is not None)
    order = spec.order_by()
    probe = limit + 1  # one extra row tells us whether another page exists

    def fetch_shard(s_db: Any, sid: int) -> tuple[int | None, list[Any]]:
        stmt = base.where(*filters)
        if seek is not None:
            stmt = stmt.where(seek)
        rows = s_db.execute(stmt.order_by(*order).limit(probe)).all()
        count = (s_db.scalar(count_stmt.where(*filters)) or 0) if need_count else None
        return count, rows

    res = router.scatter(fetch_shard)
    partial = bool(getattr(res, "failed", None))
    if partial and (not allow_partial or not res.values()):
        res.raise_if_failed()

    per_shard = [rows for _, rows in res.values()]
    window = list(itertools.islice(heapq.merge(*per_shard, key=spec.merge_key), probe))
    has_more = len(window) > limit
    rows = window[:limit]

    total = carried_total
    if need_count:
        total = sum(c for c, _ in res.values() if c is not None)

    next_cursor = _encode_state(spec, fingerprint, rows[-1], total, codec) if (has_more and rows) else None
    return PageResult(rows=rows, next_cursor=next_cursor, has_more=has_more, total=total, partial=partial)


# =============================================================================
# 6. FASTAPI GLUE (optional)
# =============================================================================
try:
    from fastapi import FastAPI, Query
    from fastapi.responses import JSONResponse

    CursorQuery = Annotated[
        str | None,
        Query(max_length=MAX_TOKEN_LEN, description="Opaque cursor from the previous page's `next_cursor`."),
    ]
    LimitQuery = Annotated[int, Query(ge=1, le=MAX_LIMIT, description="Page size.")]

    def install_cursor_handlers(app: "FastAPI") -> None:
        """InvalidCursor -> 400 so clients can restart from the first page."""

        @app.exception_handler(InvalidCursor)
        async def _invalid_cursor(_, exc: InvalidCursor):  # noqa: ANN001
            log.info("400 invalid cursor: %s", exc)
            return JSONResponse({"detail": "Invalid or expired cursor", "code": "invalid_cursor"}, 400)

except ImportError:  # pragma: no cover
    CursorQuery = Annotated[str | None, "cursor"]  # type: ignore[misc]
    LimitQuery = Annotated[int, "limit"]  # type: ignore[misc]

    def install_cursor_handlers(app: Any) -> None:  # type: ignore[misc]
        raise RuntimeError("fastapi is not installed")


# =============================================================================
# 7. SELF-TEST   python cursor_pagination.py
#    Builds 4 in-memory SQLite "shards" with heavy ties and NULLs, walks every page of
#    every sort kind and checks the result equals ONE global ORDER BY over all rows.
# =============================================================================
def _selftest() -> None:  # pragma: no cover - exercised manually / in CI
    import random
    import warnings
    from datetime import timedelta

    from sqlalchemy import Column, DateTime, Float, MetaData, Numeric, Table, Uuid, create_engine, func
    from sqlalchemy.orm import Session
    from sqlalchemy.pool import StaticPool

    warnings.simplefilter("ignore")
    rng = random.Random(42)
    md = MetaData()
    items = Table(
        "items", md,
        Column("id", Uuid, primary_key=True),
        Column("created_at", DateTime),
        Column("price", Numeric(12, 2)),
        Column("dist", Float, nullable=True),
    )

    def engine():
        e = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
        md.create_all(e)
        return e

    shards = [engine() for _ in range(4)]
    global_db = engine()
    t0 = datetime(2026, 1, 1, 12, 0, 0, 123456)
    data = [
        {
            "id": uuid.uuid4(),
            "created_at": t0 + timedelta(minutes=rng.randrange(25)),  # lots of identical timestamps
            "price": Decimal(rng.randrange(10)) * 10,                 # lots of identical prices
            "dist": None if rng.random() < 0.25 else round(rng.uniform(0, 5), 1),
        }
        for _ in range(600)
    ]
    for row in data:
        for e in (rng.choice(shards), global_db):
            with e.begin() as c:
                c.execute(items.insert(), [row])

    class _Res:
        def __init__(self) -> None:
            self.ok: dict[int, Any] = {}
            self.failed: dict[int, BaseException] = {}

        def values(self) -> list[Any]:
            return [self.ok[k] for k in sorted(self.ok)]

        def raise_if_failed(self) -> None:
            if self.failed:
                raise RuntimeError(f"shards failed: {sorted(self.failed)}")

    class _Router:
        def __init__(self, bad: set[int] = frozenset()) -> None:  # type: ignore[assignment]
            self.bad = set(bad)

        def scatter(self, fn: Callable[[Any, int], Any]) -> _Res:
            r = _Res()
            for i, e in enumerate(shards):
                try:
                    if i in self.bad:
                        raise ConnectionError("shard down")
                    with Session(e) as s:
                        r.ok[i] = fn(s, i)
                except Exception as exc:  # noqa: BLE001
                    r.failed[i] = exc
            return r

    c = items.c
    ident = lambda d: SortColumn("id", c.id, UUID_, lambda r: r.id, descending=d)  # noqa: E731
    specs = {
        "newest": SortSpec("newest", [SortColumn("created_at", c.created_at, DATETIME, lambda r: r.created_at, descending=True), ident(True)]),
        "price_low": SortSpec("price_low", [SortColumn("price", c.price, DECIMAL, lambda r: r.price), ident(False)]),
        "mixed": SortSpec("mixed", [SortColumn("price", c.price, DECIMAL, lambda r: r.price, descending=True), ident(False)]),
        "nearest": SortSpec("nearest", [SortColumn("dist", c.dist, FLOAT, lambda r: r.dist, nullable=True), ident(False)]),
        "nearest_desc": SortSpec("nearest_desc", [SortColumn("dist", c.dist, FLOAT, lambda r: r.dist, descending=True, nullable=True), ident(True)]),
    }
    codec = CursorCodec("selftest-secret-0123456789")
    flt = [c.price >= 0]

    def walk(spec: SortSpec, limit: int, router: _Router, fp: str = "fp") -> tuple[list[uuid.UUID], list[int | None]]:
        ids: list[uuid.UUID] = []
        totals: list[int | None] = []
        cur = None
        for _ in range(10_000):
            r = paginate_sharded(
                router, base=select(items), spec=spec, filters=flt, limit=limit, cursor=cur,
                fingerprint=fp, count_stmt=select(func.count()).select_from(items),
                with_total=True, codec=codec,
            )
            ids += [row.id for row in r.rows]
            totals.append(r.total)
            assert len(r.rows) <= limit
            assert r.has_more == (r.next_cursor is not None)
            if not r.has_more:
                return ids, totals
            cur = r.next_cursor
        raise AssertionError("pagination did not terminate")

    for name, spec in specs.items():
        with global_db.connect() as conn:
            expected = [row.id for row in conn.execute(select(items).where(*flt).order_by(*spec.order_by()))]
        for limit in (1, 7, 50, 600, 601):
            got, totals = walk(spec, limit, _Router())
            assert got == expected, f"{name} limit={limit}: order/content mismatch"
            assert len(set(got)) == len(got) == 600
            assert set(totals) == {600}, f"{name}: total not carried through cursors: {set(totals)}"
        print(f"ok  {name:<13} (uniform fast-path={spec._uniform})")

    # --- safety checks ---------------------------------------------------------
    spec = specs["newest"]
    first = paginate_sharded(_Router(), base=select(items), spec=spec, filters=flt, limit=5,
                             fingerprint="fp", codec=codec)
    assert first.total is None and first.has_more and first.next_cursor

    def must_fail(**kw: Any) -> None:
        args = dict(base=select(items), spec=spec, filters=flt, limit=5, fingerprint="fp", codec=codec)
        args.update(kw)
        try:
            paginate_sharded(_Router(), **args)
        except InvalidCursor:
            return
        raise AssertionError(f"expected InvalidCursor for {kw.keys()}")

    tok = first.next_cursor
    must_fail(cursor=tok[:-2] + ("AA" if not tok.endswith("AA") else "BB"))      # tampered mac
    must_fail(cursor=tok, fingerprint="other-filters")                             # different filters/user
    must_fail(cursor=tok, spec=specs["price_low"])                                 # different sort
    must_fail(cursor="not-a-cursor")
    must_fail(cursor="x" * (MAX_TOKEN_LEN + 1))
    expired = CursorCodec("selftest-secret-0123456789", max_age=-1)
    must_fail(cursor=tok, codec=expired)
    print("ok  tamper / fingerprint / sort / garbage / expiry rejected")

    # --- shard failure policy ----------------------------------------------------
    try:
        paginate_sharded(_Router({2}), base=select(items), spec=spec, filters=flt, limit=5,
                         fingerprint="fp", codec=codec)
        raise AssertionError("strict mode must raise when a shard is down")
    except RuntimeError:
        pass
    part = paginate_sharded(_Router({2}), base=select(items), spec=spec, filters=flt, limit=5,
                            fingerprint="fp", codec=codec, allow_partial=True)
    assert part.partial and len(part.rows) == 5
    print("ok  strict failure + allow_partial")
    print("ALL CURSOR PAGINATION CHECKS PASSED")


if __name__ == "__main__":
    _selftest()
