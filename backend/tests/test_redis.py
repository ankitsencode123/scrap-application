"""Tests for Redis integration: URL normalization, caching, health, MapStore, and PubSub."""

import asyncio
import os
import time
import pytest

from app.core.config import normalize_redis_url
from app.core.redis_client import cache_delete, cache_get, cache_set, get_redis_health
from app.api.v1.map_system import Event, Hub, RedisBus, RedisStore


UPSTASH_URL1 = "rediss://default:gQAAAAAAApKoAAIgcDEwMWFhNDNjZTcyMDg0MGMwOTY3N2U0YzU4NjVmOWZlZg@genuine-cicada-168616.upstash.io:6379"
UPSTASH_URL2 = "rediss://default:gQAAAAAAAvCQAAIgcDFlMTVlOTk5YWQzYWU0MTFjODBjMjcxYWJlYmFjODc1OQ@massive-mantis-192656.upstash.io:6379"


def test_normalize_redis_url():
    # 1. Plain upstash url converted to rediss
    url = "redis://default:token@genuine-cicada-168616.upstash.io:6379"
    assert normalize_redis_url(url) == "rediss://default:token@genuine-cicada-168616.upstash.io:6379"

    # 2. redis-cli format handled
    cli_str = "redis-cli --tls -u redis://default:token@genuine-cicada-168616.upstash.io:6379"
    assert normalize_redis_url(cli_str) == "rediss://default:token@genuine-cicada-168616.upstash.io:6379"

    # 3. rediss scheme preserved
    assert normalize_redis_url(UPSTASH_URL1) == UPSTASH_URL1

    # 4. Empty/None handled
    assert normalize_redis_url(None) == ""
    assert normalize_redis_url("") == ""


def test_redis_cache_operations(monkeypatch):
    monkeypatch.setenv("REDIS_URL", UPSTASH_URL2)
    monkeypatch.setenv("REDIS_CACHE_URL", UPSTASH_URL2)

    # Force re-init client with test url
    from app.core import redis_client
    redis_client._sync_client = None

    test_key = f"test:cache:{int(time.time())}"
    test_val = {"message": "hello redis", "number": 42, "nested": {"a": [1, 2, 3]}}

    # Set
    ok = cache_set(test_key, test_val, ttl=30)
    assert ok is True

    # Get
    cached = cache_get(test_key)
    assert cached == test_val

    # Delete
    deleted = cache_delete(test_key)
    assert deleted is True

    # Verify gone
    assert cache_get(test_key) is None


def test_redis_health_diagnostic(monkeypatch):
    monkeypatch.setenv("MAP_REDIS_URL", UPSTASH_URL1)
    monkeypatch.setenv("REDIS_URL", UPSTASH_URL2)

    health = get_redis_health()
    assert health["configured_instances"] >= 1
    assert health["online_instances"] >= 1
    for inst in health["instances"]:
        if inst["status"] == "online":
            assert inst["latency_ms"] > 0
            assert inst["tls"] is True


@pytest.mark.anyio
async def test_redis_store_location_and_geo():
    store = RedisStore(UPSTASH_URL1)
    try:
        assert await store.ping() is True

        user_id = f"test-col-{int(time.time())}"
        lat, lng = 22.5726, 88.3639

        # Test fix
        await store.put_fix(user_id, {"lat": lat, "lng": lng, "acc": 12.0}, ttl=60)
        fix = await store.get_fix(user_id)
        assert fix is not None
        assert fix["lat"] == lat

        # Test presence & geo search
        now = time.time()
        await store.presence_set(user_id, lat, lng, now)
        nearby = await store.presence_search(lat, lng, radius_km=10.0, limit=5, max_age=300)
        assert any(n[0] == user_id for n in nearby)

        # Test presence removal
        await store.presence_remove(user_id)
        nearby_after = await store.presence_search(lat, lng, radius_km=10.0, limit=5, max_age=300)
        assert not any(n[0] == user_id for n in nearby_after)

        # Test trail
        pickup_key = f"test-trail-{int(time.time())}"
        await store.trail_push(pickup_key, [lat, lng, int(now)], maxlen=50, ttl=60)
        trail = await store.trail_get(pickup_key, n=10)
        assert len(trail) >= 1
        assert trail[-1][0] == lat

        # Cleanup
        await store.r.delete(f"mp:fix:{user_id}", f"mp:trail:{pickup_key}")
    finally:
        await store.close()


@pytest.mark.anyio
async def test_redis_bus_pubsub():
    hub = Hub()
    channel = f"test:bus:{int(time.time())}"
    bus = RedisBus(UPSTASH_URL1, hub, channel=channel)
    await bus.start()

    try:
        ev = Event(topic="test:topic", type="test.event", data={"foo": "bar"})
        received = []

        # Hook into hub.dispatch
        original_dispatch = hub.dispatch
        async def mock_dispatch(event):
            received.append(event)
            await original_dispatch(event)
        hub.dispatch = mock_dispatch

        # Publish
        await bus.publish(ev)

        # Wait for pubsub message consumption
        for _ in range(30):
            if received:
                break
            await asyncio.sleep(0.1)

        assert len(received) >= 1
        assert received[0].type == "test.event"
        assert received[0].data == {"foo": "bar"}
    finally:
        await bus.stop()


def test_redis_graceful_degradation():
    # Invalid url should not crash cache_get or cache_set
    from app.core import redis_client
    redis_client._sync_client = None

    import redis
    # Intentionally point to an unreachable port with immediate timeout
    bad_client = redis.Redis(host="127.0.0.1", port=65432, socket_timeout=0.2, socket_connect_timeout=0.2)
    redis_client._sync_client = bad_client

    # Verify graceful degradation to None / False without raising
    assert cache_get("some:key") is None
    assert cache_set("some:key", {"a": 1}) is False
    assert cache_delete("some:key") is False

    # Reset
    redis_client._sync_client = None
