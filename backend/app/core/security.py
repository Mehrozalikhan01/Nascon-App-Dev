import hmac
import hashlib
import json
import time
import jwt
from typing import Any, Dict
from app.core.config import settings

JWT_SECRET = "nascon_jwt_internal_secret_key_2026"
JWT_ALGORITHM = "HS256"

def canonical(obj: Any) -> str:
    """Canonical JSON as defined in Section 5.3 & Appendix A."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)

def sign(salt: str, id_: str, payload_obj: Any) -> str:
    """HMAC-SHA256 signature as defined in Section 5.3 & Appendix A."""
    msg = (id_ + "|" + canonical(payload_obj)).encode("utf-8")
    return hmac.new(salt.encode("utf-8"), msg, hashlib.sha256).hexdigest()

def lots_key(seed: int, league_id: str, team_id: str) -> str:
    """Deterministic draw_lots key as defined in Section 5.10 & Appendix A."""
    return hashlib.sha256(f"{seed}|{league_id}|{team_id}".encode("utf-8")).hexdigest()

def create_role_jwt(role: str, user_id: str, tenant_id: str, extra: Dict[str, Any] = None) -> str:
    now = int(time.time())
    payload = {
        "sub": user_id,
        "role": role,
        "tenant_id": tenant_id,
        "iat": now,
        "exp": now + 7200,  # 2 hours
    }
    if extra:
        payload.update(extra)
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)

def verify_role_jwt(token: str) -> Dict[str, Any]:
    try:
        # First check unverified header to reject alg: none
        unverified_header = jwt.get_unverified_header(token)
        if unverified_header.get("alg") in ("none", None, "NONE"):
            raise jwt.InvalidTokenError("alg: none rejected")
        
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        return payload
    except Exception as e:
        raise jwt.InvalidTokenError(str(e))
