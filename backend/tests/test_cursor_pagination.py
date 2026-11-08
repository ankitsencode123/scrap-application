"""Comprehensive integration tests for cursor (keyset) pagination."""

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
    return d["user"], {"Authorization": f"Bearer {d['access_token']}"}, email


def _png():
    buf = io.BytesIO()
    Image.new("RGB", (32, 32), (50, 150, 50)).save(buf, "PNG")
    return buf.getvalue()


def test_cursor_pagination_listings(client):
    seller, sh, _ = _signup(client, "seller", "CursorSeller")
    col, ch, _ = _signup(client, "collector", "CursorCollector")

    # Upload an image
    r = client.post(f"{API}/uploads/images", headers=sh, files=[("files", ("test.png", _png(), "image/png"))])
    assert r.status_code == 201, r.text
    img_url = r.json()[0]["url"]

    # Create 5 listings with distinct prices and weights
    created_ids = []
    prices = [100.0, 250.0, 300.0, 450.0, 500.0]
    weights = [10.0, 20.0, 30.0, 40.0, 50.0]
    for idx, (p, w) in enumerate(zip(prices, weights)):
        body = {
            "title": f"Scrap Item {idx}",
            "category": "metals",
            "weight_kg": w,
            "expected_price": p,
            "address": f"{10 + idx} Park Street, Kolkata",
            "latitude": 22.55 + idx * 0.01,
            "longitude": 88.35 + idx * 0.01,
            "image_urls": [img_url],
        }
        res = client.post(f"{API}/listings", headers=sh, json=body)
        assert res.status_code == 201, res.text
        created_ids.append(res.json()["id"])

    # 1. Page 1: limit=2, newest
    p1 = client.get(f"{API}/listings", headers=ch, params={"tab": "open", "limit": 2, "sort": "newest"})
    assert p1.status_code == 200, p1.text
    data1 = p1.json()
    assert len(data1["items"]) == 2
    assert data1["has_more"] is True
    assert data1["next_cursor"] is not None
    assert data1["total"] >= 5
    page1_ids = [item["id"] for item in data1["items"]]
    # Newest listings first (most recently created)
    assert page1_ids == [created_ids[4], created_ids[3]]

    # 2. Page 2: limit=2 using cursor
    cursor1 = data1["next_cursor"]
    p2 = client.get(f"{API}/listings", headers=ch, params={"tab": "open", "limit": 2, "sort": "newest", "cursor": cursor1})
    assert p2.status_code == 200, p2.text
    data2 = p2.json()
    assert len(data2["items"]) == 2
    assert data2["has_more"] is True
    assert data2["next_cursor"] is not None
    assert data2["total"] == data1["total"]  # total carried over without count(*)
    page2_ids = [item["id"] for item in data2["items"]]
    assert page2_ids == [created_ids[2], created_ids[1]]
    assert set(page1_ids).isdisjoint(set(page2_ids))

    # 3. Page 3: limit=2 using cursor (last page)
    cursor2 = data2["next_cursor"]
    p3 = client.get(f"{API}/listings", headers=ch, params={"tab": "open", "limit": 2, "sort": "newest", "cursor": cursor2})
    assert p3.status_code == 200, p3.text
    data3 = p3.json()
    # Should have remaining 1 item
    assert len(data3["items"]) >= 1
    page3_ids = [item["id"] for item in data3["items"]]
    assert created_ids[0] in page3_ids


def test_cursor_pagination_sorts(client):
    seller, sh, _ = _signup(client, "seller", "SortSeller")
    col, ch, _ = _signup(client, "collector", "SortCollector")

    r = client.post(f"{API}/uploads/images", headers=sh, files=[("files", ("test.png", _png(), "image/png"))])
    img_url = r.json()[0]["url"]

    # Create 3 items with specific prices: 50, 150, 350
    for p, w in [(50.0, 5.0), (150.0, 15.0), (350.0, 35.0)]:
        client.post(
            f"{API}/listings",
            headers=sh,
            json={
                "title": f"Item price {p}",
                "category": "paper_cardboard",
                "weight_kg": w,
                "expected_price": p,
                "address": "Salt Lake, Kolkata",
                "latitude": 22.58,
                "longitude": 88.41,
                "image_urls": [img_url],
            },
        )

    # Sort price_high with pagination
    p1 = client.get(
        f"{API}/listings",
        headers=ch,
        params={"tab": "open", "category": "paper_cardboard", "sort": "price_high", "limit": 2},
    )
    assert p1.status_code == 200
    d1 = p1.json()
    assert len(d1["items"]) == 2
    assert d1["items"][0]["expected_price"] >= d1["items"][1]["expected_price"]
    assert d1["next_cursor"] is not None

    p2 = client.get(
        f"{API}/listings",
        headers=ch,
        params={"tab": "open", "category": "paper_cardboard", "sort": "price_high", "limit": 2, "cursor": d1["next_cursor"]},
    )
    assert p2.status_code == 200
    d2 = p2.json()
    assert len(d2["items"]) == 1
    assert d2["items"][0]["expected_price"] <= d1["items"][1]["expected_price"]

    # Sort price_low with pagination
    low1 = client.get(
        f"{API}/listings",
        headers=ch,
        params={"tab": "open", "category": "paper_cardboard", "sort": "price_low", "limit": 2},
    )
    assert low1.status_code == 200
    dl1 = low1.json()
    assert dl1["items"][0]["expected_price"] <= dl1["items"][1]["expected_price"]

    # Nearest sort
    near1 = client.get(
        f"{API}/listings",
        headers=ch,
        params={"tab": "open", "category": "paper_cardboard", "sort": "nearest", "lat": 22.58, "lng": 88.41, "limit": 2},
    )
    assert near1.status_code == 200
    dn1 = near1.json()
    assert len(dn1["items"]) == 2
    assert dn1["items"][0]["distance_km"] is not None


def test_cursor_security_and_tampering(client):
    seller, sh, _ = _signup(client, "seller", "TamperSeller")
    col, ch, _ = _signup(client, "collector", "TamperCollector")

    r = client.post(f"{API}/uploads/images", headers=sh, files=[("files", ("test.png", _png(), "image/png"))])
    img_url = r.json()[0]["url"]

    for idx in range(3):
        client.post(
            f"{API}/listings",
            headers=sh,
            json={
                "title": f"Item {idx}",
                "category": "glass",
                "weight_kg": 5.0,
                "expected_price": 50.0,
                "address": "Howrah, Kolkata",
                "latitude": 22.59,
                "longitude": 88.30,
                "image_urls": [img_url],
            },
        )

    res = client.get(f"{API}/listings", headers=ch, params={"tab": "open", "category": "glass", "limit": 1})
    assert res.status_code == 200
    cursor = res.json()["next_cursor"]
    assert cursor is not None

    # 1. Tampered signature -> 400
    tampered = cursor[:-3] + ("XYZ" if not cursor.endswith("XYZ") else "ABC")
    r_bad = client.get(f"{API}/listings", headers=ch, params={"tab": "open", "category": "glass", "cursor": tampered})
    assert r_bad.status_code == 400
    assert r_bad.json()["code"] == "invalid_cursor"

    # 2. Changed filter/fingerprint (category mismatch) -> 400
    r_diff = client.get(f"{API}/listings", headers=ch, params={"tab": "open", "category": "metals", "cursor": cursor})
    assert r_diff.status_code == 400
    assert r_diff.json()["code"] == "invalid_cursor"

    # 3. Changed sort with same cursor -> 400
    r_sort = client.get(f"{API}/listings", headers=ch, params={"tab": "open", "category": "glass", "sort": "price_high", "cursor": cursor})
    assert r_sort.status_code == 400
    assert r_sort.json()["code"] == "invalid_cursor"

    # 4. Garbage cursor -> 400
    r_garbage = client.get(f"{API}/listings", headers=ch, params={"tab": "open", "cursor": "invalid.token.payload"})
    assert r_garbage.status_code == 400
    assert r_garbage.json()["code"] == "invalid_cursor"


def test_cursor_bids_and_reviews(client):
    seller, sh, _ = _signup(client, "seller", "BidRevSeller")
    col, ch, _ = _signup(client, "collector", "BidRevCollector")

    r = client.post(f"{API}/uploads/images", headers=sh, files=[("files", ("test.png", _png(), "image/png"))])
    img_url = r.json()[0]["url"]

    # Create listing & bid
    res = client.post(
        f"{API}/listings",
        headers=sh,
        json={
            "title": "Old brass tap",
            "category": "metals",
            "weight_kg": 2.0,
            "expected_price": 400.0,
            "address": "MG Road, Kolkata",
            "latitude": 22.57,
            "longitude": 88.36,
            "image_urls": [img_url],
        },
    )
    lid = res.json()["id"]

    # Place bid
    b_res = client.post(f"{API}/listings/{lid}/bids", headers=ch, json={"amount": 350.0, "note": "Cash"})
    assert b_res.status_code == 201

    # Check bids/mine cursor pagination
    my_bids = client.get(f"{API}/bids/mine", headers=ch, params={"limit": 5})
    assert my_bids.status_code == 200
    mb_data = my_bids.json()
    assert mb_data["total"] >= 1
    assert len(mb_data["items"]) >= 1
    assert "next_cursor" in mb_data
    assert "has_more" in mb_data

    # Accept & complete to write review
    bid_id = b_res.json()["id"]
    client.post(f"{API}/listings/{lid}/bids/{bid_id}/accept", headers=sh)
    for _ in ("on_the_way", "arrived", "completed"):
        client.post(f"{API}/listings/{lid}/advance", headers=ch)

    # Post review
    client.post(f"{API}/listings/{lid}/reviews", headers=ch, json={"rating": 5, "comment": "Excellent"})

    # Check reviews cursor pagination
    revs = client.get(f"{API}/users/me/reviews", headers=sh, params={"limit": 5})
    assert revs.status_code == 200
    r_data = revs.json()
    assert r_data["total"] >= 1
    assert len(r_data["items"]) >= 1
    assert "next_cursor" in r_data
