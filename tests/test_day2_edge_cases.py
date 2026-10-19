"""Edge-case coverage for the Day 2 API routes and streaming helpers."""

import asyncio
import sys
import types
from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.core.config import settings
from app.core.security import create_role_jwt
from app.main import app
from app.services import day2 as day2_service


client = TestClient(app)


def reset():
    response = client.post("/audit/v1/reset", json={"seed": 42})
    assert response.status_code == 200


def test_reset_clears_redis_dlq_when_redis_is_configured(monkeypatch):
    reset_calls = []

    class FakeRedisClient:
        def delete(self, key):
            reset_calls.append(("delete", key))

        def close(self):
            reset_calls.append(("close", None))

    monkeypatch.setattr(settings, "REDIS_URL", "redis://unit-test/0")
    monkeypatch.setitem(sys.modules, "redis", SimpleNamespace(Redis=SimpleNamespace(from_url=lambda *args, **kwargs: FakeRedisClient())))
    day2_service.reset_day2()
    assert reset_calls == [("delete", "nascon:day2:dlq"), ("close", None)]


def test_stream_key_validation_rotate_revoke_and_media_authorization():
    reset()
    assert client.post("/audit/v1/stream/key", content="{").status_code == 400
    assert client.post("/audit/v1/stream/key", json=[]).status_code == 400
    assert client.post("/audit/v1/stream/key", json={"action": "other", "player_id": ""}).status_code == 400
    assert client.post("/audit/v1/stream/key", json={"action": "generate", "player_id": "player-alpha-1", "label": ""}).status_code == 400
    assert client.post("/audit/v1/stream/key", json={"action": "generate", "player_id": "player-alpha-1", "stream_key_override": "bad"}).status_code == 400
    assert client.post("/audit/v1/stream/key", json={"action": "generate", "player_id": "no-such-player"}).status_code == 404
    assert client.post("/audit/v1/stream/key", json={"action": "revoke", "player_id": "player-alpha-1"}).status_code == 404

    created = client.post("/audit/v1/stream/key", json={"action": "generate", "player_id": "player-alpha-1"})
    assert created.status_code == 201
    old_key = created.json()["stream_key"]
    assert client.get("/api/v1/matches/missing/playback").status_code == 404
    assert client.get("/api/v1/matches/match-m1/playback").json()["status"] == "live"
    assert client.post("/internal/media/auth", json={"action": "publish", "path": f"live/{old_key}"}).status_code == 200
    assert client.post("/internal/media/auth", json={"action": "read", "path": "live/unknown"}).status_code == 401

    rotated = client.post("/audit/v1/stream/key", json={"action": "rotate", "player_id": "player-alpha-1"})
    assert rotated.status_code == 200
    assert rotated.json()["stream_key"] != old_key
    assert client.post("/internal/media/auth", json={"action": "publish", "path": f"live/{old_key}"}).status_code == 409
    assert client.post("/internal/media/auth", content="{").status_code == 401
    assert client.post("/internal/media/auth", json=[]).status_code == 401
    assert client.post("/audit/v1/stream/key", json={"action": "revoke", "player_id": "player-alpha-1"}).status_code == 200
    assert client.get("/api/v1/matches/match-m1/playback").json()["status"] == "offline"


def test_costream_validation_replay_conflict_and_qr_entity_routes():
    reset()
    assert client.post("/audit/v1/costream/session", content="{").status_code == 400
    assert client.post("/audit/v1/costream/session", json=[]).status_code == 400
    invalid = client.post("/audit/v1/costream/session", json={"session_id": "bad", "host_player_id": "", "participants": [], "layout": "bad", "simulate_host_drop": "yes"})
    assert invalid.status_code == 400

    base = {"session_id": "cs-session-qr", "host_player_id": "player-alpha-1", "participants": ["player-alpha-1"], "layout": "solo"}
    assert client.post("/audit/v1/costream/session", json={**base, "session_id": "missing-participant", "participants": ["missing-player"]}).status_code == 404
    assert client.post("/audit/v1/costream/session", json={**base, "session_id": "missing-host", "host_player_id": "missing-player"}).status_code == 404
    assert client.post("/audit/v1/costream/session", json={**base, "session_id": "host-not-in-list", "participants": ["player-alpha-2"]}).status_code == 400
    too_many_for_solo = {**base, "session_id": "layout-full", "participants": ["player-alpha-1", "player-alpha-2"]}
    assert client.post("/audit/v1/costream/session", json=too_many_for_solo).status_code == 400

    created = client.post("/audit/v1/costream/session", json=base)
    assert created.status_code == 201
    assert client.get("/api/v1/costream/missing").status_code == 404
    conflict = client.post("/audit/v1/costream/session", json={**base, "layout": "spotlight"})
    assert conflict.status_code == 409

    player_qr = client.get("/audit/v1/qr/player-alpha-2?inspect=true")
    club_qr = client.get("/audit/v1/qr/club-alpha-1?inspect=true")
    co_qr = client.get("/audit/v1/qr/cs-session-qr?inspect=true")
    assert player_qr.json()["target_route"] == "/web/players/player-alpha-2"
    assert club_qr.json()["target_route"] == "/web/clubs/club-alpha-1"
    assert co_qr.json()["target_route"] == "/web/costream/cs-session-qr"
    assert client.get("/audit/v1/qr/unknown-entity").status_code == 404
    assert client.get("/q/missing-code", follow_redirects=False).status_code == 404
    assert client.get(f"/q/{co_qr.json()['short_url'].rsplit('/', 1)[-1]}", follow_redirects=False).headers["location"].endswith("/web/costream/cs-session-qr")
    assert client.get(f"/q/{player_qr.json()['short_url'].rsplit('/', 1)[-1]}", follow_redirects=False).headers["location"].endswith("/web/players/player-alpha-2")
    assert client.get(f"/q/{club_qr.json()['short_url'].rsplit('/', 1)[-1]}", follow_redirects=False).headers["location"].endswith("/web/clubs/club-alpha-1")


def test_qr_export_rejects_unknown_format_and_reports_missing_optional_renderer(monkeypatch):
    reset()
    assert client.get("/audit/v1/qr/missing/export.svg").status_code == 404
    assert client.get("/audit/v1/qr/match-m1/export.txt").status_code == 400
    monkeypatch.setitem(sys.modules, "qrcode", None)
    response = client.get("/audit/v1/qr/match-m1/export.svg")
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "EXPORT_UNAVAILABLE"


def test_moderation_validation_rate_limit_long_input_and_redis_dlq(monkeypatch):
    reset()
    calls = []

    class FakeRedisClient:
        def rpush(self, key, *items):
            calls.append((key, items))

        def close(self):
            calls.append(("close", ()))

    monkeypatch.setattr(settings, "REDIS_URL", "redis://unit-test/0")
    monkeypatch.setitem(sys.modules, "redis", SimpleNamespace(Redis=SimpleNamespace(from_url=lambda *args, **kwargs: FakeRedisClient())))
    assert client.post("/audit/v1/moderation/test", json=None).status_code == 400
    assert client.post("/audit/v1/moderation/test", json={"chat_room_id": "x", "incoming_messages": [], "check_dlq": "yes"}).status_code == 400

    payload = {"chat_room_id": "room-edge", "incoming_messages": [{"message_id": str(i), "sender": "fast-sender", "text": "plain text"} for i in range(4)], "simulate_failed_notifications": 1}
    response = client.post("/audit/v1/moderation/test", json=payload)
    assert response.status_code == 200
    assert response.json()["muted_users"] == ["fast-sender"]
    assert calls[0][0] == "nascon:day2:dlq"
    assert len(calls[0][1]) == 1
    assert calls[-1][0] == "close"

    long_payload = {"chat_room_id": "room-long", "incoming_messages": [{"message_id": "long", "sender": "long-sender", "text": "normal " * 100}], "check_dlq": False}
    assert client.post("/audit/v1/moderation/test", json=long_payload).status_code == 200


def test_guest_cart_error_paths_and_write_block_toggle():
    reset()
    assert client.post("/audit/v1/guest/cart-merge", content="{").status_code == 400
    assert client.post("/audit/v1/guest/cart-merge", json=[]).status_code == 400
    body = {"guest_token": "guest_unknown", "registered_user_id": "player-alpha-1", "guest_cart": [{"variant_id": "var-jersey-001-L", "quantity": 1}]}
    assert client.post("/audit/v1/guest/cart-merge", json={**body, "guest_cart": [{"variant_id": "var-jersey-001-L", "quantity": True}]}).status_code == 400
    assert client.post("/audit/v1/guest/cart-merge", json={**body, "registered_user_id": "missing-player"}).status_code == 404
    assert client.post("/audit/v1/guest/cart-merge", json=body).status_code == 401

    token = "guest_test_variant"
    day2_service._guest_tokens[token] = {"status": "active", "role": "guest", "user_id": None}
    missing = {**body, "guest_token": token, "guest_cart": [{"variant_id": "not-a-variant", "quantity": 1}]}
    assert client.post("/audit/v1/guest/cart-merge", json=missing).status_code == 404
    out_of_stock = {**body, "guest_token": token, "guest_cart": [{"variant_id": "var-jersey-001-L", "quantity": 999}]}
    assert client.post("/audit/v1/guest/cart-merge", json=out_of_stock).status_code == 409
    success = client.post("/audit/v1/guest/cart-merge", json={**body, "guest_token": token, "test_write_block": False})
    assert success.status_code == 200
    assert success.json()["write_block_verified"] is False
    assert client.post("/audit/v1/guest/cart-merge", json={**body, "guest_token": token, "test_write_block": False}).status_code == 200


def test_privacy_erasure_cleans_merged_guest_follow_and_stream_records():
    reset()
    token = "guest_privacy_token"
    day2_service._guest_tokens[token] = {"status": "active", "role": "guest", "user_id": None}
    merged = client.post("/audit/v1/guest/cart-merge", json={"guest_token": token, "registered_user_id": "player-alpha-1", "guest_cart": [{"variant_id": "var-jersey-001-L", "quantity": 1}]})
    assert merged.status_code == 200
    member = create_role_jwt("viewer", "player-alpha-1", "tenant-alpha")
    assert client.put("/api/v1/players/player-alpha-2/follow", headers={"Authorization": f"Bearer {member}"}).status_code == 200
    assert client.post("/audit/v1/stream/key", json={"action": "generate", "player_id": "player-alpha-1"}).status_code == 201
    erased = client.delete("/audit/v1/privacy/user/player-alpha-1")
    assert erased.status_code == 200
    assert day2_service._guest_tokens[token]["status"] == "revoked"
    assert client.get("/api/v1/players/player-alpha-2").json()["follower_count"] == 0


def test_costream_media_authorization_and_guest_page_not_found_routes():
    reset()
    session = client.post("/audit/v1/costream/session", json={"session_id": "cs-session-auth", "host_player_id": "player-alpha-1", "participants": ["player-alpha-1"], "layout": "solo"})
    assert session.status_code == 201
    assert client.post("/internal/media/auth", json={"action": "read", "path": "costream/cs-session-auth"}).status_code == 200
    assert client.post("/internal/media/auth", json={"action": "read", "path": "costream/missing"}).status_code == 401
    assert client.get("/web/matches/match-m1/unknown").status_code == 404
    assert client.get("/web/players/missing").status_code == 404
    assert client.get("/web/clubs/missing").status_code == 404
    assert client.get("/web/costream/cs-session-auth").status_code == 200
    assert client.get("/web/players/player-alpha-1").status_code == 200
    assert client.get("/web/clubs/club-alpha-1").status_code == 200


def test_chat_auth_validation_rate_limit_messages_and_sse_cleanup():
    reset()
    assert client.post("/api/v1/chat", json={"chat_room_id": "r", "text": "hello"}).status_code == 401
    token = create_role_jwt("viewer", "fan-edge", "tenant-alpha")
    headers = {"Authorization": f"Bearer {token}"}
    assert client.post("/api/v1/chat", content="{", headers=headers).status_code == 400
    assert client.post("/api/v1/chat", json=[], headers=headers).status_code == 400
    assert client.post("/api/v1/chat", json={"chat_room_id": "room-chat", "text": " "}, headers=headers).status_code == 400
    for index in range(3):
        response = client.post("/api/v1/chat", json={"chat_room_id": "room-chat", "text": f"hello {index}", "message_id": f"edge-{index}"}, headers=headers)
        assert response.status_code == 200
    blocked = client.post("/api/v1/chat", json={"chat_room_id": "room-chat", "text": "one too many", "message_id": "edge-blocked"}, headers=headers)
    assert blocked.status_code == 429
    messages = client.get("/api/v1/chat/room-chat/messages?after=2").json()
    assert len(messages["items"]) == 1
    assert messages["last_sequence"] == 3

    async def read_sse_once():
        response = await day2_service.chat_sse("room-chat")
        iterator = response.body_iterator
        first = await iterator.__anext__()
        await iterator.aclose()
        return first

    first_event = asyncio.run(read_sse_once())
    assert "event: chat" in first_event


def test_match_stream_websocket_and_publish_helpers_cleanup():
    reset()
    with client.websocket_connect("/ws/matches/match-m1") as websocket:
        assert websocket.receive_json()["type"] == "score"
        assert "nascon_active_ws_connections 1" in client.get("/metrics").text
    assert "nascon_active_ws_connections 0" in client.get("/metrics").text

    match_queue = asyncio.Queue(maxsize=1)
    match_queue.put_nowait({"old": True})
    day2_service._match_subscribers["publish-match"].add(match_queue)
    chat_queue = asyncio.Queue(maxsize=1)
    chat_queue.put_nowait({"old": True})
    day2_service._chat_subscribers["publish-chat"].add(chat_queue)

    async def publish():
        await day2_service.publish_match_event("publish-match", "score", {"status": "completed"})
        await day2_service.publish_chat("publish-chat", {"message_id": "published"})

    asyncio.run(publish())
    assert match_queue.get_nowait()["type"] == "score"
    assert chat_queue.get_nowait()["message_id"] == "published"
    day2_service._match_subscribers["publish-match"].discard(match_queue)
    day2_service._chat_subscribers["publish-chat"].discard(chat_queue)

    async def read_match_sse_once():
        response = await day2_service.match_sse("match-m1")
        iterator = response.body_iterator
        retry = await iterator.__anext__()
        score = await iterator.__anext__()
        await iterator.aclose()
        return retry, score

    retry, score = asyncio.run(read_match_sse_once())
    assert "retry: 1000" in retry
    assert "event: score" in score
    assert day2_service._project_match("match-that-does-not-exist")["status"] == "upcoming"


def test_profile_errors_vod_archive_and_follow_auth_errors(monkeypatch):
    reset()
    assert client.get("/api/v1/players/not-a-player/profile").status_code == 404
    assert client.put("/api/v1/players/player-alpha-1/follow").status_code == 401
    assert client.delete("/api/v1/players/player-alpha-1/follow").status_code == 401
    token = create_role_jwt("viewer", "fan-profile", "tenant-alpha")
    headers = {"Authorization": f"Bearer {token}"}
    assert client.put("/api/v1/players/missing/follow", headers=headers).status_code == 404
    assert client.delete("/api/v1/players/missing/follow", headers=headers).status_code == 404

    generated = client.post("/audit/v1/stream/key", json={"action": "generate", "player_id": "player-alpha-1"})
    assert generated.status_code == 201

    class FakeMinio:
        def list_objects(self, bucket, prefix, recursive):
            assert bucket == settings.MINIO_BUCKET
            assert prefix.endswith(generated.json()["stream_key"] + "/")
            assert recursive is True
            return [SimpleNamespace(object_name=prefix + "match-1.m3u8")]

    fake_store = SimpleNamespace(make_minio_client=lambda *args, **kwargs: FakeMinio())
    monkeypatch.setattr(settings, "MINIO_ENDPOINT", "minio:9000")
    monkeypatch.setattr(settings, "MINIO_ACCESS_KEY", "test-access")
    monkeypatch.setattr(settings, "MINIO_SECRET_KEY", "test-secret")
    monkeypatch.setitem(sys.modules, "app.services.object_store", fake_store)
    profile = client.get("/api/v1/players/player-alpha-1/profile")
    assert profile.status_code == 200
    assert profile.json()["vod_archive"] == [{"object": f"vod/live/{generated.json()['stream_key']}/match-1.m3u8"}]


def test_qr_and_stream_reject_malformed_json_paths():
    reset()
    assert client.post("/audit/v1/stream/key", json={"action": "generate", "player_id": "player-alpha-1", "label": "x" * 65}).status_code == 400
    assert client.post("/audit/v1/stream/key", json={"action": "generate", "player_id": "player-alpha-1", "stream_key_override": 42}).status_code == 400
    assert client.get("/audit/v1/qr/club-missing?inspect=true").status_code == 404
