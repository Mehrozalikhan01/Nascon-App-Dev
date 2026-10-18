import re

import pytest

from app.services.costream import layout_capacity_error, resolve_host_drop
from app.services.moderation_dlq import is_flood_message, mask_profanity, normalize_text
from app.services.observability import render_metrics
from app.services.qr_guest import merge_guest_items, target_route
from app.services.streaming import (
    create_stream_key,
    ingest_endpoints,
    media_publish_allowed,
    stream_key_is_valid,
    stream_signature_payload,
)


def test_streaming_key_and_media_authorization_helpers():
    generated = create_stream_key()
    assert re.fullmatch(r"sk_live_[a-f0-9]{16}", generated)
    assert create_stream_key("sk_live_9f83a1b2c3d4e5f6") == "sk_live_9f83a1b2c3d4e5f6"
    assert stream_key_is_valid(generated)
    assert not stream_key_is_valid("sk_live_101")
    with pytest.raises(ValueError):
        create_stream_key("invalid")
    with pytest.raises(ValueError):
        ingest_endpoints("invalid")
    endpoints = ingest_endpoints(generated)
    assert endpoints["rtmp"].endswith(generated)
    assert endpoints["webrtc"].endswith(f"{generated}/whip")
    assert stream_signature_payload("rotate", "p1", "active", generated) == {
        "action": "rotate", "player_id": "p1", "status": "active", "stream_key": generated,
    }
    record = {"stream_key": generated, "status": "active"}
    assert media_publish_allowed("publish", f"live/{generated}", record)
    assert media_publish_allowed("read", f"live/{generated}", record)
    assert not media_publish_allowed("publish", "other/path", record)
    assert not media_publish_allowed("publish", f"live/{generated}", {**record, "status": "revoked"})
    assert not media_publish_allowed("admin", f"live/{generated}", record)
    assert not media_publish_allowed([], f"live/{generated}", record)
    assert not media_publish_allowed("read", f"live/{generated}", None)


def test_costream_capacity_and_host_handoff_branches():
    assert layout_capacity_error("solo", 2)
    assert not layout_capacity_error("solo", 1)
    assert layout_capacity_error("grid_2", 3)
    assert not layout_capacity_error("grid_2", 2)
    assert not layout_capacity_error("grid_4", 4)
    participants = ["host", "p2", "p3"]
    assert resolve_host_drop("host", participants, False) == {
        "active_host": "host", "handoff_occurred": False,
        "active_participants": participants, "status": "active",
    }
    assert resolve_host_drop("host", participants, True) == {
        "active_host": "p2", "handoff_occurred": True,
        "active_participants": ["p2", "p3"], "status": "active",
    }
    assert resolve_host_drop("host", ["host"], True) == {
        "active_host": None, "handoff_occurred": False,
        "active_participants": [], "status": "terminated",
    }


def test_qr_routes_and_inventory_safe_cart_merge():
    assert target_route("match", "m1", "upcoming") == "/web/matches/m1/preview"
    assert target_route("match", "m1", "in_progress") == "/web/matches/m1/live"
    assert target_route("match", "m1", "completed") == "/web/matches/m1/vod"
    assert target_route("match", "m1", "postponed") == "/web/matches/m1/notice?state=postponed"
    assert target_route("match", "m1", "cancelled") == "/web/matches/m1/notice?state=cancelled"
    assert target_route("match", "m1", "abandoned") == "/web/matches/m1/notice?state=abandoned"
    assert target_route("player", "p1", "active") == "/web/players/p1"
    assert target_route("club", "c1", "active") == "/web/clubs/c1"
    assert target_route("costream", "cs1", "active") == "/web/costream/cs1"
    catalogue = {"v1": {"stock": 5, "price_minor": 1250}, "v2": {"stock": 1, "price_minor": 2000}}
    merged, error = merge_guest_items(
        [{"variant_id": "v1", "quantity": 1, "unit_price_minor": 1000}],
        [{"variant_id": "v1", "quantity": 2}, {"variant_id": "v2", "quantity": 1}],
        catalogue.get,
    )
    assert error is None
    assert merged == [
        {"variant_id": "v1", "quantity": 3, "unit_price_minor": 1250},
        {"variant_id": "v2", "quantity": 1, "unit_price_minor": 2000},
    ]
    assert merge_guest_items([], [{"variant_id": "missing", "quantity": 1}], catalogue.get) == (None, "VARIANT_NOT_FOUND")
    assert merge_guest_items([], [{"variant_id": "v2", "quantity": 2}], catalogue.get) == (None, "v2")


def test_moderation_normalization_masking_and_flood_detection():
    assert normalize_text("B@D_w0rd!") == "badwordi"
    assert mask_profanity("Clean sports commentary") == ("Clean sports commentary", False)
    masked, flagged = mask_profanity("b@d_w0rd and bad_word, then sc@m")
    assert flagged
    assert masked == "*** and ***, then ***"
    assert is_flood_message("AAAA")
    assert is_flood_message("goal!!!")
    assert is_flood_message("what???")
    assert not is_flood_message("Great goal")


def test_metrics_render_valid_values_and_clamp_negatives():
    body = render_metrics(3, 2, 1, 9)
    assert "nascon_active_ws_connections 3\n" in body
    assert "nascon_active_streams_total 2\n" in body
    assert "nascon_dlq_depth 1\n" in body
    assert "nascon_qr_scans_total 9\n" in body
    assert "nascon_stream_bitrate_kbps 0\n" in body
    negative = render_metrics(-1, -2, -3, -4)
    assert "nascon_active_ws_connections 0\n" in negative
    assert "nascon_active_streams_total 0\n" in negative
    assert "nascon_dlq_depth 0\n" in negative
    assert "nascon_qr_scans_total 0\n" in negative
