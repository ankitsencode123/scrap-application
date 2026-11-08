"""Tests for ScrapMart Map System (/api/v1/map)."""

import io
import uuid
from PIL import Image
from tests.conftest import API


def _signup(client, role, name):
    email = f"{name}-{uuid.uuid4().hex[:6]}@example.com"
    r = client.post(
        f"{API}/auth/signup",
        json={"email": email, "password": "password123", "display_name": name, "role": role},
    )
    assert r.status_code == 201, r.text
    d = r.json()
    return d["user"], {"Authorization": f"Bearer {d['access_token']}"}, d["access_token"]


def _jpeg():
    buf = io.BytesIO()
    Image.new("RGB", (64, 64), (10, 200, 10)).save(buf, "PNG")
    return buf.getvalue()


def _upload_img(client, headers):
    r = client.post(f"{API}/uploads/images", headers=headers, files=[("files", ("test.png", _jpeg(), "image/png"))])
    assert r.status_code == 201, r.text
    return r.json()[0]["url"]


def test_map_health_and_strategy(client):
    seller, sh, _ = _signup(client, "seller", "SellerMap1")

    # 1. Health endpoint
    r = client.get(f"{API}/map/health", headers=sh)
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["store"] == "memory"
    assert data["bus"] == "local"
    assert data["store_ok"] is True

    # 2. Location strategy endpoint
    r = client.get(f"{API}/map/location/strategy", headers=sh)
    assert r.status_code == 200, r.text
    strategy = r.json()
    assert len(strategy["ladder"]) == 3
    assert strategy["ladder"][0]["name"] == "cached"
    assert strategy["ladder"][1]["name"] == "network"
    assert strategy["ladder"][2]["name"] == "gps"
    assert "default_center" in strategy


def test_location_resolve_position_timeout_cure(client):
    seller, sh, _ = _signup(client, "seller", "SellerMap2")

    # Test error code 3: position timeout - endpoint MUST succeed and return fallback
    r = client.post(
        f"{API}/map/location/resolve",
        headers=sh,
        json={"client_error": {"code": 3, "message": "Position acquisition timed out"}},
    )
    assert r.status_code == 200, r.text
    res = r.json()
    assert res["lat"] is not None
    assert res["lng"] is not None
    assert res["accuracy_m"] > 0
    assert "zoom" in res
    assert len(res["bounds"]) == 4
    assert res["hint"] is not None
    assert len(res["candidates"]) >= 1

    # Test valid fix resolution
    r = client.post(
        f"{API}/map/location/resolve",
        headers=sh,
        json={"fix": {"lat": 22.58, "lng": 88.42, "accuracy_m": 15.0, "source": "gps"}},
    )
    assert r.status_code == 200, r.text
    res = r.json()
    assert abs(res["lat"] - 22.58) < 0.01
    assert abs(res["lng"] - 88.42) < 0.01
    assert res["source"] == "gps"


def test_location_fix_ingestion_and_validation(client):
    collector, ch, _ = _signup(client, "collector", "CollectorMap1")

    # Rejection of Null Island (0, 0)
    r = client.post(
        f"{API}/map/location/fix",
        headers=ch,
        json={"lat": 0.0, "lng": 0.0, "source": "gps"},
    )
    assert r.status_code == 200
    assert r.json()["accepted"] is False
    assert r.json()["reason"] == "null_island"

    # Rejection of server-only source submitted by client
    r = client.post(
        f"{API}/map/location/fix",
        headers=ch,
        json={"lat": 22.57, "lng": 88.36, "source": "ip"},
    )
    assert r.status_code == 200
    assert r.json()["accepted"] is False
    assert r.json()["reason"] == "low_quality_source"

    # Accepted valid fix
    r = client.post(
        f"{API}/map/location/fix",
        headers=ch,
        json={"lat": 22.5726, "lng": 88.3639, "accuracy_m": 20.0, "source": "gps"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["accepted"] is True
    assert r.json()["quality"] == "good"


def test_collector_presence_and_nearby(client):
    seller, sh, _ = _signup(client, "seller", "SellerNear")
    col, ch, _ = _signup(client, "collector", "ColNear")

    # Collector posts fix
    client.post(
        f"{API}/map/location/fix",
        headers=ch,
        json={"lat": 22.5726, "lng": 88.3639, "accuracy_m": 15.0, "source": "gps"},
    )

    # Collector goes online
    r = client.post(f"{API}/map/presence", headers=ch, json={"online": True})
    assert r.status_code == 200 and r.json()["online"] is True

    # Seller queries collectors nearby
    r = client.get(
        f"{API}/map/collectors/nearby",
        headers=sh,
        params={"lat": 22.57, "lng": 88.36, "radius_km": 15},
    )
    assert r.status_code == 200, r.text
    nearby = r.json()
    assert len(nearby) >= 1
    hit = next((c for c in nearby if c["id"] == col["id"]), None)
    assert hit is not None
    assert hit["display_name"] == "ColNear"
    assert hit["distance_km"] < 10.0

    # Collector goes offline
    r = client.post(f"{API}/map/presence", headers=ch, json={"online": False})
    assert r.status_code == 200 and r.json()["online"] is False


def test_map_listings_and_heatmap(client):
    seller, sh, _ = _signup(client, "seller", "SellerFeed")

    # Create listing with coordinates
    img_url = _upload_img(client, sh)
    body = {
        "title": "Old books & papers",
        "category": "paper_cardboard",
        "weight_kg": 20.0,
        "expected_price": 400,
        "address": "Park Circus, Kolkata",
        "latitude": 22.54,
        "longitude": 88.37,
        "image_urls": [img_url],
    }
    r = client.post(f"{API}/listings", headers=sh, json=body)
    assert r.status_code == 201, r.text
    lid = r.json()["id"]

    # Query bounding box map listings
    r = client.get(
        f"{API}/map/listings",
        headers=sh,
        params={"south": 22.4, "west": 88.2, "north": 22.7, "east": 88.5, "zoom": 14},
    )
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["total_in_view"] >= 1
    found = any(item["id"] == lid for item in out["items"])
    assert found is True

    # Query heatmap
    r = client.get(
        f"{API}/map/heatmap",
        headers=sh,
        params={"south": 22.4, "west": 88.2, "north": 22.7, "east": 88.5, "kind": "open"},
    )
    assert r.status_code == 200, r.text
    heat = r.json()
    assert heat["kind"] == "open"
    assert len(heat["cells"]) >= 1


def test_map_tracking_flow(client):
    seller, sh, _ = _signup(client, "seller", "SellerTrack")
    col, ch, _ = _signup(client, "collector", "ColTrack")

    # 1. Create listing
    track_img = _upload_img(client, sh)
    r = client.post(
        f"{API}/listings",
        headers=sh,
        json={
            "title": "Metals scrap",
            "category": "metals",
            "weight_kg": 30.0,
            "expected_price": 1500,
            "address": "Ballygunge, Kolkata",
            "latitude": 22.52,
            "longitude": 88.36,
            "image_urls": [track_img],
        },
    )
    assert r.status_code == 201, r.text
    lid = r.json()["id"]

    # Tracking before offer accepted
    r = client.get(f"{API}/map/tracking/{lid}", headers=sh)
    assert r.status_code == 200
    assert r.json()["tracking_active"] is False

    # 2. Bid and accept
    b = client.post(f"{API}/listings/{lid}/bids", headers=ch, json={"amount": 1400})
    assert b.status_code == 201
    bid_id = b.json()["id"]
    client.post(f"{API}/listings/{lid}/bids/{bid_id}/accept", headers=sh)

    # 3. Advance to on_the_way
    client.post(f"{API}/listings/{lid}/advance", headers=ch)

    # Collector posts live fix
    client.post(
        f"{API}/map/location/fix",
        headers=ch,
        json={"lat": 22.53, "lng": 88.36, "accuracy_m": 12.0, "source": "gps", "speed_mps": 5.0, "listing_id": lid},
    )

    # Query tracking snapshot
    r = client.get(f"{API}/map/tracking/{lid}", headers=sh, params={"include_route": "true"})
    assert r.status_code == 200, r.text
    track = r.json()
    assert track["status"] == "on_the_way"
    assert track["tracking_active"] is True
    assert track["collector"] is not None
    assert track["collector"]["live"] is True
    assert track["distance_m"] is not None
    assert track["eta_s"] is not None


def test_map_websocket_hub(client):
    _, _, token = _signup(client, "collector", "ColWs")

    with client.websocket_connect(f"{API}/map/ws") as ws:
        # First frame must be auth
        ws.send_json({"type": "auth", "token": token})
        ready = ws.receive_json()
        assert ready["type"] == "ready"
        assert ready["role"] == "collector"

        # Ping-pong
        ws.send_json({"type": "ping"})
        pong = ws.receive_json()
        assert pong["type"] == "pong"

        # Subscribe area
        ws.send_json({"type": "subscribe_area", "bbox": [22.4, 88.2, 22.7, 88.5]})
        area_ack = ws.receive_json()
        assert area_ack["type"] == "area_ok"
        assert area_ack["cells"] > 0

        # Send location ping over websocket with ack
        ws.send_json({
            "type": "location",
            "lat": 22.57,
            "lng": 88.36,
            "accuracy_m": 25.0,
            "source": "gps",
            "ack": True,
        })
        ack = ws.receive_json()
        assert ack["type"] == "fix_ack"
        assert ack["accepted"] is True
