"""Centralized Redis connection, pooling, failover, and caching utilities."""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any

from app.core.config import normalize_redis_url, settings

log = logging.getLogger("scrapmart.redis")

_sync_pool: Any = None
_sync_client: Any = None
_async_client: Any = None


def _resolve_url(env_name: str, fallback: str = "") -> str:
    val = os.getenv(env_name) or getattr(settings, env_name, "") or fallback
    return normalize_redis_url(val)


def get_sync_redis():
    """Return a shared sync Redis client with connection pooling and failover."""
    global _sync_client
    if _sync_client is not None:
        return _sync_client

    import redis

    primary_url = _resolve_url("REDIS_CACHE_URL", os.getenv("REDIS_URL") or settings.REDIS_URL or settings.MAP_REDIS_URL)
    fallback_url = _resolve_url("REDIS_FALLBACK_URL", settings.REDIS_FALLBACK_URL)

    if not primary_url:
        return None

    try:
        client = redis.from_url(
            primary_url,
            decode_responses=True,
            socket_timeout=3.0,
            socket_connect_timeout=3.0,
            health_check_interval=30,
            retry_on_timeout=True,
        )
        client.ping()
        _sync_client = client
        log.info("Connected to primary Redis cache")
        return _sync_client
    except Exception as exc:
        log.warning("Primary Redis cache connection failed (%s)", exc)
        if fallback_url:
            try:
                client = redis.from_url(
                    fallback_url,
                    decode_responses=True,
                    socket_timeout=3.0,
                    socket_connect_timeout=3.0,
                    health_check_interval=30,
                    retry_on_timeout=True,
                )
                client.ping()
                _sync_client = client
                log.info("Fell back to secondary Redis cache")
                return _sync_client
            except Exception as fb_exc:
                log.warning("Fallback Redis cache connection failed (%s)", fb_exc)
    return None


def get_async_redis():
    """Return a shared async Redis client with failover."""
    global _async_client
    if _async_client is not None:
        return _async_client

    import redis.asyncio as aioredis

    primary_url = settings.REDIS_CACHE_URL or settings.REDIS_URL or settings.MAP_REDIS_URL
    if not primary_url:
        return None

    try:
        _async_client = aioredis.from_url(
            primary_url,
            decode_responses=True,
            socket_timeout=3.0,
            socket_connect_timeout=3.0,
            health_check_interval=30,
            retry_on_timeout=True,
        )
        return _async_client
    except Exception as exc:
        log.warning("Async Redis initialization failed (%s)", exc)
        return None


def cache_get(key: str) -> Any | None:
    """Retrieve and deserialize a JSON cached value. Returns None on miss or error."""
    try:
        r = get_sync_redis()
        if not r:
            return None
        raw = r.get(key)
        if raw is not None:
            return json.loads(raw)
    except Exception as exc:
        log.warning("Redis cache_get failed for key '%s': %s", key, exc)
    return None


def cache_set(key: str, value: Any, ttl: int = 300) -> bool:
    """Serialize and store a JSON value with TTL. Returns False on error."""
    try:
        r = get_sync_redis()
        if not r:
            return False
        r.set(key, json.dumps(value, default=str), ex=ttl)
        return True
    except Exception as exc:
        log.warning("Redis cache_set failed for key '%s': %s", key, exc)
        return False


def cache_delete(key: str) -> bool:
    """Delete a key from Redis. Returns True if deleted or False on error/missing."""
    try:
        r = get_sync_redis()
        if not r:
            return False
        return bool(r.delete(key))
    except Exception as exc:
        log.warning("Redis cache_delete failed for key '%s': %s", key, exc)
        return False


def cache_delete_pattern(pattern: str) -> int:
    """Delete keys matching pattern using SCAN to avoid blocking Redis."""
    try:
        r = get_sync_redis()
        if not r:
            return 0
        deleted = 0
        cursor = "0"
        while cursor != 0:
            cursor, keys = r.scan(cursor=cursor, match=pattern, count=100)
            if keys:
                deleted += r.delete(*keys)
        return deleted
    except Exception as exc:
        log.warning("Redis cache_delete_pattern failed for '%s': %s", pattern, exc)
        return 0


def get_redis_health() -> dict[str, Any]:
    """Inspect and report health of configured Redis instances."""
    import redis

    configs = [
        ("map_redis", _resolve_url("MAP_REDIS_URL", settings.MAP_REDIS_URL or settings.REDIS_URL)),
        ("app_redis", _resolve_url("REDIS_CACHE_URL", os.getenv("REDIS_URL") or settings.REDIS_CACHE_URL or settings.REDIS_URL)),
        ("fallback_redis", _resolve_url("REDIS_FALLBACK_URL", settings.REDIS_FALLBACK_URL)),
    ]
    instances: list[dict[str, Any]] = []
    seen_urls = set()
    for name, raw_url in configs:
        url = normalize_redis_url(raw_url)
        if not url or url in seen_urls:
            continue
        seen_urls.add(url)

        # Mask password in URL for display
        masked_host = url.split("@")[-1] if "@" in url else url
        t0 = time.monotonic()
        try:
            r = redis.from_url(url, socket_timeout=2.0, socket_connect_timeout=2.0)
            ok = bool(r.ping())
            latency_ms = round((time.monotonic() - t0) * 1000, 2)
            info = r.info("server")
            version = info.get("redis_version", "unknown")
            instances.append({
                "name": name,
                "endpoint": masked_host,
                "status": "online" if ok else "unresponsive",
                "latency_ms": latency_ms,
                "version": version,
                "tls": url.startswith("rediss://"),
            })
            r.close()
        except Exception as exc:
            latency_ms = round((time.monotonic() - t0) * 1000, 2)
            instances.append({
                "name": name,
                "endpoint": masked_host,
                "status": "error",
                "error": str(exc),
                "latency_ms": latency_ms,
                "tls": url.startswith("rediss://"),
            })

    return {
        "configured_instances": len(instances),
        "online_instances": sum(1 for i in instances if i.get("status") == "online"),
        "instances": instances,
    }
