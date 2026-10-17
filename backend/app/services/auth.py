import hashlib
import hmac
from typing import Any, Dict, Optional

from app.core.security import create_role_jwt


_DEMO_PASSWORD_SALT = b"nascon-demo-password-v1"
_DEMO_PASSWORD_HASH = bytes.fromhex(
    "e9453921c6df6afd1c06b8e90454bb1f41abdea119fe317a588b14986b52029e"
)
_DEMO_ROUNDS = 200_000

# These accounts are deliberately limited to the competition/demo environment.
# The password itself is documented in README; only its PBKDF2 hash is stored.
_DEMO_USERS = {
    "admin@nascon.test": {
        "user_id": "admin-01", "role": "super_admin", "tenant_id": "global", "scope": {}
    },
    "organizer@nascon.test": {
        "user_id": "org-01", "role": "league_organizer", "tenant_id": "tenant-alpha",
        "scope": {"league_id": "league-alpha-1"}
    },
    "manager@nascon.test": {
        "user_id": "mgr-01", "role": "club_manager", "tenant_id": "tenant-alpha",
        "scope": {"club_id": "club-alpha-1"}
    },
    "player@nascon.test": {
        "user_id": "player-alpha-1", "role": "player", "tenant_id": "tenant-alpha",
        "scope": {"player_id": "player-alpha-1", "club_id": "club-alpha-1"}
    },
    "viewer@nascon.test": {
        "user_id": "view-01", "role": "viewer", "tenant_id": "tenant-alpha", "scope": {}
    },
}


class AuthService:
    def login(self, email: Any, password: Any) -> Optional[Dict[str, Any]]:
        if not isinstance(email, str) or not isinstance(password, str):
            return None

        normalized_email = email.strip().lower()
        user = _DEMO_USERS.get(normalized_email)
        candidate_hash = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), _DEMO_PASSWORD_SALT, _DEMO_ROUNDS
        )
        if user is None or not hmac.compare_digest(candidate_hash, _DEMO_PASSWORD_HASH):
            return None

        token = create_role_jwt(
            user["role"], user["user_id"], user["tenant_id"], user["scope"]
        )
        return {
            "access_token": token,
            "token_type": "Bearer",
            "user": {
                "user_id": user["user_id"],
                "email": normalized_email,
                "role": user["role"],
                "tenant_id": user["tenant_id"],
                **user["scope"],
            },
        }

    @staticmethod
    def authenticate(token: str) -> Optional[Dict[str, Any]]:
        from app.core.security import verify_role_jwt

        try:
            return verify_role_jwt(token)
        except Exception:
            return None


auth_service = AuthService()
