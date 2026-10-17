import time
import collections
from fastapi import FastAPI, Request, Response, status
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from typing import Dict, Any, Optional

from app.core.config import settings
from app.services.sports import sports_service
from app.services.fixtures import (
    generate_round_robin, generate_swiss, generate_single_elim,
    generate_double_elim, NoValidPairingException
)
from app.services.scoring import scoring_service
from app.services.standings import standings_service
from app.services.roster import roster_service
from app.services.store import store_service
from app.services.rbac import generate_rbac_matrix_response, check_probe_authorization
from app.services.sync import offline_sync_service

app = FastAPI(title="NaSCon 26 Multi-Sport Platform")

# Rolling 10s Rate Limiter for /api/v1/*
ip_request_timestamps = collections.defaultdict(list)

def make_error(code: str, message: str, status_code: int, details: Optional[Dict[str, Any]] = None):
    return JSONResponse(
        status_code=status_code,
        content={
            "error": {
                "code": code,
                "message": message,
                "details": details or {}
            }
        }
    )

@app.middleware("http")
async def validation_and_security_middleware(request: Request, call_next):
    # Check body size limit 1 MB
    content_length = request.headers.get("content-length")
    if content_length and int(content_length) > 1024 * 1024:
        return make_error("PAYLOAD_TOO_LARGE", "Request body exceeds 1 MB limit.", 413)

    # Rate limiting for /api/v1/* (100 req / rolling 10s)
    if request.url.path.startswith("/api/v1/"):
        client_ip = request.client.host if request.client else "127.0.0.1"
        now = time.time()
        timestamps = ip_request_timestamps[client_ip]
        # Keep only timestamps in last 10s
        ip_request_timestamps[client_ip] = [ts for ts in timestamps if now - ts < 10.0]
        if len(ip_request_timestamps[client_ip]) >= 100:
            return JSONResponse(
                status_code=429,
                headers={"Retry-After": "10"},
                content={
                    "error": {
                        "code": "RATE_LIMITED",
                        "message": "Rate limit exceeded (100 req per rolling 10 s).",
                        "details": {}
                    }
                }
            )
        ip_request_timestamps[client_ip].append(now)

    response = await call_next(request)
    return response

# --- HEALTH ---
@app.get("/health")
def health_probe():
    return {"status": "ok"}

# --- RESET ---
@app.post("/audit/v1/reset")
async def reset_baseline(request: Request):
    body = {}
    try:
        body = await request.json()
    except Exception:
        pass

    seed = body.get("seed", 42)
    stock_map = body.get("stock", {"var-jersey-001-M": 1, "var-jersey-001-L": 5})

    # Reset all services
    sports_service.init_baseline()
    scoring_service.reset()
    standings_service.reset()
    roster_service.reset()
    store_service.init_baseline(stock_map)
    offline_sync_service.reset()

    # Pre-seed baseline RBAC scenario
    roster_service.setup_roster({
        "league": {
            "league_id": "league-alpha-1",
            "tenant_id": "tenant-alpha",
            "sport_id": "football"
        },
        "squads": [
            {"squad_id": "club-alpha-1", "club_id": "club-alpha-1", "tenant_id": "tenant-alpha", "name": "Alpha 1"},
            {"squad_id": "club-alpha-2", "club_id": "club-alpha-2", "tenant_id": "tenant-alpha", "name": "Alpha 2"}
        ],
        "players": [
            {"player_id": "player-alpha-1", "name": "Player A1", "squad_id": "club-alpha-1", "role": "starter"},
            {"player_id": "player-alpha-2", "name": "Player A2", "squad_id": "club-alpha-2", "role": "starter"}
        ]
    })
    roster_service.setup_roster({
        "league": {
            "league_id": "league-beta-1",
            "tenant_id": "tenant-beta",
            "sport_id": "football"
        },
        "squads": [
            {"squad_id": "club-beta-1", "club_id": "club-beta-1", "tenant_id": "tenant-beta", "name": "Beta 1"}
        ],
        "players": [
            {"player_id": "player-beta-1", "name": "Player B1", "squad_id": "club-beta-1", "role": "starter"}
        ]
    })

    return {
        "status": "reset_complete",
        "seed": seed,
        "sports": sports_service.get_all(),
        "tenants": ["tenant-alpha", "tenant-beta"],
        "stock": {
            "var-jersey-001-M": store_service.get_inventory("var-jersey-001-M")["stock"],
            "var-jersey-001-L": store_service.get_inventory("var-jersey-001-L")["stock"]
        }
    }

# --- SPORTS ---
@app.post("/audit/v1/sports")
async def create_sport(request: Request):
    try:
        body = await request.json()
    except Exception:
        return make_error("VALIDATION_ERROR", "Malformed JSON", 400)

    res = sports_service.register_sport(body)
    if "error" in res:
        if res["error"] == "SPORT_EXISTS":
            return make_error("SPORT_EXISTS", f"Sport already exists", 409)
        return make_error("VALIDATION_ERROR", "Validation error", 400, {"fields": res.get("fields", [])})

    return JSONResponse(status_code=201, content={
        "sport_id": res["sport_id"],
        "status": "created",
        "config": res["config"]
    })

# --- FIXTURES ---
@app.post("/audit/v1/fixtures")
async def generate_fixtures(request: Request):
    try:
        body = await request.json()
    except Exception:
        return make_error("VALIDATION_ERROR", "Malformed JSON", 400)

    fmt = body.get("format")
    teams = body.get("teams", [])
    params = body.get("params", {})

    if not fmt or not teams or not isinstance(teams, list):
        return make_error("VALIDATION_ERROR", "Invalid teams or format", 400, {"fields": ["teams", "format"]})

    try:
        if fmt == "round_robin":
            res = generate_round_robin(teams, params)
        elif fmt == "swiss":
            res = generate_swiss(teams, params)
        elif fmt == "single_elim":
            res = generate_single_elim(teams, params)
        elif fmt == "double_elim":
            res = generate_double_elim(teams, params)
        else:
            return make_error("VALIDATION_ERROR", f"Unknown format: {fmt}", 400, {"fields": ["format"]})
        return res
    except NoValidPairingException as e:
        return make_error("NO_VALID_PAIRING", "No rematch-free Swiss pairing exists", 409, {"round": e.round_num})
    except ValueError as e:
        return make_error("VALIDATION_ERROR", str(e), 400)

# --- LEAGUES ---
@app.post("/audit/v1/leagues")
async def register_league(request: Request):
    try:
        body = await request.json()
    except Exception:
        return make_error("VALIDATION_ERROR", "Malformed JSON", 400)

    res = standings_service.register_league(body)
    if "error" in res:
        err = res["error"]
        if err == "SPORT_NOT_FOUND":
            return make_error("SPORT_NOT_FOUND", "Referenced sport does not exist", 404)
        if err == "LEAGUE_EXISTS":
            return make_error("LEAGUE_EXISTS", "League already exists", 409)
        if err == "MATCH_EXISTS":
            return make_error("MATCH_EXISTS", "Match ID already exists", 409)
        return make_error("VALIDATION_ERROR", "Validation error", 400, {"fields": res.get("fields", [])})

    return JSONResponse(status_code=201, content={
        "league_id": res["league_id"],
        "status": "created",
        "team_count": res["team_count"],
        "fixture_count": res["fixture_count"]
    })

# --- EVENTS ---
@app.post("/audit/v1/events")
async def append_event(request: Request):
    try:
        body = await request.json()
    except Exception:
        return make_error("VALIDATION_ERROR", "Malformed JSON", 400)

    eid = body.get("event_id")
    mid = body.get("match_id")
    etype = body.get("type")
    payload = body.get("payload", {})

    if not eid or not mid or not etype:
        return make_error("VALIDATION_ERROR", "Missing required fields", 400, {"fields": ["event_id", "match_id", "type"]})

    res = scoring_service.append_event(eid, mid, etype, payload)
    if "error" in res:
        err = res["error"]
        status_map = {
            "SPORT_NOT_FOUND": 404,
            "MATCH_NOT_FOUND": 404,
            "EVENT_NOT_FOUND": 404,
            "MATCH_EXISTS": 409,
            "DUPLICATE_EVENT_ID": 409,
            "ILLEGAL_TRANSITION": 409,
            "MATCH_NOT_IN_PROGRESS": 409,
            "TIE_NOT_ALLOWED": 409,
            "INNINGS_COMPLETE": 409,
            "SERIES_ALREADY_DECIDED": 409,
            "INVALID_COMPENSATION": 409,
            "UNKNOWN_EVENT_TYPE": 400,
            "VALIDATION_ERROR": 400
        }
        st = status_map.get(err, 400)
        return make_error(err, f"Event error: {err}", st, {"fields": res.get("fields", [])} if err == "VALIDATION_ERROR" else {})

    status_code = 200 if res.get("is_replay") else 201
    return JSONResponse(status_code=status_code, content={
        "event_id": res["event_id"],
        "match_id": res["match_id"],
        "type": res["type"],
        "sequence": res["sequence"],
        "match_version": res["match_version"],
        "signature": res["signature"]
    })

@app.get("/audit/v1/score/{match_id}")
def get_score(match_id: str):
    proj = scoring_service.project_match(match_id)
    if not proj:
        return make_error("MATCH_NOT_FOUND", f"Match {match_id} not found", 404)
    return proj

# --- STANDINGS ---
@app.get("/audit/v1/standings/{league_id}")
def get_standings(league_id: str):
    res = standings_service.compute_standings(league_id)
    if not res:
        return make_error("LEAGUE_NOT_FOUND", f"League {league_id} not found", 404)
    return res

# --- ROSTER ---
@app.post("/audit/v1/roster/setup")
async def setup_roster(request: Request):
    try:
        body = await request.json()
    except Exception:
        return make_error("VALIDATION_ERROR", "Malformed JSON", 400)
    return JSONResponse(status_code=201, content=roster_service.setup_roster(body))

@app.post("/audit/v1/roster/transfer")
async def transfer_roster(request: Request):
    try:
        body = await request.json()
    except Exception:
        return make_error("VALIDATION_ERROR", "Malformed JSON", 400)

    res = roster_service.transfer_player(body)
    if "error" in res:
        err = res["error"]
        status_map = {
            "VALIDATION_ERROR": 400,
            "LEAGUE_NOT_FOUND": 404,
            "PLAYER_NOT_FOUND": 404,
            "SQUAD_NOT_FOUND": 404,
            "CROSS_TENANT_TRANSFER": 409,
            "NOT_IN_SQUAD": 409,
            "TRANSFER_WINDOW_CLOSED": 409,
            "PLAYER_ON_LOAN": 409,
            "SQUAD_FULL": 409,
            "MIN_POSITION_VIOLATION": 409
        }
        st = status_map.get(err, 400)
        return make_error(err, f"Transfer error: {err}", st, {"fields": res.get("fields", [])} if err == "VALIDATION_ERROR" else {})

    st_code = 200 if res.get("is_replay") else 201
    return JSONResponse(status_code=st_code, content=res["response"])

@app.get("/audit/v1/roster/history/{player_id}")
def get_roster_history(player_id: str, as_of: Optional[str] = None):
    res = roster_service.get_history(player_id, as_of)
    if not res:
        return make_error("PLAYER_NOT_FOUND", f"Player {player_id} not found", 404)
    return res

# --- STORE & CHECKOUT ---
@app.post("/audit/v1/checkout")
async def checkout(request: Request):
    try:
        body = await request.json()
    except Exception:
        return make_error("VALIDATION_ERROR", "Malformed JSON", 400)

    res = store_service.checkout(body)
    if "error" in res:
        err = res["error"]
        if err == "VALIDATION_ERROR":
            return make_error("VALIDATION_ERROR", "Validation error", 400, {"fields": res.get("fields", [])})
        if err == "INVALID_DISCOUNT_CODE":
            return make_error("INVALID_DISCOUNT_CODE", "Invalid discount code", 400)
        if err == "VARIANT_NOT_FOUND":
            return make_error("VARIANT_NOT_FOUND", f"Variant {res.get('variant_id')} not found", 404)
        if err == "DUPLICATE_ORDER_ID":
            return make_error("DUPLICATE_ORDER_ID", "Duplicate order ID with different body", 409)
        if err == "OUT_OF_STOCK":
            return make_error("OUT_OF_STOCK", "Variant has no stock left", 409, {"variant_id": res.get("variant_id")})
        if err == "PAYMENT_DECLINED":
            return make_error("PAYMENT_DECLINED", "Payment authorization declined", 402)
        if err == "PAYMENT_CAPTURE_FAILED":
            return make_error("PAYMENT_CAPTURE_FAILED", "Payment capture failed", 402)

    st_code = 200 if res.get("is_replay") else 201
    return JSONResponse(status_code=st_code, content=res["response"])

@app.get("/audit/v1/inventory/{variant_id}")
def get_inventory(variant_id: str):
    inv = store_service.get_inventory(variant_id)
    if not inv:
        return make_error("VARIANT_NOT_FOUND", f"Variant {variant_id} not found", 404)
    return inv

# --- RBAC ---
@app.post("/audit/v1/rbac-matrix")
def get_rbac_matrix():
    return generate_rbac_matrix_response()

@app.post("/audit/v1/rbac/tenants")
def rbac_p1(request: Request):
    auth = request.headers.get("authorization")
    st = check_probe_authorization("P1", None, auth)
    if st == 200:
        return {"authorized": True}
    if st == 401:
        return make_error("UNAUTHENTICATED", "Unauthenticated", 401)
    return make_error("FORBIDDEN", "Forbidden", 403)

@app.post("/audit/v1/rbac/leagues/{league_id}/fixtures")
def rbac_p2(league_id: str, request: Request):
    auth = request.headers.get("authorization")
    st = check_probe_authorization("P2", league_id, auth)
    if st == 200:
        return {"authorized": True}
    if st == 401:
        return make_error("UNAUTHENTICATED", "Unauthenticated", 401)
    return make_error("FORBIDDEN", "Forbidden", 403)

@app.post("/audit/v1/rbac/clubs/{club_id}/roster")
def rbac_p3(club_id: str, request: Request):
    auth = request.headers.get("authorization")
    st = check_probe_authorization("P3", club_id, auth)
    if st == 200:
        return {"authorized": True}
    if st == 401:
        return make_error("UNAUTHENTICATED", "Unauthenticated", 401)
    return make_error("FORBIDDEN", "Forbidden", 403)

@app.post("/audit/v1/rbac/clubs/{club_id}/products")
def rbac_p4(club_id: str, request: Request):
    auth = request.headers.get("authorization")
    st = check_probe_authorization("P4", club_id, auth)
    if st == 200:
        return {"authorized": True}
    if st == 401:
        return make_error("UNAUTHENTICATED", "Unauthenticated", 401)
    return make_error("FORBIDDEN", "Forbidden", 403)

@app.patch("/audit/v1/rbac/players/{player_id}")
def rbac_p5(player_id: str, request: Request):
    auth = request.headers.get("authorization")
    st = check_probe_authorization("P5", player_id, auth)
    if st == 200:
        return {"authorized": True}
    if st == 401:
        return make_error("UNAUTHENTICATED", "Unauthenticated", 401)
    return make_error("FORBIDDEN", "Forbidden", 403)

@app.get("/audit/v1/rbac/clubs/{club_id}")
def rbac_p6(club_id: str, request: Request):
    auth = request.headers.get("authorization")
    st = check_probe_authorization("P6", club_id, auth)
    if st == 200:
        return {"authorized": True}
    if st == 401:
        return make_error("UNAUTHENTICATED", "Unauthenticated", 401)
    return make_error("FORBIDDEN", "Forbidden", 403)

@app.get("/audit/v1/rbac/platform/config")
def rbac_p7(request: Request):
    auth = request.headers.get("authorization")
    st = check_probe_authorization("P7", None, auth)
    if st == 200:
        return {"authorized": True}
    if st == 401:
        return make_error("UNAUTHENTICATED", "Unauthenticated", 401)
    return make_error("FORBIDDEN", "Forbidden", 403)

# --- PING (Rate Limiter Probe) ---
@app.get("/api/v1/ping")
def ping_probe():
    return {"pong": True}

# --- OFFLINE SYNC ---
@app.post("/audit/v1/offline-sync")
async def offline_sync(request: Request):
    try:
        body = await request.json()
    except Exception:
        return make_error("VALIDATION_ERROR", "Malformed JSON", 400)

    device_id = body.get("device_id")
    actions = body.get("actions", [])
    if not device_id or not isinstance(actions, list):
        return make_error("VALIDATION_ERROR", "Invalid sync envelope", 400, {"fields": ["device_id", "actions"]})

    res = offline_sync_service.sync_actions(device_id, actions)
    if "error" in res:
        return make_error("VALIDATION_ERROR", "Sync error", 400, {"fields": res.get("fields", [])})
    return res
