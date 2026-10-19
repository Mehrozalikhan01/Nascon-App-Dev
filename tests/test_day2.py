import hashlib
import hmac
import json
from concurrent.futures import ThreadPoolExecutor

from fastapi.testclient import TestClient

from app.core.config import settings
from app.main import app

client = TestClient(app)


def reset():
    result = client.post("/audit/v1/reset", json={"seed": 42})
    assert result.status_code == 200


def signed(entity_id, payload):
    message = entity_id + "|" + json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hmac.new(settings.DAY2_SALT.encode(), message.encode(), hashlib.sha256).hexdigest()


def test_stream_key_lifecycle_and_exact_signature():
    reset()
    payload = {"action": "generate", "player_id": "player-alpha-1", "label": "Grid test", "stream_key_override": "sk_live_9f83a1b2c3d4e5f6"}
    created = client.post("/audit/v1/stream/key", json=payload)
    assert created.status_code == 201
    result = created.json()
    assert result["stream_key"] == payload["stream_key_override"]
    assert result["ingest_endpoints"]["rtmp"].endswith(result["stream_key"])
    assert result["signature"] == signed(result["stream_key"], {
        "action": "generate", "player_id": "player-alpha-1", "status": "active", "stream_key": result["stream_key"]
    })
    revoked = client.post("/audit/v1/stream/key", json={"action": "revoke", "player_id": "player-alpha-1"})
    assert revoked.status_code == 200
    assert revoked.json()["status"] == "revoked"
    assert revoked.json()["ingest_endpoints"] is None


def test_costream_handoff_replay_and_validation_order():
    reset()
    body = {
        "session_id": "cs-session-101", "host_player_id": "player-alpha-1",
        "participants": ["player-alpha-1", "player-alpha-2", "player-beta-1", "player-beta-2"],
        "layout": "grid_4", "simulate_host_drop": True,
    }
    result = client.post("/audit/v1/costream/session", json=body)
    assert result.status_code == 201
    assert result.json()["active_host"] == "player-alpha-2"
    assert result.json()["participant_count"] == 3
    assert result.json()["active_participants"] == body["participants"][1:]
    assert client.post("/audit/v1/costream/session", json=body).status_code == 200
    too_many = {**body, "session_id": "too-many", "participants": [*body["participants"], "player-alpha-3"]}
    assert client.post("/audit/v1/costream/session", json=too_many).json()["error"]["code"] == "COSTREAM_SESSION_FULL"
    drop_to_fit = {**body, "session_id": "drop-to-grid-two", "layout": "grid_2", "participants": body["participants"][:3]}
    assert client.post("/audit/v1/costream/session", json=drop_to_fit).status_code == 201
    invalid_action = client.post("/audit/v1/stream/key", json={"action": [], "player_id": "player-alpha-1"})
    assert invalid_action.status_code == 400


def test_qr_is_dynamic_private_on_inspection_and_exports_valid_bytes():
    reset()
    qr = client.get("/audit/v1/qr/match-m1?inspect=true").json()
    assert qr["scan_count"] == 0
    assert qr["target_route"] == "/web/matches/match-m1/live"
    assert qr["signature"] == signed("match-m1", {"entity_id": "match-m1", "short_url": qr["short_url"], "target_route": qr["target_route"]})
    assert client.get("/audit/v1/qr/match-m1").json()["scan_count"] == 1
    for fmt, content_type, prefix in (("svg", "image/svg+xml", b"<?xml"), ("png", "image/png", b"\x89PNG\r\n\x1a\n"), ("pdf", "application/pdf", b"%PDF-")):
        response = client.get(f"/audit/v1/qr/match-m1/export.{fmt}")
        assert response.status_code == 200
        assert response.headers["content-type"].startswith(content_type)
        assert response.content.startswith(prefix)
    redirect = client.get("/q/m1-live", follow_redirects=False)
    assert redirect.status_code == 302
    assert redirect.headers["location"].endswith("/web/matches/match-m1/live")
    event = client.post("/audit/v1/events", json={"event_id": "day2-finish", "match_id": "match-m1", "type": "match_end", "payload": {}})
    assert event.status_code == 201
    assert client.get("/q/m1-live", follow_redirects=False).headers["location"].endswith("/web/matches/match-m1/vod")


def test_qr_inspection_refreshes_signed_target_after_match_end():
    reset()
    before = client.get("/audit/v1/qr/match-m1?inspect=true").json()
    assert before["target_route"] == "/web/matches/match-m1/live"

    event = client.post("/audit/v1/events", json={
        "event_id": "qr-match-end", "match_id": "match-m1",
        "type": "match_end", "payload": {},
    })
    assert event.status_code == 201

    after = client.get("/audit/v1/qr/match-m1?inspect=true").json()
    assert after["short_url"] == before["short_url"]
    assert after["target_route"] == "/web/matches/match-m1/vod"
    assert after["signature"] != before["signature"]
    assert after["signature"] == signed("match-m1", {
        "entity_id": "match-m1", "short_url": after["short_url"],
        "target_route": "/web/matches/match-m1/vod",
    })

    scan = client.get("/audit/v1/qr/match-m1").json()
    assert scan["short_url"] == before["short_url"]
    assert scan["target_route"] == "/web/matches/match-m1/vod"
    redirect = client.get("/q/m1-live", follow_redirects=False)
    assert redirect.headers["location"].endswith("/web/matches/match-m1/vod")


def test_guest_cart_merge_is_atomic_and_idempotent():
    reset()
    body = {
        "guest_token": "guest_anon_tok_7721", "registered_user_id": "player-alpha-1",
        "guest_cart": [{"variant_id": "var-jersey-001-L", "quantity": 1}], "test_write_block": True,
    }
    first = client.post("/audit/v1/guest/cart-merge", json=body)
    assert first.status_code == 200
    assert first.json()["active_cart"]["subtotal_minor"] == 450000
    assert first.json()["write_rejection_code"] == 403
    assert client.post("/audit/v1/guest/cart-merge", json=body).json() == first.json()
    changed = {**body, "guest_cart": [{"variant_id": "var-jersey-001-M", "quantity": 1}]}
    assert client.post("/audit/v1/guest/cart-merge", json=changed).json()["error"]["code"] == "GUEST_TOKEN_ALREADY_MERGED"
    for path, method, payload in (("/api/v1/chat", "post", {"chat_room_id": "chat-x", "text": "hi"}), ("/audit/v1/checkout", "post", {}), ("/audit/v1/events", "post", {})):
        response = getattr(client, method)(path, json=payload, headers={"Authorization": "Bearer guest_anon_tok_7721"})
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "GUEST_WRITE_FORBIDDEN"


def test_moderation_masks_leetspeak_drops_flood_and_is_concurrency_safe():
    reset()
    body = {
        "chat_room_id": "chat-cs-session-101",
        "incoming_messages": [
            {"message_id": "clean", "sender": "fan-1", "text": "Clean sports commentary"},
            {"message_id": "bad", "sender": "spammer", "text": "Watch this illegal stream b@d_w0rd now!"},
            {"message_id": "flood", "sender": "flooder", "text": "GOAL! AAAA!!!"},
        ], "check_dlq": True, "simulate_failed_notifications": 2,
    }
    with ThreadPoolExecutor(max_workers=10) as pool:
        responses = list(pool.map(lambda _: client.post("/audit/v1/moderation/test", json=body), range(10)))
    assert all(response.status_code == 200 for response in responses)
    result = responses[0].json()
    assert result["filtered_messages"][1]["text"] == "Watch this illegal stream *** now!"
    assert result["kicked_users"] == ["spammer"]
    assert result["muted_users"] == ["flooder"]
    assert len(result["dlq_status"]["undelivered_events"]) == 2
    assert all(response.json() == result for response in responses)


def test_guest_player_dom_accessibility_score_and_metrics_contract():
    reset()
    page = client.get("/web/matches/match-m1/live")
    assert page.status_code == 200
    for selector in ("guest-video-player", "live-score-banner", "pinned-merch-card", "add-to-guest-cart-btn", "guest-cart-count", "chat-message-list", "chat-input-field", "chat-send-btn", "progressive-auth-modal"):
        assert f'data-testid="{selector}"' in page.text
    assert client.get("/audit/v1/score/match-m1").json()["home"]["score"] == 1
    metric = client.get("/metrics")
    assert metric.status_code == 200
    assert metric.headers["content-type"].startswith("text/plain; version=0.0.4")
    for name in ("nascon_active_ws_connections", "nascon_active_streams_total", "nascon_stream_bitrate_kbps", "nascon_dlq_depth", "nascon_qr_scans_total"):
        assert name in metric.text


def test_follow_unfollow_is_idempotent_and_profile_has_required_sections():
    reset()
    from app.core.security import create_role_jwt
    token = create_role_jwt("viewer", "fan-99", "tenant-alpha")
    headers = {"Authorization": f"Bearer {token}"}
    first = client.put("/api/v1/players/player-alpha-1/follow", headers=headers)
    second = client.put("/api/v1/players/player-alpha-1/follow", headers=headers)
    assert first.status_code == second.status_code == 200
    assert second.json()["follower_count"] == 1
    profile = client.get("/api/v1/players/player-alpha-1").json()
    assert all(key in profile for key in ("career_match_events", "clubs", "upcoming_fixtures", "vod_archive"))
    profile_alias = client.get("/api/v1/players/player-alpha-1/profile")
    assert profile_alias.status_code == 200
    assert profile_alias.json() == profile
    removed = client.delete("/api/v1/players/player-alpha-1/follow", headers=headers)
    assert removed.json()["follower_count"] == 0


def test_privacy_erasure_removes_linked_profile_and_chat_identifiers():
    reset()
    from app.core.security import create_role_jwt
    token = create_role_jwt("viewer", "player-alpha-1", "tenant-alpha")
    posted = client.post("/api/v1/chat", headers={"Authorization": f"Bearer {token}"}, json={
        "chat_room_id": "chat-privacy", "text": "Anonymize this account",
    })
    assert posted.status_code == 200
    result = client.delete("/audit/v1/privacy/user/player-alpha-1")
    assert result.status_code == 200
    assert result.json()["pii_retained"] is False
    assert client.get("/api/v1/players/player-alpha-1").json()["player"]["name"] == "Deleted user"
    messages = client.get("/api/v1/chat/chat-privacy/messages").json()["items"]
    assert messages[0]["sender"] == "deleted-user"
