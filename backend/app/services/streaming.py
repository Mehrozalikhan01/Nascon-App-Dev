"""Deterministic stream-key and media authorization helpers."""

import re
import secrets
from typing import Any

STREAM_KEY_PATTERN = re.compile(r"^sk_live_[a-f0-9]{16}$")


def create_stream_key(override: str | None = None) -> str:
    """Return a competition-format key, honoring deterministic test overrides."""
    if override is not None:
        if not isinstance(override, str) or STREAM_KEY_PATTERN.fullmatch(override) is None:
            raise ValueError("Invalid stream key override")
        return override
    return f"sk_live_{secrets.token_hex(8)}"


def stream_key_is_valid(key: Any) -> bool:
    return isinstance(key, str) and STREAM_KEY_PATTERN.fullmatch(key) is not None


def ingest_endpoints(key: str) -> dict[str, str]:
    if not stream_key_is_valid(key):
        raise ValueError("Invalid stream key")
    return {
        "rtmp": f"rtmp://localhost:1935/live/{key}",
        "webrtc": f"http://localhost:8889/live/{key}/whip",
    }


def stream_signature_payload(action: str, player_id: str, status: str, key: str) -> dict[str, str]:
    return {"action": action, "player_id": player_id, "status": status, "stream_key": key}


def media_publish_allowed(action: str, path: str, stream_record: dict[str, Any] | None) -> bool:
    """Match MediaMTX's publish/read auth probe to an active stream key."""
    match = re.fullmatch(r"live/(sk_live_[a-f0-9]{16})", path)
    return bool(match and isinstance(action, str) and action in {"publish", "read"}
                and stream_record and stream_record.get("stream_key") == match.group(1)
                and stream_record.get("status") == "active")
