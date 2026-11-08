"""Test real-time chat WebSocket endpoint and companion REST endpoints."""

import io
import uuid
import pytest
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
    return d["user"], d["access_token"], {"Authorization": f"Bearer {d['access_token']}"}


def _jpeg():
    buf = io.BytesIO()
    Image.new("RGB", (64, 64), (10, 200, 10)).save(buf, "PNG")
    return buf.getvalue()


def test_chat_websocket_and_rest(client):
    seller, s_tok, sh = _signup(client, "seller", "SellerWs")
    col, c_tok, ch = _signup(client, "collector", "CollectorWs")

    # Upload & create a listing
    r = client.post(f"{API}/uploads/images", headers=sh, files=[("files", ("a.png", _jpeg(), "image/png"))])
    assert r.status_code == 201, r.text
    url = r.json()[0]["url"]

    body = {
        "title": "Old CRT Monitor",
        "category": "ewaste_appliances",
        "weight_kg": 10.0,
        "expected_price": 200,
        "address": "12 Park Street, Kolkata",
        "latitude": 22.55,
        "longitude": 88.35,
        "image_urls": [url],
    }
    r = client.post(f"{API}/listings", headers=sh, json=body)
    assert r.status_code == 201, r.text
    lid = r.json()["id"]

    # Connect WebSocket for collector with ?token=
    with client.websocket_connect(f"{API}/chat/ws?token={c_tok}") as ws_col:
        ready = ws_col.receive_json()
        assert ready["type"] == "ready"
        assert ready["user"]["id"] == col["id"]

        # Ping-pong
        ws_col.send_json({"type": "ping", "ts": 12345})
        pong = ws_col.receive_json()
        assert pong["type"] == "pong"
        assert pong["ts"] == 12345

        # Also connect WebSocket for seller
        with client.websocket_connect(f"{API}/chat/ws?token={s_tok}") as ws_seller:
            s_ready = ws_seller.receive_json()
            assert s_ready["type"] == "ready"
            assert s_ready["user"]["id"] == seller["id"]

            # Collector sends typing.start
            ws_col.send_json({
                "type": "typing.start",
                "listing_id": lid,
                "recipient_id": seller["id"],
            })
            typing_event = ws_seller.receive_json()
            assert typing_event["type"] == "typing"
            assert typing_event["is_typing"] is True
            assert typing_event["listing_id"] == lid

            # Collector sends a message
            client_msg_id = f"c-{uuid.uuid4().hex[:8]}"
            ws_col.send_json({
                "type": "message.send",
                "listing_id": lid,
                "recipient_id": seller["id"],
                "body": "Hello seller from websocket!",
                "client_id": client_msg_id,
            })

            # Collector gets ack
            ack = ws_col.receive_json()
            assert ack["type"] == "message.ack"
            assert ack["client_id"] == client_msg_id
            assert ack["message"]["body"] == "Hello seller from websocket!"

            # Sending stops typing, so seller receives typing=False
            typing_stop = ws_seller.receive_json()
            assert typing_stop["type"] == "typing"
            assert typing_stop["is_typing"] is False

            # Seller gets message.new
            new_msg = ws_seller.receive_json()
            assert new_msg["type"] == "message.new"
            assert new_msg["message"]["body"] == "Hello seller from websocket!"

            # Seller marks read via WebSocket
            ws_seller.send_json({
                "type": "message.read",
                "listing_id": lid,
                "peer_id": col["id"],
            })

            # Collector receives "read" receipt
            read_receipt = ws_col.receive_json()
            assert read_receipt["type"] == "read"
            assert read_receipt["listing_id"] == lid
            assert read_receipt["reader_id"] == seller["id"]

    # Test REST companions
    # 1. /chat/history
    r = client.get(
        f"{API}/chat/history",
        headers=sh,
        params={"listing_id": lid, "peer_id": col["id"]},
    )
    assert r.status_code == 200, r.text
    hist = r.json()
    assert len(hist["items"]) >= 1
    assert hist["items"][-1]["body"] == "Hello seller from websocket!"

    # 2. /chat/unread
    r = client.get(f"{API}/chat/unread", headers=sh)
    assert r.status_code == 200, r.text

    # 3. /chat/read via HTTP
    r = client.post(
        f"{API}/chat/read",
        headers=sh,
        json={"listing_id": lid, "peer_id": col["id"]},
    )
    assert r.status_code == 200, r.text
    assert "read_at" in r.json()
