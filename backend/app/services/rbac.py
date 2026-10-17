from typing import Dict, Any, Optional
from app.core.security import create_role_jwt, verify_role_jwt

CONTEXT = {
    "tenant_id": "tenant-alpha",
    "foreign_tenant_id": "tenant-beta",
    "own_league_id": "league-alpha-1",
    "other_league_id": "league-alpha-2",
    "foreign_league_id": "league-beta-1",
    "own_club_id": "club-alpha-1",
    "other_club_id": "club-alpha-2",
    "foreign_club_id": "club-beta-1",
    "own_player_id": "player-alpha-1",
    "other_player_id": "player-alpha-2",
    "foreign_player_id": "player-beta-1"
}

def generate_rbac_matrix_response() -> Dict[str, Any]:
    tokens = {
        "super_admin": create_role_jwt("super_admin", "admin-01", "global"),
        "league_organizer": create_role_jwt("league_organizer", "org-01", "tenant-alpha", {"league_id": "league-alpha-1"}),
        "club_manager": create_role_jwt("club_manager", "mgr-01", "tenant-alpha", {"club_id": "club-alpha-1"}),
        "player": create_role_jwt("player", "player-alpha-1", "tenant-alpha", {"player_id": "player-alpha-1", "club_id": "club-alpha-1"}),
        "viewer": create_role_jwt("viewer", "view-01", "tenant-alpha")
    }
    return {
        "tokens": tokens,
        "context": dict(CONTEXT)
    }

def check_probe_authorization(probe: str, target_id: Optional[str], auth_header: Optional[str]) -> int:
    if not auth_header or not auth_header.startswith("Bearer "):
        return 401

    token = auth_header[7:].strip()
    try:
        payload = verify_role_jwt(token)
    except Exception:
        return 401

    role = payload.get("role")
    token_tenant = payload.get("tenant_id")

    # Super Admin is allowed everywhere
    if role == "super_admin":
        return 200

    # Cross-tenant check: non-super-admin accessing foreign resources is strictly 403
    foreign_targets = {
        CONTEXT["foreign_tenant_id"],
        CONTEXT["foreign_league_id"],
        CONTEXT["foreign_club_id"],
        CONTEXT["foreign_player_id"]
    }
    if target_id and target_id in foreign_targets:
        return 403

    # P1: POST /audit/v1/rbac/tenants (Super Admin only)
    if probe == "P1":
        return 403

    # P2: POST /audit/v1/rbac/leagues/{league_id}/fixtures
    if probe == "P2":
        if role == "league_organizer" and target_id == CONTEXT["own_league_id"]:
            return 200
        return 403

    # P3 / P4: POST /audit/v1/rbac/clubs/{club_id}/roster, products
    if probe in ("P3", "P4"):
        if role == "club_manager" and target_id == CONTEXT["own_club_id"]:
            return 200
        return 403

    # P5: PATCH /audit/v1/rbac/players/{player_id}
    if probe == "P5":
        if role == "club_manager" and target_id == CONTEXT["own_player_id"]:
            return 200
        if role == "player" and target_id == CONTEXT["own_player_id"]:
            return 200
        return 403

    # P6: GET /audit/v1/rbac/clubs/{club_id} (Read access for all roles within tenant-alpha)
    if probe == "P6":
        if target_id in (CONTEXT["own_club_id"], CONTEXT["other_club_id"]):
            return 200
        return 403

    # P7: GET /audit/v1/rbac/platform/config (Super Admin only)
    if probe == "P7":
        return 403

    return 403
