"""End-to-end happy path: signup -> upload -> list -> bid -> accept -> tracker -> reviews -> stats -> chat."""

import io
import uuid

from PIL import Image

from tests.conftest import API


def _signup(client, role, name):
    email = f"{name}-{uuid.uuid4().hex[:6]}@example.com"
    r = client.post(f"{API}/auth/signup", json={"email": email, "password": "password123", "display_name": name, "role": role})
    assert r.status_code == 201, r.text
    d = r.json()
    return d["user"], {"Authorization": f"Bearer {d['access_token']}"}, email


def _jpeg():
    buf = io.BytesIO()
    Image.new("RGB", (64, 64), (10, 200, 10)).save(buf, "PNG")
    return buf.getvalue()


def test_full_flow(client):
    seller, sh, s_email = _signup(client, "seller", "Seller")
    col, ch, _ = _signup(client, "collector", "Collector")
    col2, ch2, _ = _signup(client, "collector", "Collector2")

    # login + refresh
    r = client.post(f"{API}/auth/login", json={"email": s_email, "password": "password123"})
    assert r.status_code == 200
    assert client.post(f"{API}/auth/refresh", json={"refresh_token": r.json()["refresh_token"]}).status_code == 200
    assert client.post(f"{API}/auth/login", json={"email": s_email, "password": "nope"}).status_code == 401

    # upload + create listing
    r = client.post(f"{API}/uploads/images", headers=sh, files=[("files", ("a.png", _jpeg(), "image/png"))])
    assert r.status_code == 201, r.text
    url = r.json()[0]["url"]
    assert client.post(f"{API}/uploads/images", headers=ch, files=[("files", ("a.png", _jpeg(), "image/png"))]).status_code == 403
    body = {"title": "Old newspapers", "category": "clothes", "weight_kg": 12.5, "expected_price": 300,
            "address": "12 MG Road, Salt Lake, Kolkata", "latitude": 22.5837, "longitude": 88.4112, "image_urls": [url]}
    assert client.post(f"{API}/listings", headers=sh, json={**body, "image_urls": []}).status_code == 422
    r = client.post(f"{API}/listings", headers=sh, json=body)
    assert r.status_code == 201, r.text
    lid = r.json()["id"]
    assert r.json()["weight_kg"] == 12.5

    # collector sees masked address + distance
    r = client.get(f"{API}/listings", headers=ch, params={"tab": "open", "lat": 22.5, "lng": 88.3, "sort": "nearest"})
    assert r.status_code == 200 and r.json()["total"] >= 1
    item = next(i for i in r.json()["items"] if i["id"] == lid)
    assert item["address"] == "Salt Lake, Kolkata" and item["distance_km"] is not None and item["latitude"] == 22.58 and item["longitude"] == 88.41 and item["address"] != body["address"]

    # chat before bidding
    r = client.post(f"{API}/messages", headers=ch, json={"listing_id": lid, "recipient_id": seller["id"], "body": "Is it dry?"})
    assert r.status_code == 201, r.text
    r = client.post(f"{API}/messages", headers=sh, json={"listing_id": lid, "recipient_id": col["id"], "body": "Yes"})
    assert r.status_code == 201
    assert client.post(f"{API}/messages", headers=ch, json={"listing_id": lid, "recipient_id": col2["id"], "body": "x"}).status_code == 403
    assert len(client.get(f"{API}/messages", headers=sh).json()) == 2

    # bidding
    assert client.post(f"{API}/listings/{lid}/bids", headers=sh, json={"amount": 100}).status_code == 403
    b1 = client.post(f"{API}/listings/{lid}/bids", headers=ch, json={"amount": 250, "note": "today"})
    assert b1.status_code == 201, b1.text
    b2 = client.post(f"{API}/listings/{lid}/bids", headers=ch2, json={"amount": 280})
    assert b2.status_code == 201
    bids = client.get(f"{API}/listings/{lid}/bids", headers=sh).json()
    assert bids[0]["amount"] == 280 and bids[0]["is_highest"]
    assert client.get(f"{API}/bids/mine", headers=ch).json()["total"] == 1

    # accept
    r = client.post(f"{API}/listings/{lid}/bids/{b1.json()['id']}/accept", headers=sh)
    assert r.status_code == 200 and r.json()["status"] == "offer_accepted" and r.json()["final_price"] == 250
    assert client.post(f"{API}/listings/{lid}/bids/{b2.json()['id']}/accept", headers=sh).status_code == 409

    # tracker
    for expected in ("on_the_way", "arrived", "completed"):
        r = client.post(f"{API}/listings/{lid}/advance", headers=ch)
        assert r.status_code == 200 and r.json()["status"] == expected
    assert client.post(f"{API}/listings/{lid}/advance", headers=ch).status_code == 409
    assert client.get(f"{API}/listings/{lid}/tracker", headers=sh).json()["current_step"] == 5

    # reviews
    assert client.post(f"{API}/listings/{lid}/reviews", headers=sh, json={"rating": 5, "comment": "great"}).status_code == 201
    assert client.post(f"{API}/listings/{lid}/reviews", headers=sh, json={"rating": 5}).status_code == 409
    assert client.post(f"{API}/listings/{lid}/reviews", headers=ch, json={"rating": 4}).status_code == 201
    assert client.get(f"{API}/users/me/reviews", headers=ch).json()["total"] == 1

    # stats / profile
    st = client.get(f"{API}/users/me/stats", headers=sh).json()
    assert st["completed_pickups"] == 1 and st["total_amount"] == 250 and st["amount_label"] == "earned"
    r = client.patch(f"{API}/users/me", headers=sh, json={"phone": "+91 98765 43210", "city": "Kolkata"})
    assert r.status_code == 200 and r.json()["city"] == "Kolkata"
    assert client.get(f"{API}/users/{col['id']}", headers=sh).json()["rating_count"] == 1


def test_new_features(client):
    seller, sh, _ = _signup(client, "seller", "Seller2")
    col, ch, _ = _signup(client, "collector", "Collector3")

    # 1. Multi-photo upload (parallel backend processing)
    img1 = ("files", ("photo1.png", _jpeg(), "image/png"))
    img2 = ("files", ("photo2.png", _jpeg(), "image/png"))
    img3 = ("files", ("photo3.png", _jpeg(), "image/png"))
    r = client.post(f"{API}/uploads/images", headers=sh, files=[img1, img2, img3])
    assert r.status_code == 201, r.text
    uploaded_images = r.json()
    assert len(uploaded_images) == 3
    urls = [img["url"] for img in uploaded_images]

    # 2. Listing creation with multiple photos and convenient seller fields
    listing_body = {
        "title": "Copper wire & iron scrap",
        "category": "metals",
        "weight_kg": 45.0,
        "expected_price": 4500,
        "address": "45 Park Street, Kolkata",
        "latitude": 22.55,
        "longitude": 88.35,
        "image_urls": urls,
        "pickup_time_slot": "Morning (9am - 12pm)",
        "floor_access": "Lift available",
        "is_negotiable": True,
        "packaging_type": "Bags / Boxes",
    }
    r = client.post(f"{API}/listings", headers=sh, json=listing_body)
    assert r.status_code == 201, r.text
    created = r.json()
    assert len(created["images"]) == 3
    assert created["pickup_time_slot"] == "Morning (9am - 12pm)"
    assert created["floor_access"] == "Lift available"
    assert created["is_negotiable"] is True
    assert created["packaging_type"] == "Bags / Boxes"

    # Verify listing detail endpoint returns all fields
    lid = created["id"]
    r = client.get(f"{API}/listings/{lid}", headers=ch)
    assert r.status_code == 200
    detail = r.json()
    assert len(detail["images"]) == 3
    assert detail["pickup_time_slot"] == "Morning (9am - 12pm)"
    assert detail["floor_access"] == "Lift available"
    assert detail["is_negotiable"] is True
    assert detail["packaging_type"] == "Bags / Boxes"

    # 3. Avatar uploads for BOTH Seller and Collector
    # Seller avatar upload
    r_seller_av = client.post(f"{API}/uploads/avatar", headers=sh, files={"file": ("avatar_seller.png", _jpeg(), "image/png")})
    assert r_seller_av.status_code == 201, r_seller_av.text
    seller_av_url = r_seller_av.json()["url"]
    assert "avatars/" in seller_av_url

    # Check seller me profile
    me_seller = client.get(f"{API}/users/me", headers=sh).json()
    assert me_seller["avatar_url"] == seller_av_url

    # Collector avatar upload
    r_col_av = client.post(f"{API}/uploads/avatar", headers=ch, files={"file": ("avatar_collector.png", _jpeg(), "image/png")})
    assert r_col_av.status_code == 201, r_col_av.text
    col_av_url = r_col_av.json()["url"]
    assert "avatars/" in col_av_url

    # Check collector me profile
    me_col = client.get(f"{API}/users/me", headers=ch).json()
    assert me_col["avatar_url"] == col_av_url

    # Verify public profile reflects avatar
    pub = client.get(f"{API}/users/{seller['id']}", headers=ch).json()
    assert pub["avatar_url"] == seller_av_url

