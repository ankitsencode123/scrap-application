"""
ScrapMart real-time chat  ->  app/api/v1/chat_ws.py
====================================================

One self-contained module that adds WebSocket chat on top of the existing ``messages`` table:

  * live message delivery (+ optimistic-UI acks, idempotent retries via ``client_id``)
  * typing indicators (auto-expiring, throttled, authorised once per conversation)
  * read receipts + unread counters
  * online / offline presence
  * multi-worker / multi-instance fan-out through Redis pub/sub (falls back to in-process)
  * back-pressure, rate limits, frame-size limits, idle timeouts, connection caps
  * REST companions: history (keyset pagination), unread counts, mark-read

It re-uses your existing ``Message`` model, ``MessageCreate`` / ``MessageOut`` schemas, shard router and
the exact authorisation rules of ``POST /messages``. The only new table is ``chat_read_states``.

---------------------------------------------------------------------------------------------------
WIRE PROTOCOL  (JSON text frames, protocol v1)           ws(s)://<host>/api/v1/chat/ws
---------------------------------------------------------------------------------------------------
Auth (first match wins):  ``Authorization: Bearer <jwt>`` header  |  ``Sec-WebSocket-Protocol: bearer, <jwt>``
                          (best for browsers)  |  ``?token=<jwt>`` (dev only, ends up in logs)  |
                          first frame ``{"type":"auth","token":"<jwt>"}`` within 10 s.

client -> server
  {"type":"message.send","listing_id":U,"recipient_id":U,"body":"hi","client_id":"c-123"}
  {"type":"typing.start","listing_id":U,"recipient_id":U}      (re-send every ~2 s while typing)
  {"type":"typing.stop","listing_id":U,"recipient_id":U}
  {"type":"message.read","listing_id":U,"peer_id":U}           (conversation opened / new msg seen)
  {"type":"presence.watch","user_ids":[U,...]}                 (subscribe + get current state)
  {"type":"presence.unwatch","user_ids":[U,...]}
  {"type":"presence.query","user_ids":[U,...]}                 (one-off snapshot)
  {"type":"ping","ts":123}                                     (every ~25 s)

server -> client
  {"type":"ready","connection_id":..,"user":{..},"heartbeat":{..},"limits":{..}}
  {"type":"message.ack","client_id":"c-123","message":{MessageOut}}
  {"type":"message.new","message":{MessageOut}}               (also echoed to your *other* devices)
  {"type":"typing","listing_id":U,"user_id":U,"display_name":"..","is_typing":true}
  {"type":"read","listing_id":U,"reader_id":U,"peer_id":U,"read_at":ISO}
        reader_id == me   -> clear the unread badge of (listing, peer_id)   [sync across my devices]
        reader_id == peer -> show "seen" on my messages sent up to read_at
  {"type":"presence","user_id":U,"online":bool,"last_seen":ISO|null}
  {"type":"presence.state","users":{U:{"online":bool}}}
  {"type":"pong","ts":123,"server_time":ISO}
  {"type":"error","code":"rate_limited|forbidden|invalid_frame|...","message":"..","ref":"c-123|<frame type>"}

Close codes: 4401 unauthorised | 4408 idle/auth timeout | 4409 replaced by newer connection (cap)
             4429 abusive rate | 1009 frame too big | 1013 slow consumer | 1001 server restart

Minimal browser client
    const ws = new WebSocket(`wss://api.example.com/api/v1/chat/ws`, ["bearer", accessToken]);
    ws.onopen    = () => setInterval(() => ws.send(JSON.stringify({type: "ping", ts: Date.now()})), 25000);
    ws.onmessage = (e) => handle(JSON.parse(e.data));
    ws.send(JSON.stringify({type: "message.send", listing_id, recipient_id, body, client_id: crypto.randomUUID()}));

---------------------------------------------------------------------------------------------------
INTEGRATION (3 small edits)
---------------------------------------------------------------------------------------------------
1) app/api/v1/router.py
       from app.api.v1.chat_ws import chat_router
       ...add ``chat_router`` to the tuple of routers.

2) app/main.py  (lifespan)  - starts the Redis backplane eagerly so HTTP-sent messages also fan out
       from app.api.v1 import chat_ws
       ...after init_router():   await chat_ws.startup()
       ...before close_router(): await chat_ws.shutdown()

3) (optional, recommended) app/api/v1/messages.py :: send_message - after ``s_db.refresh(m)``
       from app.api.v1.chat_ws import announce_http_message
       out = message_out(m, sender=user, recipient=recipient)
       announce_http_message(out)
       return out
   so messages created by the legacy REST endpoint are pushed in real time too.

Set AUTO_CREATE_TABLES=true once (or run ``Base.metadata.create_all``) so ``chat_read_states`` exists on
every shard. Install ``redis>=5`` for multi-instance fan-out (optional; single instance works without).

Two things I could not see in the files you pasted are isolated in "INTEGRATION ADAPTERS" below:
how a JWT is decoded (``app.core.security``) and how a DB session is opened outside a request. Both have
introspection-based defaults and an explicit override: ``configure_chat(token_decoder=..., db_factory=...)``.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import contextlib
import inspect
import json
import time
import uuid
from collections import OrderedDict
from contextlib import contextmanager
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any, Awaitable, Callable, ContextManager, Iterable, get_args

from fastapi import APIRouter, HTTPException, Query, WebSocket
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import DateTime, ForeignKey, Uuid, and_, func, literal, or_, select, tuple_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Mapped, mapped_column
from starlette.concurrency import run_in_threadpool

from app.core.config import log, settings
from app.core.security import CurrentUser
from app.core.shard_router import get_router
from app.db.base import DB, Base, utcnow
from app.db.enums import ListingStatus
from app.db.models import ChatReadState, Listing, Message, User
from app.schemas import MessageCreate, MessageOut

__all__ = [
    "chat_router",
    "hub",
    "startup",
    "shutdown",
    "configure_chat",
    "announce_http_message",
    "ensure_chat_allowed",
    "ChatError",
    "ChatReadState",
]

# =================================================================================================
# Tunables
# =================================================================================================
PROTOCOL_VERSION = 1
MAX_FRAME_BYTES = 16 * 1024  # inbound text frame cap
AUTH_TIMEOUT_S = 10.0  # time allowed to send the auth frame
IDLE_TIMEOUT_S = 75.0  # no inbound frame (incl. ping) for this long -> close
HEARTBEAT_INTERVAL_S = 25  # advertised to clients
SEND_TIMEOUT_S = 10.0  # a single socket write may not block longer than this
OUTBOUND_QUEUE_MAX = 256  # per-connection buffer; overflow = slow consumer -> disconnect
MAX_CONNECTIONS_PER_USER = 5
TYPING_TTL_S = 6.0  # no refresh within this window -> server emits typing=false
TYPING_REBROADCAST_S = 2.0  # at most one typing=true forwarded per this interval per conversation
AUTHZ_CACHE_TTL_S = 60.0  # how long a (listing, peer) authorisation is trusted for typing
PRESENCE_TTL_S = 90  # presence entry lifetime without a heartbeat
PRESENCE_PEER_TTL_S = 300.0  # cache for "may I see this user's presence"
MAX_WATCHED_USERS = 200
IDEMPOTENCY_TTL_S = 300.0
IDEMPOTENCY_MAX = 20_000
MAX_STRIKES = 25  # rate-limit violations before the socket is dropped

CLOSE_UNAUTHORIZED = 4401
CLOSE_TIMEOUT = 4408
CLOSE_REPLACED = 4409
CLOSE_RATE_LIMITED = 4429


def _setting(name: str, default: Any = None) -> Any:
    return getattr(settings, name, default)


# =================================================================================================
# Errors & small utilities
# =================================================================================================
class ChatError(Exception):
    """A client-visible failure. ``status`` is used when the error surfaces over HTTP."""

    def __init__(self, code: str, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


def _json_default(o: Any) -> Any:
    if isinstance(o, uuid.UUID):
        return str(o)
    if isinstance(o, datetime):
        return o.isoformat()
    if isinstance(o, Decimal):
        return float(o)
    raise TypeError(f"not JSON serialisable: {type(o).__name__}")


def _dumps(obj: Any) -> str:
    return json.dumps(obj, separators=(",", ":"), default=_json_default)


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


def user_topic(user_id: uuid.UUID | str) -> str:
    return f"user:{user_id}"


def presence_topic(user_id: uuid.UUID | str) -> str:
    return f"presence:{user_id}"


class TokenBucket:
    """Classic token bucket; ``allow()`` is O(1) and allocation-free."""

    __slots__ = ("rate", "capacity", "tokens", "stamp")

    def __init__(self, rate: float, capacity: float) -> None:
        self.rate, self.capacity, self.tokens, self.stamp = rate, capacity, capacity, time.monotonic()

    def allow(self, cost: float = 1.0) -> bool:
        now = time.monotonic()
        self.tokens = min(self.capacity, self.tokens + (now - self.stamp) * self.rate)
        self.stamp = now
        if self.tokens >= cost:
            self.tokens -= cost
            return True
        return False


def _validate(model: type[BaseModel], data: Any) -> Any:
    try:
        return model.model_validate(data)
    except ValidationError as exc:
        err = exc.errors()[0]
        loc = ".".join(str(p) for p in err["loc"])
        raise ChatError("invalid_frame", f"{loc}: {err['msg']}" if loc else err["msg"]) from None


# =================================================================================================
# Persistence: read-state table (one row per reader x peer x listing, lives on the listing's shard)
# =================================================================================================
# ChatReadState is imported from app.db.models to ensure single registration on Base.metadata
ChatReadState = ChatReadState


# =================================================================================================
# INTEGRATION ADAPTERS  (the only code that depends on files I was not shown)
# =================================================================================================
_token_decoder: Callable[[str], uuid.UUID] | None = None
_db_factory: Callable[[], ContextManager[Any]] | None = None


def configure_chat(
    *,
    token_decoder: Callable[[str], uuid.UUID | str | dict] | None = None,
    db_factory: Callable[[], ContextManager[Any]] | None = None,
) -> None:
    """
    Override the adapters explicitly.

    token_decoder(token) -> user id (UUID/str) or a claims dict containing ``sub``; raise on invalid tokens.
    db_factory()         -> context manager yielding the same object the ``DB`` dependency yields
                            (``.for_id(listing_id)``, ``.get(User, id)``), closed on exit.
    """
    global _token_decoder, _db_factory
    if token_decoder is not None:
        _token_decoder = lambda t: _subject_to_uuid(token_decoder(t))  # noqa: E731
    if db_factory is not None:
        _db_factory = db_factory


def _subject_to_uuid(data: Any) -> uuid.UUID:
    if isinstance(data, dict):
        if str(data.get("type") or data.get("typ") or "access").lower() == "refresh":
            raise ChatError("unauthorized", "Refresh tokens cannot open a chat socket", 401)
        sub = data.get("sub") or data.get("user_id")
    else:
        sub = getattr(data, "sub", data)
    try:
        return sub if isinstance(sub, uuid.UUID) else uuid.UUID(str(sub))
    except (ValueError, AttributeError, TypeError):
        raise ChatError("unauthorized", "Invalid or expired token", 401) from None


def _default_token_decoder(token: str) -> uuid.UUID:
    """Try the usual helper names in app.core.security, then fall back to PyJWT with common settings."""
    from app.core import security

    for name in ("decode_access_token", "decode_token", "verify_access_token", "verify_token"):
        fn = getattr(security, name, None)
        if callable(fn):
            try:
                return _subject_to_uuid(fn(token))
            except ChatError:
                raise
            except Exception:
                raise ChatError("unauthorized", "Invalid or expired token", 401) from None

    import jwt  # PyJWT

    secret = _setting("JWT_SECRET") or _setting("JWT_SECRET_KEY") or _setting("SECRET_KEY")
    if not secret:
        raise RuntimeError("chat: cannot decode tokens - call configure_chat(token_decoder=...)")
    alg = _setting("JWT_ALGORITHM") or _setting("ALGORITHM") or "HS256"
    try:
        return _subject_to_uuid(jwt.decode(token, secret, algorithms=[alg]))
    except jwt.PyJWTError:
        raise ChatError("unauthorized", "Invalid or expired token", 401) from None


def decode_token(token: str) -> uuid.UUID:
    return (_token_decoder or _default_token_decoder)(token)


@contextmanager
def _call_plain(dep: Callable[[], Any]):
    obj = dep()
    try:
        yield obj
    finally:
        close = getattr(obj, "close", None)
        if callable(close):
            close()


def _build_db_factory() -> Callable[[], ContextManager[Any]]:
    """Re-use whatever the ``DB`` dependency does, but outside of a request."""
    try:
        dep = next((getattr(m, "dependency", None) for m in get_args(DB)[1:] if getattr(m, "dependency", None)), None)
        if dep is not None:
            if inspect.isgeneratorfunction(dep):
                return contextmanager(dep)
            if not inspect.isasyncgenfunction(dep) and not inspect.iscoroutinefunction(dep):
                return lambda: _call_plain(dep)
    except Exception:
        pass
    return lambda: get_router().unit_of_work()


def open_db() -> ContextManager[Any]:
    """Short-lived session facade. Never hold one across an ``await`` on the event loop."""
    global _db_factory
    if _db_factory is None:
        _db_factory = _build_db_factory()
    return _db_factory()


# =================================================================================================
# Domain rules (identical to POST /messages) + sync DB operations (run in the thread pool)
# =================================================================================================
def ensure_chat_allowed(listing: Listing, sender: User, recipient: User, *, require_open: bool = True) -> None:
    """
    One side must be the listing's seller and the other a collector. While the listing is still open any
    collector may talk to the seller; afterwards only the assigned collector can (``require_open=True``).
    History / read receipts pass ``require_open=False`` so old conversations stay readable.
    """
    msg = "Chats are only between the seller and a collector of this listing"
    if listing.seller_id not in {sender.id, recipient.id}:
        raise ChatError("forbidden", msg, 403)
    other = recipient if sender.id == listing.seller_id else sender
    if getattr(other.role, "value", other.role) != "collector":
        raise ChatError("forbidden", msg, 403)
    if require_open and listing.status != ListingStatus.POSTED and other.id != listing.collector_id:
        raise ChatError("listing_assigned", "This listing is already assigned to another collector", 403)


def _load_pair(db: Any, listing_id: uuid.UUID, sender_id: uuid.UUID, recipient_id: uuid.UUID, *, require_open: bool):
    s_db = db.for_id(listing_id)
    listing = s_db.get(Listing, listing_id)
    if not listing:
        raise ChatError("listing_not_found", "Listing not found", 404)
    sender = db.get(User, sender_id)
    if not sender or not sender.is_active:
        raise ChatError("unauthorized", "Account not found or disabled", 401)
    recipient = db.get(User, recipient_id)
    if not recipient or not recipient.is_active or recipient.id == sender.id:
        raise ChatError("recipient_not_found", "Recipient not found", 404)
    ensure_chat_allowed(listing, sender, recipient, require_open=require_open)
    return s_db, listing, sender, recipient


def _wire_message(m: Message, listing: Listing, sender: User, recipient: User) -> dict[str, Any]:
    return MessageOut(
        id=m.id,
        listing_id=m.listing_id,
        listing_title=listing.title,
        sender_id=m.sender_id,
        sender_name=sender.display_name,
        recipient_id=m.recipient_id,
        recipient_name=recipient.display_name,
        body=m.body,
        created_at=m.created_at,
    ).model_dump(mode="json")


def _authenticate_sync(token: str) -> dict[str, Any]:
    uid = decode_token(token)
    with open_db() as db:
        user = db.get(User, uid)
        if not user or not user.is_active:
            raise ChatError("unauthorized", "Account not found or disabled", 401)
        return {
            "id": user.id,
            "display_name": user.display_name,
            "role": getattr(user.role, "value", str(user.role)),
        }


def _authorize_pair_sync(sender_id: uuid.UUID, listing_id: uuid.UUID, peer_id: uuid.UUID) -> None:
    with open_db() as db:
        _load_pair(db, listing_id, sender_id, peer_id, require_open=True)


def _persist_message_sync(sender_id: uuid.UUID, payload: MessageCreate) -> dict[str, Any]:
    with open_db() as db:
        s_db, listing, sender, recipient = _load_pair(
            db, payload.listing_id, sender_id, payload.recipient_id, require_open=True
        )
        m = Message(
            id=get_router().colocated_id(listing.id),
            listing_id=listing.id,
            sender_id=sender.id,
            recipient_id=recipient.id,
            body=payload.body,
        )
        s_db.add(m)
        s_db.commit()
        s_db.refresh(m)

        try:
            from app.db.chat_db import insert_supabase_message

            insert_supabase_message(
                message_id=str(m.id),
                listing_id=str(listing.id),
                listing_title=listing.title,
                sender_id=str(sender.id),
                sender_name=sender.display_name,
                recipient_id=str(recipient.id),
                recipient_name=recipient.display_name,
                body=payload.body,
            )
        except Exception:
            pass

        return _wire_message(m, listing, sender, recipient)


def _mark_read_sync(reader_id: uuid.UUID, listing_id: uuid.UUID, peer_id: uuid.UUID) -> datetime:
    with open_db() as db:
        s_db, *_ = _load_pair(db, listing_id, reader_id, peer_id, require_open=False)
        now = utcnow()
        for attempt in (1, 2):  # second pass handles a concurrent first-insert race
            row = s_db.get(ChatReadState, (listing_id, reader_id, peer_id))
            if row:
                row.last_read_at = now
            else:
                s_db.add(ChatReadState(listing_id=listing_id, reader_id=reader_id, peer_id=peer_id, last_read_at=now))
            try:
                s_db.commit()
                return now
            except IntegrityError:
                s_db.rollback()
                if attempt == 2:
                    raise
        return now  # pragma: no cover


def _visible_peers_sync(me: uuid.UUID, candidates: list[uuid.UUID]) -> set[uuid.UUID]:
    """Presence is only shared between users who already have a conversation."""
    if not candidates:
        return set()

    def probe(s_db: Any, _sid: Any) -> set[uuid.UUID]:
        sent = s_db.scalars(
            select(Message.recipient_id).where(Message.sender_id == me, Message.recipient_id.in_(candidates)).distinct()
        )
        got = s_db.scalars(
            select(Message.sender_id).where(Message.recipient_id == me, Message.sender_id.in_(candidates)).distinct()
        )
        return set(sent) | set(got)

    out: set[uuid.UUID] = set()
    for part in get_router().scatter(probe).values():
        out |= part
    return out


# =================================================================================================
# Backplane: cross-instance pub/sub + presence registry
# =================================================================================================
OnBackplaneMessage = Callable[[str, str], None]


class Backplane:
    """Interface. Topics are plain strings; payloads are opaque JSON strings."""

    async def start(self, on_message: OnBackplaneMessage) -> None: ...
    async def stop(self) -> None: ...
    async def subscribe(self, topic: str) -> None: ...
    async def unsubscribe(self, topic: str) -> None: ...
    async def publish(self, topic: str, envelope: str) -> None: ...
    async def presence_touch(self, user_id: uuid.UUID, conn_id: str) -> bool:
        """Register / refresh a connection. Returns True when the user just became online (cluster-wide)."""
        raise NotImplementedError

    async def presence_drop(self, user_id: uuid.UUID, conn_id: str) -> bool:
        """Remove a connection. Returns True when the user has no connection left anywhere."""
        raise NotImplementedError

    async def presence_query(self, user_ids: Iterable[uuid.UUID]) -> dict[uuid.UUID, bool]:
        raise NotImplementedError


class LocalBackplane(Backplane):
    """Single-process implementation: no network, presence kept in memory."""

    def __init__(self) -> None:
        self._online: dict[uuid.UUID, set[str]] = {}

    async def start(self, on_message: OnBackplaneMessage) -> None:
        return None

    async def stop(self) -> None:
        self._online.clear()

    async def subscribe(self, topic: str) -> None:
        return None

    async def unsubscribe(self, topic: str) -> None:
        return None

    async def publish(self, topic: str, envelope: str) -> None:
        return None

    async def presence_touch(self, user_id: uuid.UUID, conn_id: str) -> bool:
        conns = self._online.setdefault(user_id, set())
        was_offline = not conns
        conns.add(conn_id)
        return was_offline

    async def presence_drop(self, user_id: uuid.UUID, conn_id: str) -> bool:
        conns = self._online.get(user_id)
        if not conns:
            return False
        conns.discard(conn_id)
        if not conns:
            del self._online[user_id]
            return True
        return False

    async def presence_query(self, user_ids: Iterable[uuid.UUID]) -> dict[uuid.UUID, bool]:
        return {u: bool(self._online.get(u)) for u in user_ids}


class RedisBackplane(Backplane):
    """
    Redis pub/sub for fan-out (one channel per topic, subscribed lazily by whichever instances hold a
    local socket interested in it) and a ZSET per user for presence (member = connection id,
    score = expiry timestamp, so crashed instances age out on their own).
    """

    PREFIX = "scrapmart:chat:"

    def __init__(self, url: str) -> None:
        self._url = url
        self._redis: Any = None
        self._pubsub: Any = None
        self._task: asyncio.Task | None = None
        self._topics: set[str] = set()
        self._lock = asyncio.Lock()
        self._closing = False
        self._on_message: OnBackplaneMessage = lambda t, p: None

    # ---- lifecycle ---------------------------------------------------------------------------
    async def start(self, on_message: OnBackplaneMessage) -> None:
        import redis.asyncio as aioredis  # optional dependency

        self._on_message = on_message
        self._redis = aioredis.from_url(self._url, decode_responses=True, health_check_interval=30)
        await self._redis.ping()
        await self._new_pubsub()
        self._task = asyncio.create_task(self._reader(), name="chat-redis-reader")

    async def stop(self) -> None:
        self._closing = True
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._task
        with contextlib.suppress(Exception):
            if self._pubsub:
                await self._pubsub.aclose()
            if self._redis:
                await self._redis.aclose()

    async def _new_pubsub(self) -> None:
        """(Re)create the pub/sub connection. A permanent control channel keeps it valid with no topics."""
        if self._pubsub:
            with contextlib.suppress(Exception):
                await self._pubsub.aclose()
        self._pubsub = self._redis.pubsub(ignore_subscribe_messages=True)
        await self._pubsub.subscribe(self.PREFIX + "control", *[self.PREFIX + t for t in self._topics])

    async def _reader(self) -> None:
        backoff = 0.5
        while not self._closing:
            try:
                msg = await self._pubsub.get_message(timeout=1.0)
                if msg is None:
                    continue
                backoff = 0.5
                if msg.get("type") != "message":
                    continue
                channel, data = msg["channel"], msg["data"]
                self._on_message(channel[len(self.PREFIX):], data)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # network blip, failover, ...
                log.warning("chat redis reader error: %s (retry in %.1fs)", exc, backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 15.0)
                with contextlib.suppress(Exception):
                    async with self._lock:
                        await self._new_pubsub()

    # ---- pub/sub -----------------------------------------------------------------------------
    async def subscribe(self, topic: str) -> None:
        async with self._lock:
            self._topics.add(topic)
            with contextlib.suppress(Exception):
                await self._pubsub.subscribe(self.PREFIX + topic)

    async def unsubscribe(self, topic: str) -> None:
        async with self._lock:
            self._topics.discard(topic)
            with contextlib.suppress(Exception):
                await self._pubsub.unsubscribe(self.PREFIX + topic)

    async def publish(self, topic: str, envelope: str) -> None:
        try:
            await self._redis.publish(self.PREFIX + topic, envelope)
        except Exception as exc:
            log.warning("chat redis publish failed (%s): %s", topic, exc)

    # ---- presence ----------------------------------------------------------------------------
    def _pkey(self, user_id: uuid.UUID) -> str:
        return f"{self.PREFIX}presence:{user_id}"

    async def presence_touch(self, user_id: uuid.UUID, conn_id: str) -> bool:
        key, now = self._pkey(user_id), time.time()
        try:
            pipe = self._redis.pipeline(transaction=True)
            pipe.zremrangebyscore(key, "-inf", now)
            pipe.zcard(key)
            pipe.zadd(key, {conn_id: now + PRESENCE_TTL_S})
            pipe.expire(key, PRESENCE_TTL_S * 2)
            _, before, *_ = await pipe.execute()
            return before == 0
        except Exception as exc:
            log.warning("chat presence_touch failed: %s", exc)
            return False

    async def presence_drop(self, user_id: uuid.UUID, conn_id: str) -> bool:
        key, now = self._pkey(user_id), time.time()
        try:
            pipe = self._redis.pipeline(transaction=True)
            pipe.zrem(key, conn_id)
            pipe.zremrangebyscore(key, "-inf", now)
            pipe.zcard(key)
            *_, remaining = await pipe.execute()
            return remaining == 0
        except Exception as exc:
            log.warning("chat presence_drop failed: %s", exc)
            return False

    async def presence_query(self, user_ids: Iterable[uuid.UUID]) -> dict[uuid.UUID, bool]:
        ids, now = list(user_ids), time.time()
        if not ids:
            return {}
        try:
            pipe = self._redis.pipeline(transaction=False)
            for u in ids:
                pipe.zcount(self._pkey(u), f"({now}", "+inf")
            return {u: n > 0 for u, n in zip(ids, await pipe.execute())}
        except Exception as exc:
            log.warning("chat presence_query failed: %s", exc)
            return {u: False for u in ids}


# =================================================================================================
# Connection
# =================================================================================================
class Connection:
    """
    One authenticated socket. Outbound frames go through a bounded queue drained by a dedicated writer
    task, so a slow client can never stall a publisher or other connections.
    """

    def __init__(self, ws: WebSocket, user: dict[str, Any]) -> None:
        self.id = uuid.uuid4().hex
        self.ws = ws
        self.user_id: uuid.UUID = user["id"]
        self.display_name: str = user["display_name"]
        self.role: str = user["role"]
        self.created = time.monotonic()
        self.queue: asyncio.Queue[str] = asyncio.Queue(maxsize=OUTBOUND_QUEUE_MAX)
        self.topics: set[str] = set()
        self.closed = asyncio.Event()
        self.close_code: int | None = None
        self.close_reason = ""
        # rate limiting
        self.frame_bucket = TokenBucket(rate=30, capacity=60)
        self.message_bucket = TokenBucket(rate=2, capacity=10)
        self.typing_bucket = TokenBucket(rate=5, capacity=10)
        self.strikes = 0
        self.last_strike = 0.0
        # caches
        self.authz: dict[tuple[uuid.UUID, uuid.UUID], float] = {}
        self.presence_ok: dict[uuid.UUID, float] = {}
        self.watching: set[uuid.UUID] = set()
        self.last_presence_touch = time.monotonic()
        self.typing = TypingTracker(self)

    # ---- outbound ----------------------------------------------------------------------------
    def send(self, event: dict[str, Any]) -> None:
        self.send_raw(_dumps(event))

    def send_raw(self, payload: str) -> None:
        if self.closed.is_set():
            return
        try:
            self.queue.put_nowait(payload)
        except asyncio.QueueFull:
            log.warning("chat: slow consumer %s (user=%s) dropped", self.id, self.user_id)
            self.request_close(1013, "slow consumer")

    def error(self, code: str, message: str, ref: str | None = None) -> None:
        self.send({"type": "error", "code": code, "message": message, "ref": ref})

    def request_close(self, code: int, reason: str = "") -> None:
        if not self.closed.is_set():
            self.close_code, self.close_reason = code, reason
            self.closed.set()

    # ---- helpers -----------------------------------------------------------------------------
    def strike(self) -> None:
        self.strikes += 1
        self.last_strike = time.monotonic()
        if self.strikes >= MAX_STRIKES:
            self.request_close(CLOSE_RATE_LIMITED, "rate limit exceeded")


class _TypingState:
    __slots__ = ("timer", "last_sent")

    def __init__(self) -> None:
        self.timer: asyncio.TimerHandle | None = None
        self.last_sent = 0.0


class TypingTracker:
    """
    Per-connection typing state machine. ``start`` is cheap and idempotent: it refreshes the expiry timer
    and forwards at most one ``typing=true`` per TYPING_REBROADCAST_S. If the client goes silent (crash,
    tunnel, lost network) the server itself emits ``typing=false`` after TYPING_TTL_S.
    """

    def __init__(self, conn: Connection) -> None:
        self._conn = conn
        self._active: dict[tuple[uuid.UUID, uuid.UUID], _TypingState] = {}
        self._tasks: set[asyncio.Task] = set()

    async def start(self, listing_id: uuid.UUID, peer_id: uuid.UUID) -> None:
        key, now = (listing_id, peer_id), time.monotonic()
        loop = asyncio.get_running_loop()
        state = self._active.get(key)
        forward = state is None or now - state.last_sent >= TYPING_REBROADCAST_S
        if state is None:
            state = self._active[key] = _TypingState()
        elif state.timer:
            state.timer.cancel()
        state.timer = loop.call_later(TYPING_TTL_S, self._expire, key, state)
        if forward:
            state.last_sent = now
            await self._emit(listing_id, peer_id, True)

    async def stop(self, listing_id: uuid.UUID, peer_id: uuid.UUID, *, only: _TypingState | None = None) -> None:
        key = (listing_id, peer_id)
        state = self._active.get(key)
        if state is None or (only is not None and state is not only):
            return
        del self._active[key]
        if state.timer:
            state.timer.cancel()
        await self._emit(listing_id, peer_id, False)

    async def stop_all(self) -> None:
        for listing_id, peer_id in list(self._active):
            await self.stop(listing_id, peer_id)

    def _expire(self, key: tuple[uuid.UUID, uuid.UUID], state: _TypingState) -> None:
        task = asyncio.get_running_loop().create_task(self.stop(*key, only=state))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _emit(self, listing_id: uuid.UUID, peer_id: uuid.UUID, is_typing: bool) -> None:
        c = self._conn
        await hub.publish(
            user_topic(peer_id),
            {
                "type": "typing",
                "listing_id": listing_id,
                "user_id": c.user_id,
                "display_name": c.display_name,
                "is_typing": is_typing,
            },
        )


# =================================================================================================
# Hub
# =================================================================================================
MessageListener = Callable[[dict[str, Any], bool], "Awaitable[None] | None"]


class ChatHub:
    """Topic registry for this process + the bridge to the (optional) cross-instance backplane."""

    def __init__(self) -> None:
        self.instance_id = uuid.uuid4().hex
        self.loop: asyncio.AbstractEventLoop | None = None
        self.backplane: Backplane = LocalBackplane()
        self._users: dict[uuid.UUID, dict[str, Connection]] = {}
        self._topics: dict[str, set[Connection]] = {}
        self._topic_lock = asyncio.Lock()
        self._start_lock = asyncio.Lock()
        self._started = False
        self._idem: OrderedDict[tuple[uuid.UUID, str], tuple[float, dict[str, Any]]] = OrderedDict()
        self._listeners: list[MessageListener] = []
        self._tasks: set[asyncio.Task] = set()

    # ---- lifecycle ---------------------------------------------------------------------------
    async def ensure_started(self) -> None:
        if self._started:
            return
        async with self._start_lock:
            if self._started:
                return
            self.loop = asyncio.get_running_loop()
            url = _setting("CHAT_REDIS_URL") or _setting("REDIS_URL") or _setting("MAP_REDIS_URL")
            backplane: Backplane = LocalBackplane()
            if url:
                try:
                    candidate = RedisBackplane(url)
                    await candidate.start(self._on_backplane_message)
                    backplane = candidate
                    log.info("chat: Redis backplane active (instance=%s)", self.instance_id)
                except Exception as exc:
                    log.warning("chat: Redis unavailable (%s) - single-instance mode", exc)
            else:
                log.info("chat: no REDIS_URL configured - single-instance mode")
            self.backplane = backplane
            self._started = True

    async def shutdown(self) -> None:
        for conns in list(self._users.values()):
            for conn in list(conns.values()):
                conn.request_close(1001, "server shutting down")
        await asyncio.sleep(0)
        await self.backplane.stop()
        self._started = False

    def stats(self) -> dict[str, Any]:
        return {
            "instance": self.instance_id,
            "backplane": type(self.backplane).__name__,
            "users": len(self._users),
            "connections": sum(len(c) for c in self._users.values()),
            "topics": len(self._topics),
        }

    def add_listener(self, fn: MessageListener) -> None:
        """``fn(wire_message, recipient_online)`` runs after every persisted message (push notifications...)."""
        self._listeners.append(fn)

    def spawn(self, coro: Awaitable[Any]) -> None:
        task = asyncio.ensure_future(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    # ---- connection registry -----------------------------------------------------------------
    async def register(self, conn: Connection) -> None:
        mine = self._users.setdefault(conn.user_id, {})
        excess = len(mine) - MAX_CONNECTIONS_PER_USER + 1
        if excess > 0:
            for old in sorted(mine.values(), key=lambda c: c.created)[:excess]:
                old.request_close(CLOSE_REPLACED, "replaced by a newer connection")
        mine[conn.id] = conn
        await self.join(conn, user_topic(conn.user_id))
        if await self.backplane.presence_touch(conn.user_id, conn.id):
            await self.publish(
                presence_topic(conn.user_id), {"type": "presence", "user_id": conn.user_id, "online": True, "last_seen": None}
            )

    async def unregister(self, conn: Connection) -> None:
        mine = self._users.get(conn.user_id)
        if mine:
            mine.pop(conn.id, None)
            if not mine:
                del self._users[conn.user_id]
        for topic in list(conn.topics):
            await self.leave(conn, topic)
        if await self.backplane.presence_drop(conn.user_id, conn.id):
            await self.publish(
                presence_topic(conn.user_id),
                {"type": "presence", "user_id": conn.user_id, "online": False, "last_seen": _iso(utcnow())},
            )

    async def join(self, conn: Connection, topic: str) -> None:
        async with self._topic_lock:
            subs = self._topics.setdefault(topic, set())
            first = not subs
            subs.add(conn)
            conn.topics.add(topic)
            if first:
                await self.backplane.subscribe(topic)

    async def leave(self, conn: Connection, topic: str) -> None:
        async with self._topic_lock:
            conn.topics.discard(topic)
            subs = self._topics.get(topic)
            if subs is None:
                return
            subs.discard(conn)
            if not subs:
                del self._topics[topic]
                await self.backplane.unsubscribe(topic)

    # ---- publishing --------------------------------------------------------------------------
    async def publish(self, topic: str, event: dict[str, Any], *, exclude: str | None = None) -> None:
        """Deliver to local subscribers immediately and to other instances via the backplane."""
        payload = _dumps(event)
        self._deliver_local(topic, payload, exclude)
        envelope = f'{{"o":{json.dumps(self.instance_id)},"x":{json.dumps(exclude)},"e":{payload}}}'
        await self.backplane.publish(topic, envelope)

    def publish_threadsafe(self, topic: str, event: dict[str, Any], *, exclude: str | None = None) -> bool:
        """Entry point for sync code running in worker threads (legacy REST routes)."""
        loop = self.loop
        if loop is None or loop.is_closed():
            return False
        asyncio.run_coroutine_threadsafe(self.publish(topic, event, exclude=exclude), loop)
        return True

    def _deliver_local(self, topic: str, payload: str, exclude: str | None) -> None:
        for conn in tuple(self._topics.get(topic, ())):
            if conn.id != exclude:
                conn.send_raw(payload)

    def _on_backplane_message(self, topic: str, raw: str) -> None:
        try:
            data = json.loads(raw)
            if data.get("o") == self.instance_id:  # our own echo; already delivered locally
                return
            self._deliver_local(topic, _dumps(data["e"]), data.get("x"))
        except Exception as exc:
            log.warning("chat: bad backplane payload on %s: %s", topic, exc)

    # ---- messages ----------------------------------------------------------------------------
    async def announce_message(
        self, wire: dict[str, Any], *, origin: Connection | None = None, client_id: str | None = None
    ) -> None:
        """Ack the sender, push to recipient + sender's other devices, run listeners."""
        if origin is not None:
            origin.send({"type": "message.ack", "client_id": client_id, "message": wire})
        event = {"type": "message.new", "message": wire}
        await self.publish(user_topic(wire["recipient_id"]), event)
        await self.publish(user_topic(wire["sender_id"]), event, exclude=origin.id if origin else None)
        if self._listeners:
            self.spawn(self._run_listeners(wire))

    async def _run_listeners(self, wire: dict[str, Any]) -> None:
        rid = uuid.UUID(wire["recipient_id"])
        online = (await self.backplane.presence_query([rid])).get(rid, False)
        for fn in self._listeners:
            try:
                res = fn(wire, online)
                if inspect.isawaitable(res):
                    await res
            except Exception:
                log.exception("chat message listener failed")

    # ---- idempotency (retry-safe sends) ------------------------------------------------------
    def idem_get(self, user_id: uuid.UUID, client_id: str) -> dict[str, Any] | None:
        hit = self._idem.get((user_id, client_id))
        if hit and hit[0] > time.monotonic():
            return hit[1]
        return None

    def idem_put(self, user_id: uuid.UUID, client_id: str, wire: dict[str, Any]) -> None:
        now = time.monotonic()
        self._idem[(user_id, client_id)] = (now + IDEMPOTENCY_TTL_S, wire)
        self._idem.move_to_end((user_id, client_id))
        while self._idem and (len(self._idem) > IDEMPOTENCY_MAX or next(iter(self._idem.values()))[0] < now):
            self._idem.popitem(last=False)


hub = ChatHub()


async def startup() -> None:
    """Call from the app lifespan (see module docstring)."""
    await hub.ensure_started()


async def shutdown() -> None:
    await hub.shutdown()


def announce_http_message(out: MessageOut | dict[str, Any]) -> None:
    """Sync-safe bridge for the legacy ``POST /messages`` route: pushes the stored message over WebSockets."""
    wire = out.model_dump(mode="json") if isinstance(out, BaseModel) else out
    loop = hub.loop
    if loop is None or loop.is_closed():
        return
    asyncio.run_coroutine_threadsafe(hub.announce_message(wire), loop)


# =================================================================================================
# Inbound frames
# =================================================================================================
class _TypingFrame(BaseModel):
    listing_id: uuid.UUID
    recipient_id: uuid.UUID


class _ReadFrame(BaseModel):
    listing_id: uuid.UUID
    peer_id: uuid.UUID


class _UsersFrame(BaseModel):
    user_ids: list[uuid.UUID] = Field(min_length=1, max_length=MAX_WATCHED_USERS)


def _client_id(frame: dict[str, Any]) -> str | None:
    cid = frame.get("client_id")
    if cid is None:
        return None
    if not isinstance(cid, str) or not (1 <= len(cid) <= 64):
        raise ChatError("invalid_frame", "client_id must be a string of 1-64 characters")
    return cid


async def _authorize_cached(conn: Connection, listing_id: uuid.UUID, peer_id: uuid.UUID) -> None:
    key, now = (listing_id, peer_id), time.monotonic()
    if conn.authz.get(key, 0.0) > now:
        return
    await run_in_threadpool(_authorize_pair_sync, conn.user_id, listing_id, peer_id)
    if len(conn.authz) > 512:
        conn.authz = {k: v for k, v in conn.authz.items() if v > now}
    conn.authz[key] = now + AUTHZ_CACHE_TTL_S


async def _h_ping(conn: Connection, frame: dict[str, Any]) -> None:
    conn.send({"type": "pong", "ts": frame.get("ts"), "server_time": _iso(utcnow())})


async def _h_send(conn: Connection, frame: dict[str, Any]) -> None:
    client_id = _client_id(frame)
    if not conn.message_bucket.allow():
        raise ChatError("rate_limited", "You are sending messages too fast", 429)
    payload: MessageCreate = _validate(MessageCreate, frame)
    if client_id and (dup := hub.idem_get(conn.user_id, client_id)):
        conn.send({"type": "message.ack", "client_id": client_id, "message": dup, "duplicate": True})
        return
    wire = await run_in_threadpool(_persist_message_sync, conn.user_id, payload)
    if client_id:
        hub.idem_put(conn.user_id, client_id, wire)
    conn.authz[(payload.listing_id, payload.recipient_id)] = time.monotonic() + AUTHZ_CACHE_TTL_S
    await conn.typing.stop(payload.listing_id, payload.recipient_id)  # sending ends typing
    await hub.announce_message(wire, origin=conn, client_id=client_id)


async def _h_typing_start(conn: Connection, frame: dict[str, Any]) -> None:
    f: _TypingFrame = _validate(_TypingFrame, frame)
    if not conn.typing_bucket.allow():
        return  # silently drop: typing is best-effort
    await _authorize_cached(conn, f.listing_id, f.recipient_id)
    await conn.typing.start(f.listing_id, f.recipient_id)


async def _h_typing_stop(conn: Connection, frame: dict[str, Any]) -> None:
    f: _TypingFrame = _validate(_TypingFrame, frame)
    await conn.typing.stop(f.listing_id, f.recipient_id)


async def _publish_read(me: uuid.UUID, listing_id: uuid.UUID, peer_id: uuid.UUID, read_at: datetime, *, origin: str | None):
    event = {"type": "read", "listing_id": listing_id, "reader_id": me, "peer_id": peer_id, "read_at": read_at}
    await hub.publish(user_topic(peer_id), event)  # peer: "seen"
    await hub.publish(user_topic(me), event, exclude=origin)  # my other devices: clear badge


async def _h_read(conn: Connection, frame: dict[str, Any]) -> None:
    f: _ReadFrame = _validate(_ReadFrame, frame)
    read_at = await run_in_threadpool(_mark_read_sync, conn.user_id, f.listing_id, f.peer_id)
    await _publish_read(conn.user_id, f.listing_id, f.peer_id, read_at, origin=conn.id)


async def _allowed_presence(conn: Connection, ids: list[uuid.UUID]) -> list[uuid.UUID]:
    now = time.monotonic()
    unknown = [u for u in dict.fromkeys(ids) if conn.presence_ok.get(u, 0.0) <= now and u != conn.user_id]
    if unknown:
        for u in await run_in_threadpool(_visible_peers_sync, conn.user_id, unknown):
            conn.presence_ok[u] = now + PRESENCE_PEER_TTL_S
    return [u for u in dict.fromkeys(ids) if conn.presence_ok.get(u, 0.0) > now]


async def _presence_state(conn: Connection, ids: list[uuid.UUID]) -> None:
    state = await hub.backplane.presence_query(ids)
    conn.send({"type": "presence.state", "users": {str(u): {"online": on} for u, on in state.items()}})


async def _h_presence_watch(conn: Connection, frame: dict[str, Any]) -> None:
    f: _UsersFrame = _validate(_UsersFrame, frame)
    ids = await _allowed_presence(conn, f.user_ids)
    for u in ids:
        if u not in conn.watching and len(conn.watching) < MAX_WATCHED_USERS:
            conn.watching.add(u)
            await hub.join(conn, presence_topic(u))
    await _presence_state(conn, ids)


async def _h_presence_unwatch(conn: Connection, frame: dict[str, Any]) -> None:
    f: _UsersFrame = _validate(_UsersFrame, frame)
    for u in f.user_ids:
        if u in conn.watching:
            conn.watching.discard(u)
            await hub.leave(conn, presence_topic(u))


async def _h_presence_query(conn: Connection, frame: dict[str, Any]) -> None:
    f: _UsersFrame = _validate(_UsersFrame, frame)
    await _presence_state(conn, await _allowed_presence(conn, f.user_ids))


_HANDLERS: dict[str, Callable[[Connection, dict[str, Any]], Awaitable[None]]] = {
    "ping": _h_ping,
    "message.send": _h_send,
    "typing.start": _h_typing_start,
    "typing.stop": _h_typing_stop,
    "message.read": _h_read,
    "presence.watch": _h_presence_watch,
    "presence.unwatch": _h_presence_unwatch,
    "presence.query": _h_presence_query,
}


async def _dispatch(conn: Connection, frame: dict[str, Any]) -> None:
    ftype = frame.get("type")
    ref = frame.get("client_id") if isinstance(frame.get("client_id"), str) else ftype
    handler = _HANDLERS.get(ftype) if isinstance(ftype, str) else None
    if handler is None:
        conn.error("unknown_type", f"Unknown frame type: {ftype!r}", ref)
        return
    try:
        await handler(conn, frame)
    except ChatError as exc:
        conn.error(exc.code, exc.message, ref)
        if exc.code == "rate_limited":
            conn.strike()
    except Exception:
        log.exception("chat handler %s crashed (user=%s)", ftype, conn.user_id)
        conn.error("internal_error", "Something went wrong", ref)


# =================================================================================================
# Socket loops
# =================================================================================================
async def _reader(conn: Connection) -> None:
    ws = conn.ws
    while True:
        try:
            raw = await asyncio.wait_for(ws.receive(), IDLE_TIMEOUT_S)
        except asyncio.TimeoutError:
            conn.request_close(CLOSE_TIMEOUT, "idle timeout")
            return
        if raw["type"] == "websocket.disconnect":
            return
        text = raw.get("text")
        if text is None:
            conn.error("unsupported_frame", "Only JSON text frames are supported")
            continue
        if len(text) > MAX_FRAME_BYTES:
            conn.request_close(1009, "frame too large")
            return

        now = time.monotonic()
        if not conn.frame_bucket.allow():
            conn.error("rate_limited", "Slow down")
            conn.strike()
            if conn.closed.is_set():
                return
            continue
        if conn.strikes and now - conn.last_strike > 30:
            conn.strikes = 0
        if now - conn.last_presence_touch > PRESENCE_TTL_S / 3:
            conn.last_presence_touch = now
            await hub.backplane.presence_touch(conn.user_id, conn.id)

        try:
            frame = json.loads(text)
        except ValueError:
            conn.error("invalid_json", "Frame is not valid JSON")
            continue
        if not isinstance(frame, dict):
            conn.error("invalid_frame", "Frame must be a JSON object")
            continue
        await _dispatch(conn, frame)


async def _writer(conn: Connection) -> None:
    while True:
        payload = await conn.queue.get()
        await asyncio.wait_for(conn.ws.send_text(payload), SEND_TIMEOUT_S)


async def _serve(conn: Connection) -> None:
    tasks = {
        asyncio.create_task(_reader(conn), name=f"chat-r-{conn.id[:6]}"),
        asyncio.create_task(_writer(conn), name=f"chat-w-{conn.id[:6]}"),
        asyncio.create_task(conn.closed.wait(), name=f"chat-c-{conn.id[:6]}"),
    }
    try:
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for t in done:
            exc = None if t.cancelled() else t.exception()
            if exc and not isinstance(exc, (RuntimeError, asyncio.TimeoutError, ConnectionError)):
                if type(exc).__name__ != "WebSocketDisconnect":
                    log.error("chat loop crashed (user=%s)", conn.user_id, exc_info=exc)
    finally:
        # Cancel without awaiting: awaiting here would itself be cancelled if this handler is being torn down.
        # (Exceptions of finished tasks were already retrieved above; cancelled ones carry none.)
        for t in tasks:
            t.cancel()
    if conn.close_code is not None:
        with contextlib.suppress(Exception):
            await conn.ws.close(code=conn.close_code, reason=conn.close_reason)


async def _cleanup(conn: Connection) -> None:
    with contextlib.suppress(Exception):
        await conn.typing.stop_all()
    try:
        await hub.unregister(conn)
    except Exception:
        log.exception("chat: unregister failed (user=%s)", conn.user_id)


# =================================================================================================
# WebSocket endpoint
# =================================================================================================
chat_router = APIRouter(tags=["Chat"])


def _extract_token(ws: WebSocket) -> tuple[str | None, str | None]:
    """Returns (token, subprotocol-to-echo)."""
    auth = ws.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip(), None
    protos = [p.strip() for p in ws.headers.get("sec-websocket-protocol", "").split(",") if p.strip()]
    if len(protos) >= 2 and protos[0].lower() == "bearer":
        return protos[1], "bearer"
    if tok := ws.query_params.get("token"):
        return tok, None
    return None, None


async def _await_auth_frame(ws: WebSocket) -> str:
    try:
        raw = await asyncio.wait_for(ws.receive_text(), AUTH_TIMEOUT_S)
        frame = json.loads(raw)
    except asyncio.TimeoutError:
        raise ChatError("auth_timeout", "No auth frame received", 401) from None
    except Exception:
        raise ChatError("unauthorized", "Expected an auth frame", 401) from None
    if not isinstance(frame, dict) or frame.get("type") != "auth" or not isinstance(frame.get("token"), str):
        raise ChatError("unauthorized", 'Expected {"type":"auth","token":"..."}', 401)
    return frame["token"]


async def _reject(ws: WebSocket, exc: ChatError) -> None:
    code = CLOSE_TIMEOUT if exc.code == "auth_timeout" else CLOSE_UNAUTHORIZED
    with contextlib.suppress(Exception):
        await ws.send_text(_dumps({"type": "error", "code": exc.code, "message": exc.message, "ref": "auth"}))
        await ws.close(code=code, reason=exc.code)


@chat_router.websocket("/chat/ws")
async def chat_ws(ws: WebSocket) -> None:
    await hub.ensure_started()
    token, subprotocol = _extract_token(ws)
    await ws.accept(subprotocol=subprotocol)  # accept first so rejections carry a close code + reason

    try:
        if token is None:
            token = await _await_auth_frame(ws)
        user = await run_in_threadpool(_authenticate_sync, token)
    except ChatError as exc:
        await _reject(ws, exc)
        return
    except Exception:
        log.exception("chat auth failed unexpectedly")
        await _reject(ws, ChatError("internal_error", "Authentication unavailable", 500))
        return

    conn = Connection(ws, user)
    await hub.register(conn)
    conn.send(
        {
            "type": "ready",
            "protocol": PROTOCOL_VERSION,
            "connection_id": conn.id,
            "user": {"id": conn.user_id, "display_name": conn.display_name, "role": conn.role},
            "server_time": _iso(utcnow()),
            "heartbeat": {"ping_interval_s": HEARTBEAT_INTERVAL_S, "idle_timeout_s": IDLE_TIMEOUT_S},
            "limits": {"max_frame_bytes": MAX_FRAME_BYTES, "max_body_chars": 2000, "max_watched_users": MAX_WATCHED_USERS},
        }
    )
    try:
        await _serve(conn)
    finally:
        # Detached task: if this handler is being cancelled (server shutdown, proxy teardown) any ``await``
        # here would be cancelled too and the connection would leak in the registry / presence.
        hub.spawn(_cleanup(conn))


# =================================================================================================
# REST companions
# =================================================================================================
class ChatHistoryOut(BaseModel):
    items: list[MessageOut]  # chronological (oldest first) within the page
    next_cursor: str | None  # pass as ?before= to fetch the next (older) page
    my_last_read_at: datetime | None  # what I have read from the peer
    peer_last_read_at: datetime | None  # what the peer has read from me -> "seen" ticks


class UnreadItem(BaseModel):
    listing_id: uuid.UUID
    peer_id: uuid.UUID
    unread: int


class UnreadOut(BaseModel):
    total: int
    conversations: list[UnreadItem]


class ReadIn(BaseModel):
    listing_id: uuid.UUID
    peer_id: uuid.UUID


class ReadOut(BaseModel):
    read_at: datetime


@contextmanager
def _http_errors():
    try:
        yield
    except ChatError as exc:
        raise HTTPException(exc.status, exc.message) from None


def _encode_cursor(m: Message) -> str:
    return base64.urlsafe_b64encode(f"{m.created_at.isoformat()}|{m.id}".encode()).decode()


def _decode_cursor(cursor: str) -> tuple[datetime, uuid.UUID]:
    try:
        ts, mid = base64.urlsafe_b64decode(cursor.encode()).decode().split("|", 1)
        return datetime.fromisoformat(ts), uuid.UUID(mid)
    except (ValueError, binascii.Error, UnicodeDecodeError):
        raise HTTPException(400, "Invalid cursor") from None


@chat_router.get("/chat/history", response_model=ChatHistoryOut)
def chat_history(
    db: DB,
    user: CurrentUser,
    listing_id: uuid.UUID,
    peer_id: uuid.UUID,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    before: str | None = None,
):
    """One conversation (me <-> peer, on one listing), newest page first, keyset-paginated."""
    with _http_errors():
        s_db, listing, _me, peer = _load_pair(db, listing_id, user.id, peer_id, require_open=False)

    pair = or_(
        and_(Message.sender_id == user.id, Message.recipient_id == peer_id),
        and_(Message.sender_id == peer_id, Message.recipient_id == user.id),
    )
    stmt = select(Message).where(Message.listing_id == listing_id, pair)
    if before:
        ts, mid = _decode_cursor(before)
        stmt = stmt.where(
            tuple_(Message.created_at, Message.id)
            < tuple_(literal(ts, DateTime(timezone=True)), literal(mid, Uuid))
        )
    rows = list(s_db.scalars(stmt.order_by(Message.created_at.desc(), Message.id.desc()).limit(limit + 1)))
    has_more = len(rows) > limit
    rows = rows[:limit]

    def watermark(reader: uuid.UUID, other: uuid.UUID) -> datetime | None:
        return s_db.scalar(
            select(ChatReadState.last_read_at).where(
                ChatReadState.listing_id == listing_id,
                ChatReadState.reader_id == reader,
                ChatReadState.peer_id == other,
            )
        )

    names = {user.id: user.display_name, peer.id: peer.display_name}
    items = [
        MessageOut(
            id=m.id,
            listing_id=m.listing_id,
            listing_title=listing.title,
            sender_id=m.sender_id,
            sender_name=names.get(m.sender_id, ""),
            recipient_id=m.recipient_id,
            recipient_name=names.get(m.recipient_id, ""),
            body=m.body,
            created_at=m.created_at,
        )
        for m in reversed(rows)
    ]
    return ChatHistoryOut(
        items=items,
        next_cursor=_encode_cursor(rows[-1]) if has_more and rows else None,
        my_last_read_at=watermark(user.id, peer_id),
        peer_last_read_at=watermark(peer_id, user.id),
    )


@chat_router.get("/chat/unread", response_model=UnreadOut)
def chat_unread(user: CurrentUser):
    """Unread message counts per (listing, peer) across all shards."""

    def probe(s_db: Any, _sid: Any) -> list[tuple[uuid.UUID, uuid.UUID, int]]:
        stmt = (
            select(Message.listing_id, Message.sender_id, func.count(Message.id))
            .outerjoin(
                ChatReadState,
                and_(
                    ChatReadState.listing_id == Message.listing_id,
                    ChatReadState.reader_id == Message.recipient_id,
                    ChatReadState.peer_id == Message.sender_id,
                ),
            )
            .where(
                Message.recipient_id == user.id,
                or_(ChatReadState.last_read_at.is_(None), Message.created_at > ChatReadState.last_read_at),
            )
            .group_by(Message.listing_id, Message.sender_id)
        )
        return [(lid, sid, int(n)) for lid, sid, n in s_db.execute(stmt)]

    convs = [UnreadItem(listing_id=l, peer_id=p, unread=n) for part in get_router().scatter(probe).values() for l, p, n in part]
    convs.sort(key=lambda c: -c.unread)
    return UnreadOut(total=sum(c.unread for c in convs), conversations=convs)


@chat_router.post("/chat/read", response_model=ReadOut)
def chat_mark_read(payload: ReadIn, user: CurrentUser):
    """HTTP twin of the ``message.read`` frame (e.g. when the socket is not connected)."""
    with _http_errors():
        read_at = _mark_read_sync(user.id, payload.listing_id, payload.peer_id)
    event = {
        "type": "read",
        "listing_id": payload.listing_id,
        "reader_id": user.id,
        "peer_id": payload.peer_id,
        "read_at": read_at,
    }
    hub.publish_threadsafe(user_topic(payload.peer_id), event)
    hub.publish_threadsafe(user_topic(user.id), event)
    return ReadOut(read_at=read_at)
