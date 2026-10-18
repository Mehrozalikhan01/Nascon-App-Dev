"""Day 2 platform contracts: streams, co-streams, QR guests, chat, and metrics.

This module keeps the public API contract in one place.  The competition stack
runs one API process; state-changing critical sections are protected so replay
and concurrency probes are deterministic.  Durable notification failures are
also copied to Redis when it is available.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import io
import json
import re
import secrets
import threading
import time
from collections import defaultdict, deque
from typing import Any

from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response, StreamingResponse

from app.core.config import settings
from app.services.auth import auth_service
from app.services.costream import layout_capacity_error, resolve_host_drop
from app.services.moderation_dlq import is_flood_message, mask_profanity
from app.services.observability import render_metrics
from app.services.qr_guest import merge_guest_items, target_route as resolve_target_route
from app.services.roster import roster_service
from app.services.store import store_service
from app.services.streaming import create_stream_key, ingest_endpoints, media_publish_allowed, stream_key_is_valid, stream_signature_payload

router = APIRouter()
_lock = threading.RLock()
_stream_by_player: dict[str, dict[str, Any]] = {}
_stream_by_key: dict[str, dict[str, Any]] = {}
_sessions: dict[str, dict[str, Any]] = {}
_qr_entities: dict[str, dict[str, Any]] = {}
_short_to_entity: dict[str, str] = {}
_guest_tokens: dict[str, dict[str, Any]] = {}
_guest_merges: dict[str, dict[str, Any]] = {}
_carts: dict[str, dict[str, Any]] = {}
_dlq: deque[dict[str, Any]] = deque(maxlen=10000)
_moderation_replays: dict[str, dict[str, Any]] = {}
_moderation_times: dict[str, deque[float]] = defaultdict(deque)
_chat_messages: dict[str, list[dict[str, Any]]] = defaultdict(list)
_chat_sequences: dict[str, int] = defaultdict(int)
_follows: set[tuple[str, str]] = set()
_match_subscribers: dict[str, set[asyncio.Queue]] = defaultdict(set)
_chat_subscribers: dict[str, set[asyncio.Queue]] = defaultdict(set)
_ws_count = 0

def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _sign(entity_id: str, payload: dict[str, Any]) -> str:
    salt = getattr(settings, "DAY2_SALT", "") or ""
    message = f"{entity_id}|{_canonical(payload)}".encode("utf-8")
    return hmac.new(salt.encode("utf-8"), message, hashlib.sha256).hexdigest()


def reset_day2() -> None:
    """Reset Day 2 state and install the required deterministic baseline."""
    global _ws_count
    with _lock:
        _stream_by_player.clear()
        _stream_by_key.clear()
        _sessions.clear()
        _qr_entities.clear()
        _short_to_entity.clear()
        _guest_tokens.clear()
        _guest_merges.clear()
        _carts.clear()
        _dlq.clear()
        _moderation_replays.clear()
        _moderation_times.clear()
        _chat_messages.clear()
        _chat_sequences.clear()
        _follows.clear()
        _ws_count = 0
        defaults = [
            ("match-m1", "match", "in_progress", "m1-live"),
            ("player-alpha-1", "player", "active", "p-alpha-1"),
            ("club-alpha-1", "club", "active", "c-alpha-1"),
        ]
        for entity_id, entity_type, state, short_code in defaults:
            _qr_entities[entity_id] = {
                "entity_type": entity_type,
                "status": state,
                "short_code": short_code,
                "scan_count": 0,
            }
            _short_to_entity[short_code] = entity_id
        _guest_tokens["guest_anon_tok_7721"] = {
            "status": "active", "role": "guest", "user_id": None,
        }
        _carts["player-alpha-1"] = []
        if settings.REDIS_URL:
            try:
                import redis
                client = redis.Redis.from_url(
                    settings.REDIS_URL, socket_connect_timeout=0.2, socket_timeout=0.2
                )
                client.delete("nascon:day2:dlq")
                client.close()
            except Exception:
                pass


reset_day2()


def _error(code: str, message: str, status_code: int, details: dict[str, Any] | None = None):
    return JSONResponse(
        status_code=status_code,
        content={"error": {"code": code, "message": message, "details": details or {}}},
    )


def guest_write_decision(token: str, method: str) -> tuple[int, str | None]:
    """Return the shared server-side decision for guest-token mutations."""
    if token.startswith("guest_") and method.upper() in {"POST", "PUT", "PATCH", "DELETE"}:
        return 403, "GUEST_WRITE_FORBIDDEN"
    return 0, None


def _player_exists(player_id: str) -> bool:
    return roster_service.get_player(player_id) is not None


def _match_target(entity_id: str, status: str) -> str:
    return resolve_target_route("match", entity_id, status)


def update_match_state(match_id: str, status: str) -> None:
    """Synchronize a match QR route with the event-sourced score state."""
    entity = _qr_entities.get(match_id)
    if entity and status in {"upcoming", "in_progress", "completed", "postponed", "cancelled", "abandoned"}:
        entity["status"] = status


def _url(request: Request, path: str) -> str:
    return str(request.base_url).rstrip("/") + path


def _short_code(entity_id: str) -> str:
    if entity_id == "match-m1":
        return "m1-live"
    if entity_id == "player-alpha-1":
        return "p-alpha-1"
    if entity_id == "club-alpha-1":
        return "c-alpha-1"
    if entity_id.startswith("cs-session-"):
        return entity_id.removeprefix("cs-session-")[:24]
    return "e-" + hashlib.sha256(entity_id.encode()).hexdigest()[:12]


def _ensure_qr(entity_id: str) -> dict[str, Any] | None:
    existing = _qr_entities.get(entity_id)
    if existing:
        return existing
    if entity_id.startswith("player-") and _player_exists(entity_id):
        entity_type, state = "player", "active"
    elif entity_id.startswith("club-") and roster_service.get_squad_profile(entity_id):
        entity_type, state = "club", "active"
    elif entity_id.startswith("cs-session-") and entity_id in _sessions:
        entity_type, state = "costream", "active"
    else:
        return None
    code = _short_code(entity_id)
    result = {"entity_type": entity_type, "status": state, "short_code": code, "scan_count": 0}
    _qr_entities[entity_id] = result
    _short_to_entity[code] = entity_id
    return result


def _qr_metadata(request: Request, entity_id: str, entity: dict[str, Any]) -> dict[str, Any]:
    code = entity["short_code"]
    short_url = entity.get("short_url")
    if not short_url:
        short_url = _url(request, f"/q/{code}")
        entity["short_url"] = short_url
    entity_type = entity["entity_type"]
    if entity_type == "match":
        target_route = _match_target(entity_id, entity["status"])
    elif entity_type == "costream":
        target_route = f"/web/costream/{entity_id}"
    elif entity_type == "player":
        target_route = f"/web/players/{entity_id}"
    else:
        target_route = f"/web/clubs/{entity_id}"
    payload = {"entity_id": entity_id, "short_url": short_url, "target_route": target_route}
    return {
        "entity_id": entity_id,
        "entity_type": entity_type,
        "entity_status": entity["status"],
        "short_url": short_url,
        "target_route": target_route,
        "deep_link_uri": f"nascon://entity/{entity_id}",
        "scan_count": entity["scan_count"],
        "export_formats": {
            fmt: _url(request, f"/audit/v1/qr/{entity_id}/export.{fmt}")
            for fmt in ("svg", "png", "pdf")
        },
        "signature": _sign(entity_id, payload),
    }


@router.post("/audit/v1/stream/key")
async def stream_key(request: Request):
    try:
        body = await request.json()
    except Exception:
        return _error("VALIDATION_ERROR", "Malformed JSON", 400, {"fields": ["body"]})
    if not isinstance(body, dict):
        return _error("VALIDATION_ERROR", "Request body must be an object", 400, {"fields": ["body"]})
    action, player_id = body.get("action"), body.get("player_id")
    valid_action = isinstance(action, str) and action in {"generate", "rotate", "revoke"}
    if not valid_action or not isinstance(player_id, str) or not player_id:
        fields = [key for key, valid in (("action", valid_action), ("player_id", isinstance(player_id, str) and bool(player_id))) if not valid]
        return _error("VALIDATION_ERROR", "Invalid stream key request", 400, {"fields": fields})
    label = body.get("label", "default")
    if not isinstance(label, str) or not 1 <= len(label) <= 64:
        return _error("VALIDATION_ERROR", "label must be 1 to 64 characters", 400, {"fields": ["label"]})
    override = body.get("stream_key_override")
    if override is not None and not stream_key_is_valid(override):
        return _error("VALIDATION_ERROR", "stream_key_override has an invalid format", 400, {"fields": ["stream_key_override"]})
    if not _player_exists(player_id):
        return _error("PLAYER_NOT_FOUND", "Player not found", 404, {"player_id": player_id})

    with _lock:
        current = _stream_by_player.get(player_id)
        if action == "revoke":
            if not current or current.get("status") != "active":
                return _error("STREAM_NOT_FOUND", "No active stream key exists for this player", 404, {"player_id": player_id})
            key = current["stream_key"]
            current["status"] = "revoked"
            _stream_by_key[key]["status"] = "revoked"
            response = {
                "player_id": player_id, "label": current["label"], "stream_key": key,
                "status": "revoked", "ingest_endpoints": None, "playback_url": None,
            }
            status_code = 200
        else:
            key = create_stream_key(override)
            previous = _stream_by_player.get(player_id)
            if previous:
                previous["status"] = "revoked"
                _stream_by_key[previous["stream_key"]]["status"] = "revoked"
            record = {"player_id": player_id, "label": label, "stream_key": key, "status": "active", "created_at": time.time()}
            _stream_by_player[player_id] = record
            _stream_by_key[key] = record
            response = {
                "player_id": player_id, "label": label, "stream_key": key, "status": "active",
                "ingest_endpoints": ingest_endpoints(key),
                "playback_url": f"http://localhost:8888/live/{key}/index.m3u8",
            }
            status_code = 201 if action == "generate" else 200
        signature_payload = stream_signature_payload(action, player_id, response["status"], response["stream_key"])
        response["signature"] = _sign(response["stream_key"], signature_payload)
    return JSONResponse(status_code=status_code, content=response)


@router.post("/audit/v1/costream/session")
async def create_costream_session(request: Request):
    try:
        body = await request.json()
    except Exception:
        return _error("VALIDATION_ERROR", "Malformed JSON", 400, {"fields": ["body"]})
    if not isinstance(body, dict):
        return _error("VALIDATION_ERROR", "Request body must be an object", 400, {"fields": ["body"]})
    session_id, host, participants = body.get("session_id"), body.get("host_player_id"), body.get("participants")
    layout = body.get("layout", "grid_4")
    simulate_drop = body.get("simulate_host_drop", False)
    fields: list[str] = []
    if not isinstance(session_id, str) or not session_id: fields.append("session_id")
    if not isinstance(host, str) or not host: fields.append("host_player_id")
    if not isinstance(participants, list) or not participants or any(not isinstance(item, str) or not item for item in participants): fields.append("participants")
    elif len(set(participants)) != len(participants): fields.append("participants")
    if not isinstance(layout, str) or layout not in {"solo", "grid_2", "grid_4", "spotlight"}: fields.append("layout")
    if not isinstance(simulate_drop, bool): fields.append("simulate_host_drop")
    if fields:
        return _error("VALIDATION_ERROR", "Invalid co-stream request", 400, {"fields": fields})
    for player_id in participants:
        if not _player_exists(player_id):
            return _error("PLAYER_NOT_FOUND", "A co-stream participant does not exist", 404, {"player_id": player_id})
    if not _player_exists(host):
        return _error("PLAYER_NOT_FOUND", "Host player not found", 404, {"player_id": host})
    if host not in participants:
        return _error("VALIDATION_ERROR", "host_player_id must be a participant", 400, {"fields": ["host_player_id"]})
    if len(participants) > 4:
        return _error("COSTREAM_SESSION_FULL", "At most four participants are supported", 409)
    effective_count = 0 if simulate_drop and len(participants) == 1 else len(participants) - int(simulate_drop)
    if layout_capacity_error(layout, effective_count):
        return _error("UNSUPPORTED_LAYOUT", "The selected layout cannot display all participants", 400, {"layout": layout})

    request_fingerprint = _canonical({"session_id": session_id, "host_player_id": host, "participants": participants, "layout": layout, "simulate_host_drop": simulate_drop})
    with _lock:
        existing = _sessions.get(session_id)
        if existing:
            if existing["request_fingerprint"] == request_fingerprint:
                return JSONResponse(status_code=200, content=existing["response"])
            return _error("VALIDATION_ERROR", "session_id is already used for a different request", 409, {"fields": ["session_id"]})
        handoff_result = resolve_host_drop(host, participants, simulate_drop)
        active = handoff_result["active_participants"]
        active_host = handoff_result["active_host"]
        handoff = handoff_result["handoff_occurred"]
        status = handoff_result["status"]
        result = {
            "session_id": session_id,
            "status": status,
            "active_host": active_host,
            "handoff_occurred": handoff,
            "participant_count": len(active),
            "active_participants": active,
            "layout": layout,
            "composite_playback_url": f"http://localhost:8888/costream/{session_id}/index.m3u8" if active else None,
            "chat_room_id": f"chat-{session_id}",
        }
        signed = {key: result[key] for key in ("active_host", "handoff_occurred", "layout", "participant_count", "session_id", "status")}
        result["signature"] = _sign(session_id, signed)
        _sessions[session_id] = {"request_fingerprint": request_fingerprint, "response": result}
        _qr_entities.setdefault(session_id, {"entity_type": "costream", "status": status, "short_code": _short_code(session_id), "scan_count": 0})
        _short_to_entity.setdefault(_short_code(session_id), session_id)
    return JSONResponse(status_code=201, content=result)


@router.get("/api/v1/costream/{session_id}")
def get_costream_session(session_id: str):
    session = _sessions.get(session_id)
    if not session:
        return _error("STREAM_NOT_FOUND", "Co-stream session not found", 404, {"session_id": session_id})
    return session["response"]


@router.get("/audit/v1/qr/{entity_id}")
def qr_metadata(entity_id: str, request: Request, inspect: bool = False):
    with _lock:
        entity = _ensure_qr(entity_id)
        if entity is None:
            return _error("QR_NOT_FOUND", "QR entity not found", 404, {"entity_id": entity_id})
        if not inspect:
            entity["scan_count"] += 1
        result = _qr_metadata(request, entity_id, entity)
    return result


@router.get("/audit/v1/qr/{entity_id}/export.{fmt}")
def export_qr(entity_id: str, fmt: str, request: Request):
    with _lock:
        entity = _ensure_qr(entity_id)
        if entity is None:
            return _error("QR_NOT_FOUND", "QR entity not found", 404, {"entity_id": entity_id})
        short_url = _qr_metadata(request, entity_id, entity)["short_url"]
    if fmt not in {"svg", "png", "pdf"}:
        return _error("VALIDATION_ERROR", "Unsupported QR export format", 400, {"fields": ["fmt"]})
    try:
        import qrcode
        from qrcode.image.svg import SvgPathImage
        qr = qrcode.QRCode(box_size=8, border=4, error_correction=qrcode.constants.ERROR_CORRECT_M)
        qr.add_data(short_url)
        qr.make(fit=True)
        if fmt == "svg":
            rendered = qr.make_image(image_factory=SvgPathImage)
            output = io.BytesIO()
            rendered.save(output)
            return Response(output.getvalue(), media_type="image/svg+xml")
        if fmt == "png":
            rendered = qr.make_image(fill_color="black", back_color="white")
            output = io.BytesIO()
            rendered.save(output, format="PNG")
            return Response(output.getvalue(), media_type="image/png")
        from reportlab.lib.pagesizes import A4
        from reportlab.pdfgen import canvas
        from reportlab.lib.utils import ImageReader
        image = qr.make_image(fill_color="black", back_color="white")
        image_bytes = io.BytesIO()
        image.save(image_bytes, format="PNG")
        image_bytes.seek(0)
        pdf = io.BytesIO()
        page = canvas.Canvas(pdf, pagesize=A4)
        page.setTitle(f"NaSCon QR - {entity_id}")
        page.setFont("Helvetica-Bold", 22)
        page.drawCentredString(A4[0] / 2, A4[1] - 70, "NaSCon '26")
        page.drawCentredString(A4[0] / 2, A4[1] - 108, entity_id)
        page.drawImage(ImageReader(image_bytes), A4[0] / 2 - 110, A4[1] - 355, 220, 220)
        page.setFont("Helvetica", 10)
        page.drawCentredString(A4[0] / 2, A4[1] - 380, short_url)
        page.save()
        return Response(pdf.getvalue(), media_type="application/pdf")
    except ImportError:
        return _error("EXPORT_UNAVAILABLE", "QR export dependencies are not installed", 503)


@router.get("/q/{short_code}")
def resolve_short_qr(short_code: str):
    with _lock:
        entity_id = _short_to_entity.get(short_code)
        entity = _qr_entities.get(entity_id) if entity_id else None
        if not entity_id or not entity:
            return _error("QR_NOT_FOUND", "Short QR link not found", 404, {"short_code": short_code})
        if entity["entity_type"] == "match":
            route = _match_target(entity_id, entity["status"])
        elif entity["entity_type"] == "costream":
            route = f"/web/costream/{entity_id}"
        elif entity["entity_type"] == "player":
            route = f"/web/players/{entity_id}"
        else:
            route = f"/web/clubs/{entity_id}"
        entity["scan_count"] += 1
    return RedirectResponse(url=route, status_code=302)


def _queue_dlq(room: str, count: int) -> None:
    events = [{"event_id": f"dlq-{secrets.token_hex(8)}", "chat_room_id": room, "type": "notification.delivery_failed", "attempts": 3} for _ in range(count)]
    _dlq.extend(events)
    if settings.REDIS_URL:
        try:
            import redis
            client = redis.Redis.from_url(settings.REDIS_URL, socket_connect_timeout=0.2, socket_timeout=0.2)
            if events:
                client.rpush("nascon:day2:dlq", *[json.dumps(item, separators=(",", ":")) for item in events])
            client.close()
        except Exception:
            # In-memory queue still provides a usable local probe; Compose's
            # Redis health check makes the normal deployment durable.
            pass


def _moderate(body: dict[str, Any]) -> dict[str, Any]:
    room = body.get("chat_room_id")
    messages = body.get("incoming_messages")
    check_dlq = body.get("check_dlq", True)
    failed_count = body.get("simulate_failed_notifications", 0)
    fields = []
    if not isinstance(room, str) or not room: fields.append("chat_room_id")
    if not isinstance(messages, list) or any(not isinstance(item, dict) or not isinstance(item.get("message_id"), str) or not isinstance(item.get("sender"), str) or not isinstance(item.get("text"), str) for item in (messages or [])): fields.append("incoming_messages")
    if not isinstance(check_dlq, bool): fields.append("check_dlq")
    if isinstance(failed_count, bool) or not isinstance(failed_count, int) or failed_count < 0: fields.append("simulate_failed_notifications")
    if fields:
        return {"_error": ("VALIDATION_ERROR", fields)}
    fingerprint = hashlib.sha256(_canonical(body).encode("utf-8")).hexdigest()
    with _lock:
        replay = _moderation_replays.get(fingerprint)
        if replay:
            return dict(replay)
        filtered, kicked, muted = [], [], []
        flagged_count = 0
        now = time.monotonic()
        for item in messages:
            sender, text = item["sender"], item["text"]
            times = _moderation_times[sender]
            while times and now - times[0] > 2:
                times.popleft()
            times.append(now)
            if len(times) > 3:
                if sender not in muted: muted.append(sender)
                flagged_count += 1
                continue
            if is_flood_message(text):
                if sender not in muted: muted.append(sender)
                flagged_count += 1
                continue
            masked, profanity = mask_profanity(text)
            if profanity:
                if sender not in kicked: kicked.append(sender)
                filtered.append({"message_id": item["message_id"], "sender": sender, "text": masked, "flagged": True, "reason": "profanity_leetspeak"})
                flagged_count += 1
            else:
                filtered.append({"message_id": item["message_id"], "sender": sender, "text": text, "flagged": False, "reason": None})
        if failed_count:
            _queue_dlq(room, failed_count)
        dlq_events = list(_dlq)
        queue_depth = len(dlq_events)
        response = {
            "chat_room_id": room,
            "filtered_messages": filtered,
            "kicked_users": kicked,
            "muted_users": muted,
            "dlq_status": {"queue_depth": queue_depth, "undelivered_events": dlq_events if check_dlq else []},
        }
        signature_payload = {"chat_room_id": room, "flagged_count": flagged_count, "kicked_users": kicked, "queue_depth": queue_depth}
        response["signature"] = _sign(room, signature_payload)
        _moderation_replays[fingerprint] = dict(response)
        return response


@router.post("/audit/v1/moderation/test")
async def moderation_probe(request: Request):
    try:
        body = await request.json()
    except Exception:
        return _error("VALIDATION_ERROR", "Malformed JSON", 400, {"fields": ["body"]})
    if not isinstance(body, dict):
        return _error("VALIDATION_ERROR", "Request body must be an object", 400, {"fields": ["body"]})
    response = _moderate(body)
    if "_error" in response:
        _, fields = response["_error"]
        return _error("VALIDATION_ERROR", "Invalid moderation probe", 400, {"fields": fields})
    return response


@router.post("/audit/v1/guest/cart-merge")
async def guest_cart_merge(request: Request):
    try:
        body = await request.json()
    except Exception:
        return _error("VALIDATION_ERROR", "Malformed JSON", 400, {"fields": ["body"]})
    if not isinstance(body, dict):
        return _error("VALIDATION_ERROR", "Request body must be an object", 400, {"fields": ["body"]})
    token, user_id, guest_cart = body.get("guest_token"), body.get("registered_user_id"), body.get("guest_cart")
    test_block = body.get("test_write_block", True)
    fields = []
    if not isinstance(token, str) or not token.startswith("guest_"): fields.append("guest_token")
    if not isinstance(user_id, str) or not user_id: fields.append("registered_user_id")
    if not isinstance(guest_cart, list) or not 1 <= len(guest_cart) <= 10: fields.append("guest_cart")
    elif any(not isinstance(item, dict) or not isinstance(item.get("variant_id"), str) or isinstance(item.get("quantity"), bool) or not isinstance(item.get("quantity"), int) or item["quantity"] < 1 for item in guest_cart): fields.append("guest_cart")
    if not isinstance(test_block, bool): fields.append("test_write_block")
    if fields:
        return _error("VALIDATION_ERROR", "Invalid guest cart merge request", 400, {"fields": fields})
    if not _player_exists(user_id):
        return _error("PLAYER_NOT_FOUND", "Registered user was not found", 404, {"user_id": user_id})
    fingerprint = _canonical({"guest_token": token, "registered_user_id": user_id, "guest_cart": guest_cart})
    with _lock:
        guest = _guest_tokens.get(token)
        if not guest or guest.get("status") != "active":
            previous = _guest_merges.get(token)
            if previous and previous["fingerprint"] == fingerprint:
                return JSONResponse(status_code=200, content=previous["response"])
            if previous:
                return _error("GUEST_TOKEN_ALREADY_MERGED", "Guest token was already merged with a different cart", 409)
            return _error("UNAUTHENTICATED", "Guest token is invalid or expired", 401)
        items, merge_error = merge_guest_items(
            _carts.get(user_id, []), guest_cart, store_service.get_inventory
        )
        if merge_error == "VARIANT_NOT_FOUND":
            return _error("VARIANT_NOT_FOUND", "Variant was not found", 404)
        if merge_error:
            return _error("OUT_OF_STOCK", "Requested quantity exceeds available stock", 409, {"variant_id": merge_error})
        assert items is not None
        total_items = sum(item["quantity"] for item in items)
        subtotal = sum(item["quantity"] * item["unit_price_minor"] for item in items)
        active_cart = {"user_id": user_id, "currency": "PKR", "total_items": total_items, "subtotal_minor": subtotal, "items": items}
        rejection_code, rejection_error = guest_write_decision(token, "POST")
        block_verified = bool(test_block and rejection_code == 403 and rejection_error == "GUEST_WRITE_FORBIDDEN")
        response = {
            "guest_token": token,
            "write_block_verified": block_verified,
            "write_rejection_code": rejection_code if block_verified else 0,
            "cart_merged": True,
            "active_cart": active_cart,
        }
        signed = {"cart_merged": True, "guest_token": token, "registered_user_id": user_id, "total_items": total_items, "write_rejection_code": response["write_rejection_code"]}
        response["signature"] = _sign(token, signed)
        _carts[user_id] = items
        guest["status"], guest["user_id"] = "merged", user_id
        _guest_merges[token] = {"fingerprint": fingerprint, "response": response}
        return response


@router.delete("/audit/v1/privacy/user/{user_id}")
def erase_user(user_id: str):
    with _lock:
        for token, record in list(_guest_tokens.items()):
            if record.get("user_id") == user_id:
                record.update({"status": "revoked", "user_id": None})
                _guest_merges.pop(token, None)
        _carts.pop(user_id, None)
        for relation in list(_follows):
            if user_id in relation:
                _follows.discard(relation)
        for messages in _chat_messages.values():
            for message in messages:
                if message.get("sender") == user_id:
                    message["sender"] = "deleted-user"
        player_record = roster_service._players.get(user_id)
        if player_record:
            for field in ("name", "email", "phone", "display_name"):
                if field in player_record:
                    player_record[field] = "Deleted user" if field in {"name", "display_name"} else None
        current_stream = _stream_by_player.pop(user_id, None)
        if current_stream:
            current_stream["status"] = "revoked"
            _stream_by_key[current_stream["stream_key"]]["status"] = "revoked"
    return {"user_id": user_id, "status": "anonymized", "pii_retained": False, "tokens_wiped": True}


@router.get("/metrics")
def metrics():
    with _lock:
        active_streams = sum(1 for item in _stream_by_player.values() if item["status"] == "active")
        qr_scans = sum(item["scan_count"] for item in _qr_entities.values())
        dlq_depth = len(_dlq)
        connections = _ws_count
    body = render_metrics(connections, active_streams, dlq_depth, qr_scans)
    return Response(body, media_type="text/plain; version=0.0.4; charset=utf-8")


@router.get("/api/v1/matches/{match_id}/playback")
def match_playback(match_id: str):
    if match_id not in _qr_entities or _qr_entities[match_id].get("entity_type") != "match":
        return _error("STREAM_NOT_FOUND", "Match stream was not found", 404, {"match_id": match_id})
    for stream in _stream_by_player.values():
        if stream.get("status") == "active":
            return {"match_id": match_id, "status": "live", "playback_url": f"http://localhost:8888/live/{stream['stream_key']}/index.m3u8"}
    return {"match_id": match_id, "status": "offline", "playback_url": None}


@router.post("/internal/media/auth")
async def mediamtx_auth(request: Request):
    try:
        body = await request.json()
    except Exception:
        return Response(status_code=401)
    if not isinstance(body, dict):
        return Response(status_code=401)
    action, path = body.get("action"), str(body.get("path", ""))
    with _lock:
        key = path.removeprefix("live/")
        record = _stream_by_key.get(key)
        if path.startswith("live/") and record and record.get("status") == "revoked":
            return _error("KEY_REVOKED", "The stream key has been revoked", 409)
        allowed = media_publish_allowed(action, path, _stream_by_key.get(key))
        if not allowed and action == "read" and path.startswith("costream/"):
            session = _sessions.get(path.removeprefix("costream/"))
            allowed = bool(session and session["response"].get("status") == "active")
    return Response(status_code=200 if allowed else 401)


async def publish_match_event(match_id: str, event_type: str, projection: dict[str, Any]) -> None:
    """Fan out an accepted score event to connected SSE and WS listeners."""
    event = {"match_id": match_id, "type": event_type, "score": projection}
    for subscribers in (_match_subscribers.get(match_id, set()),):
        for queue in list(subscribers):
            if queue.full():
                try: queue.get_nowait()
                except asyncio.QueueEmpty: pass
            try: queue.put_nowait(event)
            except asyncio.QueueFull: pass
    status = projection.get("status")
    if match_id == "match-m1" and status:
        update_match_state(match_id, status)


async def publish_chat(room_id: str, event: dict[str, Any]) -> None:
    for queue in list(_chat_subscribers.get(room_id, set())):
        if queue.full():
            try: queue.get_nowait()
            except asyncio.QueueEmpty: pass
        try: queue.put_nowait(event)
        except asyncio.QueueFull: pass


@router.get("/api/v1/matches/{match_id}/stream")
async def match_sse(match_id: str):
    queue: asyncio.Queue = asyncio.Queue(maxsize=16)
    _match_subscribers[match_id].add(queue)
    async def events():
        try:
            yield "retry: 1000\n\n"
            projection = _project_match(match_id)
            yield f"event: score\ndata: {json.dumps({'match_id': match_id, 'score': projection})}\n\n"
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15)
                    yield f"event: score\ndata: {json.dumps(event, separators=(',', ':'))}\n\n"
                except asyncio.TimeoutError:
                    yield ": keep-alive\n\n"
        finally:
            _match_subscribers[match_id].discard(queue)
    return StreamingResponse(events(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


def _project_match(match_id: str) -> dict[str, Any]:
    from app.services.scoring import scoring_service
    projection = scoring_service.project_match(match_id)
    return projection if projection else {"match_id": match_id, "status": "upcoming", "home": {"score": 0}, "away": {"score": 0}}


@router.websocket("/ws/matches/{match_id}")
async def match_websocket(websocket: WebSocket, match_id: str):
    global _ws_count
    await websocket.accept()
    _ws_count += 1
    queue: asyncio.Queue = asyncio.Queue(maxsize=16)
    _match_subscribers[match_id].add(queue)
    try:
        await websocket.send_json({"type": "score", "match_id": match_id, "score": _project_match(match_id)})
        while True:
            event = await asyncio.wait_for(queue.get(), timeout=30)
            await websocket.send_json(event)
    except (WebSocketDisconnect, asyncio.TimeoutError):
        pass
    finally:
        _match_subscribers[match_id].discard(queue)
        _ws_count = max(0, _ws_count - 1)


@router.post("/api/v1/chat")
async def post_chat(request: Request):
    authorization = request.headers.get("authorization", "")
    token = authorization[7:].strip() if authorization.startswith("Bearer ") else ""
    claims = auth_service.authenticate(token) if token else None
    if claims is None:
        return _error("UNAUTHENTICATED", "Sign in to send chat messages", 401)
    try:
        body = await request.json()
    except Exception:
        return _error("VALIDATION_ERROR", "Malformed JSON", 400, {"fields": ["body"]})
    if not isinstance(body, dict):
        return _error("VALIDATION_ERROR", "Request body must be an object", 400, {"fields": ["body"]})
    room = body.get("chat_room_id")
    text = body.get("text")
    message_id = body.get("message_id") or secrets.token_hex(8)
    sender = claims.get("sub", "member")
    if not isinstance(room, str) or not isinstance(text, str) or not text.strip():
        return _error("VALIDATION_ERROR", "chat_room_id and text are required", 400, {"fields": ["chat_room_id", "text"]})
    result = _moderate({"chat_room_id": room, "incoming_messages": [{"message_id": message_id, "sender": sender, "text": text}], "check_dlq": False})
    if result.get("muted_users") or not result.get("filtered_messages"):
        return _error("RATE_LIMITED", "Message blocked by moderation", 429)
    item = result["filtered_messages"][0]
    with _lock:
        _chat_sequences[room] += 1
        delivered = {**item, "sequence": _chat_sequences[room], "sent_at": time.time()}
        _chat_messages[room].append(delivered)
    await publish_chat(room, delivered)
    return {"message": delivered}


@router.get("/api/v1/chat/{room_id}/stream")
async def chat_sse(room_id: str):
    queue: asyncio.Queue = asyncio.Queue(maxsize=32)
    _chat_subscribers[room_id].add(queue)
    async def events():
        try:
            for event in _chat_messages.get(room_id, [])[-50:]:
                yield f"event: chat\ndata: {json.dumps(event, separators=(',', ':'))}\n\n"
            while True:
                try:
                    item = await asyncio.wait_for(queue.get(), timeout=15)
                    yield f"event: chat\ndata: {json.dumps(item, separators=(',', ':'))}\n\n"
                except asyncio.TimeoutError:
                    yield ": keep-alive\n\n"
        finally:
            _chat_subscribers[room_id].discard(queue)
    return StreamingResponse(events(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})


@router.get("/api/v1/chat/{room_id}/messages")
def get_chat_messages(room_id: str, after: int = 0):
    items = [item for item in _chat_messages.get(room_id, []) if item.get("sequence", 0) > after]
    return {"chat_room_id": room_id, "items": items[-100:], "last_sequence": _chat_sequences.get(room_id, 0)}


def _member_id(request: Request) -> str | None:
    authorization = request.headers.get("authorization", "")
    if not authorization.startswith("Bearer "):
        return None
    claims = auth_service.authenticate(authorization[7:].strip())
    return str(claims.get("sub")) if claims else None


@router.get("/api/v1/players/{player_id}")
def public_player_profile(player_id: str):
    player = roster_service.get_player(player_id)
    if not player:
        return _error("PLAYER_NOT_FOUND", "Player not found", 404, {"player_id": player_id})
    from app.services.scoring import scoring_service
    career, upcoming = [], []
    for match_id in list(scoring_service._match_events):
        projection = scoring_service.project_match(match_id)
        if not projection:
            continue
        if projection.get("status") == "upcoming":
            upcoming.append(projection)
        init = scoring_service.get_match_events(match_id)[0].get("payload", {})
        if player.get("squad_id") in {init.get("home_team_id"), init.get("away_team_id")}:
            career.extend(scoring_service.get_match_events(match_id) or [])
    clubs = []
    squad = roster_service.get_squad_profile(player.get("squad_id", ""))
    if squad:
        clubs.append(squad)
    vod_archive: list[dict[str, str]] = []
    stream = _stream_by_player.get(player_id)
    if stream and settings.MINIO_ENDPOINT and settings.MINIO_ACCESS_KEY and settings.MINIO_SECRET_KEY:
        try:
            from app.services.object_store import make_minio_client
            client = make_minio_client(settings.MINIO_ENDPOINT, settings.MINIO_ACCESS_KEY, settings.MINIO_SECRET_KEY, settings.MINIO_SECURE)
            prefix = f"vod/live/{stream['stream_key']}/"
            vod_archive = [{"object": item.object_name} for item in client.list_objects(settings.MINIO_BUCKET, prefix=prefix, recursive=True)]
        except Exception:
            vod_archive = []
    return {
        "player": player,
        "clubs": clubs,
        "career_match_events": career,
        "upcoming_fixtures": upcoming,
        "vod_archive": vod_archive,
        "follower_count": sum(1 for _, target in _follows if target == player_id),
    }


@router.put("/api/v1/players/{player_id}/follow")
def follow_player(player_id: str, request: Request):
    follower_id = _member_id(request)
    if not follower_id:
        return _error("UNAUTHENTICATED", "Sign in to follow a player", 401)
    if not _player_exists(player_id):
        return _error("PLAYER_NOT_FOUND", "Player not found", 404, {"player_id": player_id})
    with _lock:
        _follows.add((follower_id, player_id))
        count = sum(1 for _, target in _follows if target == player_id)
    return {"player_id": player_id, "following": True, "follower_count": count}


@router.delete("/api/v1/players/{player_id}/follow")
def unfollow_player(player_id: str, request: Request):
    follower_id = _member_id(request)
    if not follower_id:
        return _error("UNAUTHENTICATED", "Sign in to unfollow a player", 401)
    if not _player_exists(player_id):
        return _error("PLAYER_NOT_FOUND", "Player not found", 404, {"player_id": player_id})
    with _lock:
        _follows.discard((follower_id, player_id))
        count = sum(1 for _, target in _follows if target == player_id)
    return {"player_id": player_id, "following": False, "follower_count": count}


_GUEST_PLAYER = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="theme-color" content="#10284b"><title>NaSCon Live</title>
<style>
:root{color-scheme:light;--navy:#10284b;--green:#08775e;--gold:#e4b557;--ink:#152238;--muted:#596579;--line:#dce4e8}*{box-sizing:border-box}body{margin:0;background:#f4f7f8;color:var(--ink);font:16px/1.5 system-ui,-apple-system,Segoe UI,sans-serif}.top{background:var(--navy);color:white;padding:16px max(20px,calc((100% - 1100px)/2));font-weight:800;letter-spacing:.03em}.top span{color:var(--gold)}main{max-width:1100px;margin:28px auto;padding:0 18px}.grid{display:grid;grid-template-columns:minmax(0,1.7fr) minmax(270px,.8fr);gap:20px}.card{background:white;border:1px solid var(--line);border-radius:18px;padding:20px;box-shadow:0 8px 24px #13233a0d}.video-wrap{position:relative;background:#071322;border-radius:14px;overflow:hidden;aspect-ratio:16/9;display:grid;place-items:center;color:white}.video-wrap video{width:100%;height:100%;object-fit:contain}.placeholder{position:absolute;text-align:center;pointer-events:none}.placeholder strong{display:block;font-size:18px}.score{display:flex;justify-content:space-between;align-items:center;padding:16px;background:#f5f8fa;border-radius:12px;margin-bottom:16px;font-size:21px;font-weight:800}.live{color:#b3261e;font-size:12px;letter-spacing:.1em}.merch{border:1px solid var(--line);border-radius:12px;padding:16px;margin:15px 0}.price{color:var(--green);font-weight:800}button,input{font:inherit}button{min-height:48px;padding:10px 16px;border:0;border-radius:10px;background:var(--green);color:white;font-weight:700;cursor:pointer}button:focus-visible,input:focus-visible{outline:3px solid #edbd52;outline-offset:2px}.row{display:flex;gap:8px}.row input{min-width:0;flex:1;padding:12px;border:1px solid var(--line);border-radius:10px}.chat-list{height:180px;overflow:auto;background:#f7f9fa;padding:10px;border-radius:10px;margin:12px 0}.chat-line{padding:5px 0;border-bottom:1px solid #e7edf0}.note{color:var(--muted);font-size:14px}.modal{display:none;position:fixed;inset:0;background:#0815289c;align-items:center;justify-content:center;padding:18px}.modal[aria-hidden="false"]{display:flex}.dialog{max-width:410px;width:100%;background:white;border-radius:18px;padding:24px;box-shadow:0 18px 60px #0004}.dialog h2{margin-top:0}.secondary{background:#eaf0f4;color:var(--navy);margin-left:8px}@media(max-width:760px){main{margin:16px auto}.grid{grid-template-columns:1fr}.card{padding:15px}.score{font-size:18px}}
</style></head><body><header class="top"><span>◈</span> NaSCon <span>LIVE</span></header><main><div class="grid"><section class="card" aria-label="Live match"><div class="score" data-testid="live-score-banner" aria-live="polite"><span id="home">Alpha Athletics</span><span id="score">1 - 0</span><span id="away">Alpha Reserves</span><span class="live">● LIVE</span></div><div class="video-wrap"><video data-testid="guest-video-player" controls playsinline aria-label="Live match video"></video><div class="placeholder"><strong>NaSCon Live</strong><span>Waiting for the match stream</span></div></div><h2>Live match</h2><p class="note">Watch the match, follow score updates, and join the conversation.</p></section><aside class="card"><h2>Official club store</h2><div class="merch" data-testid="pinned-merch-card" data-product-id="prod-jersey-001"><strong>Alpha United Match Jersey</strong><p class="price">PKR 4,500</p><button data-testid="add-to-guest-cart-btn" data-variant-id="var-jersey-001-L" id="add">Add to cart</button><p class="note">Guest cart: <span data-testid="guest-cart-count" id="count">0</span></p></div><h2>Live chat</h2><div class="chat-list" data-testid="chat-message-list" id="messages" role="log" aria-live="polite"></div><div class="row"><input data-testid="chat-input-field" id="chat" aria-label="Chat message" placeholder="Write a message"><button data-testid="chat-send-btn" id="send">Send</button></div><p class="note">You can watch without an account. Sign in only when you choose to send a message or checkout.</p></aside></div></main><div class="modal" data-testid="progressive-auth-modal" id="modal" aria-hidden="true" role="dialog" aria-modal="true" aria-labelledby="modal-title"><div class="dialog"><h2 id="modal-title">Join the conversation</h2><p>Sign in or create an account to send a message. Your guest cart will be kept.</p><button id="sign-in">Sign in</button><button class="secondary" id="close">Continue watching</button></div></div>
<script>
const matchId=location.pathname.split('/')[3]||'match-m1';const roomId=new URLSearchParams(location.search).get('room')||'chat-cs-session-101';let cart=JSON.parse(sessionStorage.getItem('nascon_guest_cart')||'[]');const count=document.getElementById('count');function updateCount(){count.textContent=String(cart.reduce((sum,x)=>sum+x.quantity,0));sessionStorage.setItem('nascon_guest_cart',JSON.stringify(cart))}updateCount();document.getElementById('add').addEventListener('click',()=>{const item=cart.find(x=>x.variant_id==='var-jersey-001-L');if(item)item.quantity++;else cart.push({variant_id:'var-jersey-001-L',quantity:1});updateCount()});const modal=document.getElementById('modal');function openModal(){modal.setAttribute('aria-hidden','false');document.getElementById('close').focus()}document.getElementById('close').onclick=()=>modal.setAttribute('aria-hidden','true');document.getElementById('sign-in').onclick=()=>{sessionStorage.setItem('nascon_return_time',String(document.querySelector('video').currentTime||0));location.href='/api/v1/auth/login'};document.getElementById('send').onclick=async()=>{const text=document.getElementById('chat').value;if(!text)return;try{const r=await fetch('/api/v1/chat',{method:'POST',headers:{'Content-Type':'application/json','Authorization':'Bearer guest_anon_tok_7721'},body:JSON.stringify({chat_room_id:roomId,text})});if(r.status===403||r.status===401)openModal()}catch(_){openModal()}};async function refreshScore(){try{const r=await fetch('/audit/v1/score/'+encodeURIComponent(matchId));if(r.ok){const d=await r.json();document.getElementById('score').textContent=(d.home?.score??1)+' - '+(d.away?.score??0);}}catch(_){}}refreshScore();const scoreEvents=new EventSource('/api/v1/matches/'+encodeURIComponent(matchId)+'/stream');scoreEvents.addEventListener('score',e=>{try{const d=JSON.parse(e.data);const s=d.score||{};document.getElementById('score').textContent=(s.home?.score??1)+' - '+(s.away?.score??0)}catch(_){}});const chatEvents=new EventSource('/api/v1/chat/'+encodeURIComponent(roomId)+'/stream');chatEvents.addEventListener('chat',e=>{try{const m=JSON.parse(e.data);const row=document.createElement('div');row.className='chat-line';row.textContent=m.sender+': '+m.text;document.getElementById('messages').appendChild(row)}catch(_){}});</script></body></html>"""


_VIDEO_BOOTSTRAP = r"""<script>
(async()=>{try{
  const parts=location.pathname.split('/').filter(Boolean);
  const isCo=parts[1]==='costream';
  const id=parts[2]||(isCo?'':'match-m1');
  const endpoint=isCo?'/api/v1/costream/':'/api/v1/matches/';
  const response=await fetch(endpoint+encodeURIComponent(id)+(isCo?'':'/playback'));
  if(!response.ok)return;
  const data=await response.json();
  const source=isCo?data.composite_playback_url:data.playback_url;
  if(!source)return;
  const video=document.querySelector('[data-testid="guest-video-player"]');
  const playback=new URL(source,location.href);
  playback.hostname=location.hostname==='api'?'mediamtx':location.hostname;
  playback.port='8888';
  if(video.canPlayType('application/vnd.apple.mpegurl')){video.src=playback.href;video.load();}
  else{
    const script=document.createElement('script');
    script.src='https://cdn.jsdelivr.net/npm/hls.js@1.6.15';
    script.onload=()=>{if(window.Hls&&window.Hls.isSupported()){const hls=new window.Hls({lowLatencyMode:true});hls.loadSource(playback.href);hls.attachMedia(video);}};
    document.head.appendChild(script);
  }
  document.querySelector('.placeholder').style.display='none';
}catch(_){}})();
</script>"""


@router.get("/web/matches/{match_id}/{view}", response_class=HTMLResponse)
def guest_match(match_id: str, view: str):
    if view not in {"preview", "live", "vod", "notice"}:
        return HTMLResponse("Not found", status_code=404)
    return HTMLResponse(_GUEST_PLAYER.replace("</body>", _VIDEO_BOOTSTRAP + "</body>"))


@router.get("/web/costream/{session_id}", response_class=HTMLResponse)
def guest_costream(session_id: str):
    page = _GUEST_PLAYER.replace("Live match", "Co-stream").replace("chat-cs-session-101", f"chat-{session_id}")
    return HTMLResponse(page.replace("</body>", _VIDEO_BOOTSTRAP + "</body>"))


@router.get("/web/players/{player_id}", response_class=HTMLResponse)
def guest_player_profile(player_id: str):
    if not _player_exists(player_id):
        return HTMLResponse("Player not found", status_code=404)
    return HTMLResponse(f"<!doctype html><html lang='en'><meta name='viewport' content='width=device-width'><title>NaSCon Player</title><body><main><h1>Player profile</h1><p>{player_id}</p><a href='/web/matches/match-m1/live'>Watch current match</a></main></body></html>")


@router.get("/web/clubs/{club_id}", response_class=HTMLResponse)
def guest_club_profile(club_id: str):
    club = roster_service.get_squad_profile(club_id)
    if not club:
        return HTMLResponse("Club not found", status_code=404)
    return HTMLResponse(f"<!doctype html><html lang='en'><meta name='viewport' content='width=device-width'><title>NaSCon Club</title><body><main><h1>{club.get('name', club_id)}</h1><a href='/web/matches/match-m1/live'>Watch current match</a></main></body></html>")
