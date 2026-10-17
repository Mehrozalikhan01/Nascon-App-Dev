import time
import collections
import threading
import json
import logging
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
from app.services.auth import auth_service

from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(title="NaSCon 26 Multi-Sport Platform")
logger = logging.getLogger("nascon.api")


class BodySizeLimitMiddleware:
    """Enforce the payload limit even when a client streams without Content-Length."""

    def __init__(self, app, max_bytes: int):
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        chunks = []
        size = 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            if message["type"] != "http.request":
                continue
            body = message.get("body", b"")
            size += len(body)
            if size > self.max_bytes:
                response = JSONResponse(
                    status_code=413,
                    content={"error": {
                        "code": "PAYLOAD_TOO_LARGE",
                        "message": "Request body exceeds 1 MB limit.",
                        "details": {},
                    }},
                )
                await response(scope, receive, send)
                return
            chunks.append(message)
            if not message.get("more_body", False):
                break

        next_chunk = 0

        async def replay_request_body():
            nonlocal next_chunk
            if next_chunk < len(chunks):
                message = chunks[next_chunk]
                next_chunk += 1
                return message
            return await receive()

        await self.app(scope, replay_request_body, send)


app.add_middleware(
    CORSMiddleware,
    allow_origins=[origin.strip() for origin in settings.CORS_ORIGINS.split(",") if origin.strip()],
    allow_origin_regex=settings.CORS_ORIGIN_REGEX,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(BodySizeLimitMiddleware, max_bytes=1024 * 1024)

# Rolling 10s Rate Limiter for /api/v1/*
ip_request_timestamps = collections.defaultdict(list)
ip_request_lock = threading.Lock()

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


@app.post("/api/v1/auth/login")
async def login(request: Request):
    try:
        body = await request.json()
    except Exception:
        return make_error("VALIDATION_ERROR", "Malformed JSON", 400)
    if not isinstance(body, dict):
        return make_error("VALIDATION_ERROR", "Login body must be a JSON object", 400)
    if not isinstance(body.get("email"), str) or not isinstance(body.get("password"), str):
        return make_error("VALIDATION_ERROR", "Email and password are required", 400, {"fields": ["email", "password"]})
    result = auth_service.login(body.get("email"), body.get("password"))
    if result is None:
        return make_error("INVALID_CREDENTIALS", "Email or password is incorrect", 401)
    return result


@app.get("/api/v1/auth/me")
def current_user(request: Request):
    authorization = request.headers.get("authorization", "")
    if not authorization.startswith("Bearer "):
        return make_error("UNAUTHENTICATED", "Sign in to continue", 401)
    claims = auth_service.authenticate(authorization[7:].strip())
    if claims is None:
        return make_error("UNAUTHENTICATED", "Your session has expired. Sign in again.", 401)
    return {
        "user_id": claims.get("sub"),
        "role": claims.get("role"),
        "tenant_id": claims.get("tenant_id"),
        "league_id": claims.get("league_id"),
        "club_id": claims.get("club_id"),
        "player_id": claims.get("player_id"),
    }

@app.middleware("http")
async def validation_and_security_middleware(request: Request, call_next):
    # Check body size limit 1 MB
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            body_size = int(content_length)
        except ValueError:
            return make_error("VALIDATION_ERROR", "Invalid Content-Length header.", 400)
        if body_size < 0:
            return make_error("VALIDATION_ERROR", "Invalid Content-Length header.", 400)
        if body_size > 1024 * 1024:
            return make_error("PAYLOAD_TOO_LARGE", "Request body exceeds 1 MB limit.", 413)

    # Rate limiting for /api/v1/* (100 req / rolling 10s)
    if request.url.path.startswith("/api/v1/"):
        client_ip = request.client.host if request.client else "127.0.0.1"
        now = time.monotonic()
        with ip_request_lock:
            # Expire old entries for this client and bound stale client state.
            timestamps = [ts for ts in ip_request_timestamps[client_ip] if now - ts < 10.0]
            if len(timestamps) >= 100:
                ip_request_timestamps[client_ip] = timestamps
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
            timestamps.append(now)
            ip_request_timestamps[client_ip] = timestamps
            if len(ip_request_timestamps) > 10000:
                stale_ips = [ip for ip, values in ip_request_timestamps.items() if not values or now - values[-1] >= 10.0]
                for ip in stale_ips:
                    ip_request_timestamps.pop(ip, None)

    response = await call_next(request)
    return response

# --- HEALTH & ROOT ---
@app.api_route("/", methods=["GET", "POST", "OPTIONS"])
def root():
    return {"status": "ok", "service": "nascon-league-ops"}

@app.get("/health")
def health_probe():
    return {"status": "ok"}



# --- RESET ---
_baseline_seeded = False


def _record_database_baseline():
    if not settings.DATABASE_URL:
        return
    import psycopg

    with psycopg.connect(settings.DATABASE_URL, connect_timeout=2) as connection:
        connection.execute(
            "CREATE TABLE IF NOT EXISTS nascon_readiness ("
            "id SMALLINT PRIMARY KEY CHECK (id = 1), "
            "baseline_seeded BOOLEAN NOT NULL, seeded_at TIMESTAMPTZ NOT NULL DEFAULT NOW())"
        )
        connection.execute(
            "INSERT INTO nascon_readiness (id, baseline_seeded) VALUES (1, TRUE) "
            "ON CONFLICT (id) DO UPDATE SET baseline_seeded = TRUE, seeded_at = NOW()"
        )


def initialize_baseline(seed: int = 42, stock_map: Optional[Dict[str, int]] = None):
    global _baseline_seeded
    # Reset all services
    sports_service.init_baseline()
    scoring_service.reset()
    standings_service.reset(seed=seed)
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
            {
                "squad_id": "club-alpha-1", "club_id": "club-alpha-1", "tenant_id": "tenant-alpha",
                "name": "Alpha Athletics FC", "venue": "Jinnah Stadium, Islamabad",
                "staff": {"manager": "Tariq Ali", "coach": "Hamza Khan", "physio": "Sarah Ahmed"}
            },
            {"squad_id": "club-alpha-2", "club_id": "club-alpha-2", "tenant_id": "tenant-alpha", "name": "Alpha Reserves"}
        ],
        "players": [
            {"player_id": "player-alpha-1", "name": "Ali Raza", "position": "gk", "squad_id": "club-alpha-1", "role": "captain"},
            {"player_id": "player-alpha-3", "name": "Bilal Ahmed", "position": "df", "squad_id": "club-alpha-1", "role": "starter"},
            {"player_id": "player-alpha-2", "name": "Usman Tariq", "position": "mf", "squad_id": "club-alpha-2", "role": "starter"}
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

    # Register the demo league first; this creates the initial match records.
    # Later score events are appended to those same records.
    standings_service.register_league({
        "league_id": "league-alpha-1",
        "sport_id": "football",
        "teams": ["t1", "t2", "t3", "t4"],
        "fixtures": [
            {"match_id": "demo-m1", "home": "t1", "away": "t2"},
            {"match_id": "demo-m2", "home": "t3", "away": "t4"},
        ],
        "qualification": {"spots": 2},
        "lots_seed": seed,
    })

    # Seed real demo score events for the mobile screens so the app can show
    # API-backed data instead of fabricated local rows.
    demo_match_specs = [
        ("demo-m1", "t1", "t2", [("t1", "goal"), ("t1", "goal"), ("t2", "goal")], False),
        ("demo-m2", "t3", "t4", [("t3", "goal")], True),
    ]
    for match_id, home, away, goals, completed in demo_match_specs:
        scoring_service.append_event(f"demo-{match_id}-start", match_id, "match_start", {})
        for index, (team_id, event_type) in enumerate(goals, start=1):
            scoring_service.append_event(
                f"demo-{match_id}-score-{index}", match_id, "score",
                {"team_id": team_id, "event_type": event_type}
            )
        if completed:
            scoring_service.append_event(f"demo-{match_id}-end", match_id, "match_end", {})

    _baseline_seeded = True
    try:
        _record_database_baseline()
    except Exception:
        # The API can still start while Postgres is recovering. /health stays
        # unready until a later reset records the baseline marker successfully.
        pass

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


def enqueue_order_paid_event(response: Dict[str, Any]):
    """Publish a new paid-order event for the internal Redis worker."""
    if not settings.REDIS_URL:
        return
    from redis import Redis

    receipt = response.get("receipt", {})
    payload = {
        "event_id": f"order-paid:{response.get('order_id')}",
        "type": "order.paid",
        "order_id": response.get("order_id"),
        "receipt_signature": receipt.get("signature") if isinstance(receipt, dict) else None,
    }
    client = Redis.from_url(settings.REDIS_URL, socket_connect_timeout=1, socket_timeout=1)
    try:
        client.rpush("nascon:events", json.dumps(payload, separators=(",", ":")))
    finally:
        client.close()


initialize_baseline()


@app.post("/audit/v1/reset")
async def reset_baseline(request: Request):
    raw_body = await request.body()
    if raw_body:
        try:
            body = json.loads(raw_body)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return make_error("VALIDATION_ERROR", "Malformed JSON", 400)
        if not isinstance(body, dict):
            return make_error("VALIDATION_ERROR", "Reset body must be a JSON object", 400)
    else:
        body = {}

    seed = body.get("seed", 42)
    if isinstance(seed, bool) or not isinstance(seed, int):
        return make_error("VALIDATION_ERROR", "Invalid reset seed", 400, {"fields": ["seed"]})
    stock_map = body.get("stock", {"var-jersey-001-M": 1, "var-jersey-001-L": 5})
    if not isinstance(stock_map, dict) or any(
        not isinstance(key, str) or isinstance(value, bool) or not isinstance(value, int) or value < 0
        for key, value in stock_map.items()
    ):
        return make_error("VALIDATION_ERROR", "Invalid stock map", 400, {"fields": ["stock"]})

    return initialize_baseline(seed, stock_map)


@app.get("/audit/v1/mobile/overview")
def mobile_overview(request: Request):
    authorization = request.headers.get("authorization", "")
    claims = auth_service.authenticate(authorization[7:].strip()) if authorization.startswith("Bearer ") else None
    if claims is None:
        return make_error("UNAUTHENTICATED", "Sign in to view club operations data", 401)
    if claims.get("tenant_id") not in ("tenant-alpha", "global"):
        return make_error("FORBIDDEN", "This account cannot view the demo tenant", 403)
    fixture_ids = ("demo-m1", "demo-m2")
    fixtures = [
        scoring_service.project_match(match_id)
        for match_id in fixture_ids
        if scoring_service.get_match_events(match_id) is not None
    ]
    standings = standings_service.compute_standings("league-alpha-1")
    club = roster_service.get_squad_profile("club-alpha-1")
    return {
        "fixtures": fixtures,
        "standings": standings or {"league_id": "league-alpha-1", "table": []},
        "club": club or {},
        "team_names": {
            "t1": "Alpha United",
            "t2": "Capital Rangers",
            "t3": "River City FC",
            "t4": "North Stars",
        },
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }

# --- SPORTS ---
@app.post("/audit/v1/sports")
async def create_sport(request: Request):
    try:
        body = await request.json()
    except Exception:
        return make_error("VALIDATION_ERROR", "Malformed JSON", 400)

    if not isinstance(body, dict):
        return make_error("VALIDATION_ERROR", "Request body must be a JSON object", 400)

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

    if not isinstance(body, dict):
        return make_error("VALIDATION_ERROR", "Request body must be a JSON object", 400)

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

    if not isinstance(body, dict):
        return make_error("VALIDATION_ERROR", "Request body must be a JSON object", 400)

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

    if not isinstance(body, dict):
        return make_error("VALIDATION_ERROR", "Request body must be a JSON object", 400)

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
    if not isinstance(body, dict):
        return make_error("VALIDATION_ERROR", "Request body must be a JSON object", 400)
    return JSONResponse(status_code=201, content=roster_service.setup_roster(body))

@app.post("/audit/v1/roster/transfer")
async def transfer_roster(request: Request):
    try:
        body = await request.json()
    except Exception:
        return make_error("VALIDATION_ERROR", "Malformed JSON", 400)

    if not isinstance(body, dict):
        return make_error("VALIDATION_ERROR", "Request body must be a JSON object", 400)

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
    if st_code == 201 and res.get("response", {}).get("status") == "paid":
        try:
            enqueue_order_paid_event(res["response"])
        except Exception:
            # Preserve the checkout response if the queue is briefly unavailable.
            logger.exception("Could not enqueue paid order event for %s", res["response"].get("order_id"))
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

    if not isinstance(body, dict):
        return make_error("VALIDATION_ERROR", "Request body must be a JSON object", 400)

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

    if not isinstance(body, dict):
        return make_error("VALIDATION_ERROR", "Request body must be a JSON object", 400)

    device_id = body.get("device_id")
    actions = body.get("actions", [])
    if not device_id or not isinstance(actions, list):
        return make_error("VALIDATION_ERROR", "Invalid sync envelope", 400, {"fields": ["device_id", "actions"]})

    res = offline_sync_service.sync_actions(device_id, actions)
    if "error" in res:
        return make_error("VALIDATION_ERROR", "Sync error", 400, {"fields": res.get("fields", [])})
    return res
