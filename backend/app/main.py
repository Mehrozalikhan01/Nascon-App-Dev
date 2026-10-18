import time
import collections
import threading
import asyncio
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
from app.services.persistence import load_domain_state, save_domain_state
from app.services.object_store import ensure_receipt_bucket
from app.services.day2 import guest_write_decision, router as day2_router, reset_day2, publish_match_event, update_match_state

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

        # GET/HEAD requests cannot carry domain mutation bodies. Passing their
        # receive channel through untouched is required for long-lived streams
        # such as SSE; replaying a synthetic disconnect makes BaseHTTPMiddleware
        # treat the response as disconnected before response headers are sent.
        if scope.get("method") in {"GET", "HEAD", "OPTIONS"}:
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
            # The request was fully buffered above. Do not wait on the real
            # client after replay: test clients and some mobile transports do
            # not send a second event until the response has completed.
            return {"type": "http.disconnect"}

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
domain_mutation_lock = asyncio.Lock()

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

    # Guest credentials are read-only; enforce this at the API boundary rather
    # than trusting controls in the guest player.
    authorization = request.headers.get("authorization", "")
    guest_token = authorization[7:].strip() if authorization.startswith("Bearer ") else ""
    rejection_code, rejection_error = guest_write_decision(guest_token, request.method)
    if rejection_code:
        return make_error(
            rejection_error or "GUEST_WRITE_FORBIDDEN",
            "Anonymous guest tokens cannot perform write mutations.",
            rejection_code,
            {"guest_token": guest_token},
        )

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


@app.middleware("http")
async def persist_domain_mutations(request: Request, call_next):
    """Serialize state changes and persist successful mutations to PostgreSQL."""
    if not settings.DATABASE_URL:
        return await call_next(request)

    path = request.url.path
    changes_domain_state = request.method in {"POST", "PUT", "PATCH", "DELETE"} and (
        path in {
            "/audit/v1/reset",
            "/audit/v1/sports",
            "/audit/v1/leagues",
            "/audit/v1/events",
            "/audit/v1/roster/setup",
            "/audit/v1/roster/transfer",
            "/audit/v1/checkout",
            "/audit/v1/offline-sync",
        }
        or path.startswith("/audit/v1/players/")
        or path.startswith("/audit/v1/privacy/user/")
    )
    if not changes_domain_state:
        return await call_next(request)

    async with domain_mutation_lock:
        response = await call_next(request)
        if response.status_code < 400:
            try:
                await asyncio.to_thread(save_domain_state, settings.DATABASE_URL)
            except Exception:
                logger.exception("Could not persist a successful domain mutation")
                return make_error("PERSISTENCE_UNAVAILABLE", "The update could not be safely saved. Please retry.", 503)
        return response

# --- HEALTH & ROOT ---
@app.api_route("/", methods=["GET", "POST", "OPTIONS"])
def root():
    return {"status": "ok", "service": "nascon-league-ops"}

@app.get("/health")
def health_probe():
    if not _baseline_seeded or not _object_store_ready:
        return JSONResponse(status_code=503, content={"status": "not_ready"})
    if settings.DATABASE_URL:
        try:
            import psycopg

            with psycopg.connect(settings.DATABASE_URL, connect_timeout=2) as connection:
                marker = connection.execute(
                    "SELECT baseline_seeded FROM nascon_readiness WHERE id = 1"
                ).fetchone()
                if marker is None or marker[0] is not True:
                    return JSONResponse(status_code=503, content={"status": "not_ready"})
        except Exception:
            return JSONResponse(status_code=503, content={"status": "not_ready"})
    if settings.REDIS_URL:
        try:
            import redis
            client = redis.Redis.from_url(settings.REDIS_URL, socket_connect_timeout=1, socket_timeout=1)
            ready = client.ping()
            client.close()
            if not ready:
                return JSONResponse(status_code=503, content={"status": "not_ready"})
        except Exception:
            return JSONResponse(status_code=503, content={"status": "not_ready"})
    if settings.MEDIAMTX_API_ENDPOINT:
        try:
            import urllib.request
            with urllib.request.urlopen(settings.MEDIAMTX_API_ENDPOINT.rstrip("/") + "/v3/config/global/get", timeout=1):
                pass
        except Exception:
            return JSONResponse(status_code=503, content={"status": "not_ready"})
    return {"status": "ok"}



# --- RESET ---
_baseline_seeded = False
_object_store_ready = not bool(settings.MINIO_ENDPOINT)


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
    reset_day2()

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
            {"player_id": "player-beta-1", "name": "Player B1", "squad_id": "club-beta-1", "role": "starter"},
            {"player_id": "player-beta-2", "name": "Player B2", "squad_id": "club-beta-1", "role": "starter"}
        ]
    })

    # Register the demo league first; this creates the initial match records.
    # Later score events are appended to those same records.
    standings_service.register_league({
        "league_id": "league-alpha-1",
        "sport_id": "football",
        "teams": ["t1", "t2", "t3", "t4"],
        "fixtures": [
            {"match_id": "seed-alpha-1-m1", "home": "t1", "away": "t2"},
            {"match_id": "seed-alpha-1-m2", "home": "t3", "away": "t4"},
        ],
        "qualification": {"spots": 2},
        "lots_seed": seed,
    })

    standings_service.register_league({
        "league_id": "league-alpha-2",
        "sport_id": "football",
        "teams": ["t5", "t6", "t7", "t8"],
        "fixtures": [
            {"match_id": "seed-alpha-2-m1", "home": "t5", "away": "t6"},
            {"match_id": "seed-alpha-2-m2", "home": "t7", "away": "t8"},
        ],
        "qualification": {"spots": 2},
        "lots_seed": seed,
    })

    standings_service.register_league({
        "league_id": "league-beta-1",
        "sport_id": "football",
        "teams": ["b1", "b2", "b3", "b4"],
        "fixtures": [
            {"match_id": "seed-beta-1-m1", "home": "b1", "away": "b2"},
            {"match_id": "seed-beta-1-m2", "home": "b3", "away": "b4"},
        ],
        "qualification": {"spots": 2},
        "lots_seed": seed,
    })

    # Seed real demo score events for the mobile screens so the app can show
    # API-backed data instead of fabricated local rows.
    demo_match_specs = [
        ("seed-alpha-1-m1", "t1", "t2", [("t1", "goal"), ("t1", "goal"), ("t2", "goal")], False),
        ("seed-alpha-1-m2", "t3", "t4", [("t3", "goal")], True),
        ("seed-alpha-2-m1", "t5", "t6", [("t5", "goal")], False),
        ("seed-alpha-2-m2", "t7", "t8", [("t7", "goal")], True),
        ("seed-beta-1-m1", "b1", "b2", [("b1", "goal")], False),
        ("seed-beta-1-m2", "b3", "b4", [("b3", "goal")], True),
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

    # Required Phase II match seed: a genuine event-sourced 1-0 live fixture.
    scoring_service.append_event(
        "day2-match-m1-init", "match-m1", "match_init",
        {"sport_id": "football", "home_team_id": "club-alpha-1", "away_team_id": "club-alpha-2", "league_id": "league-alpha-1"},
    )
    scoring_service.append_event("day2-match-m1-start", "match-m1", "match_start", {})
    scoring_service.append_event("day2-match-m1-score", "match-m1", "score", {"team_id": "club-alpha-1", "event_type": "goal"})

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


def _ensure_day2_demo_records() -> bool:
    """Add the Day 2 fixture/player to restored Day 1 snapshots without resetting them."""
    changed = False
    if not roster_service.get_player("player-beta-2"):
        league = roster_service._leagues.setdefault("league-beta-1", {
            "league_id": "league-beta-1", "tenant_id": "tenant-beta", "sport_id": "football",
            "transfer_window": {}, "max_squad_size": 25, "required_positions": {},
            "matches_in_progress": [],
        })
        squad = roster_service._squads.setdefault("club-beta-1", {
            "squad_id": "club-beta-1", "club_id": "club-beta-1", "tenant_id": "tenant-beta",
            "name": "Beta 1", "venue": None, "staff": {}, "league_id": league["league_id"],
            "player_ids": [],
        })
        roster_service._players["player-beta-2"] = {
            "player_id": "player-beta-2", "name": "Player B2", "position": "generic",
            "squad_id": "club-beta-1", "league_id": league["league_id"],
            "tenant_id": "tenant-beta", "role": "starter", "is_on_loan": False,
            "parent_squad_id": None, "loan_end_date": None,
        }
        if "player-beta-2" not in squad["player_ids"]:
            squad["player_ids"].append("player-beta-2")
        roster_service._player_history.setdefault("player-beta-2", [])
        changed = True

    if not scoring_service.project_match("match-m1"):
        scoring_service.append_event(
            "day2-match-m1-init", "match-m1", "match_init",
            {"sport_id": "football", "home_team_id": "club-alpha-1", "away_team_id": "club-alpha-2", "league_id": "league-alpha-1"},
        )
        scoring_service.append_event("day2-match-m1-start", "match-m1", "match_start", {})
        scoring_service.append_event(
            "day2-match-m1-score", "match-m1", "score",
            {"team_id": "club-alpha-1", "event_type": "goal"},
        )
        changed = True
    return changed


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
        "receipt_pdf_base64": receipt.get("pdf_base64") if isinstance(receipt, dict) else None,
    }
    client = Redis.from_url(settings.REDIS_URL, socket_connect_timeout=1, socket_timeout=1)
    try:
        # LPUSH paired with BRPOPLPUSH preserves FIFO order in the worker.
        client.lpush("nascon:events", json.dumps(payload, separators=(",", ":")))
    finally:
        client.close()


initialize_baseline()


@app.on_event("startup")
async def restore_domain_state_on_startup():
    """Restore persisted domain data after the in-memory demo baseline exists."""
    global _baseline_seeded, _object_store_ready
    try:
        restored = False
        if settings.DATABASE_URL:
            restored = await asyncio.to_thread(load_domain_state, settings.DATABASE_URL)
        added_day2_baseline = _ensure_day2_demo_records()
        if settings.DATABASE_URL:
            if not restored or added_day2_baseline:
                await asyncio.to_thread(save_domain_state, settings.DATABASE_URL)
            await asyncio.to_thread(_record_database_baseline)
        if settings.MINIO_ENDPOINT:
            await asyncio.to_thread(
                ensure_receipt_bucket,
                settings.MINIO_ENDPOINT,
                settings.MINIO_ACCESS_KEY,
                settings.MINIO_SECRET_KEY,
                settings.MINIO_BUCKET,
                settings.MINIO_SECURE,
            )
            _object_store_ready = True
        _baseline_seeded = True
        if settings.DATABASE_URL:
            logger.info("Domain state %s from PostgreSQL", "restored" if restored else "initialized")
    except Exception:
        _baseline_seeded = False
        _object_store_ready = False
        logger.exception("Could not initialize PostgreSQL or MinIO dependencies")


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
def mobile_overview(
    request: Request,
    tenant_id: Optional[str] = None,
    league_id: str = "league-alpha-1",
    club_id: str = "club-alpha-1",
):
    authorization = request.headers.get("authorization", "")
    claims = auth_service.authenticate(authorization[7:].strip()) if authorization.startswith("Bearer ") else None
    if claims is None:
        return make_error("UNAUTHENTICATED", "Sign in to view club operations data", 401)
    role = claims.get("role")
    claim_tenant_id = claims.get("tenant_id")
    if role != "super_admin" and tenant_id and tenant_id != claim_tenant_id:
        return make_error("FORBIDDEN", "This account cannot view that tenant", 403)
    if role == "super_admin":
        tenant_id = tenant_id if tenant_id in ("tenant-alpha", "tenant-beta") else "tenant-alpha"
    else:
        tenant_id = claim_tenant_id
    if tenant_id not in ("tenant-alpha", "tenant-beta"):
        return make_error("FORBIDDEN", "This account cannot view the demo tenant", 403)
    tenant_leagues = {
        "tenant-alpha": ["league-alpha-1", "league-alpha-2"],
        "tenant-beta": ["league-beta-1"],
    }[tenant_id]
    tenant_clubs = {
        "tenant-alpha": ["club-alpha-1", "club-alpha-2"],
        "tenant-beta": ["club-beta-1"],
    }[tenant_id]
    if role == "super_admin" and league_id not in tenant_leagues:
        league_id = tenant_leagues[0]
    if role != "super_admin" and league_id not in tenant_leagues:
        if role == "league_organizer":
            return make_error("FORBIDDEN", "This account cannot view that league", 403)
        league_id = tenant_leagues[0]
    if role == "super_admin" and club_id not in tenant_clubs:
        club_id = tenant_clubs[0]
    if role not in ("super_admin", "club_manager", "player") and club_id not in tenant_clubs:
        club_id = tenant_clubs[0]
    if role == "league_organizer" and league_id != claims.get("league_id"):
        return make_error("FORBIDDEN", "This account cannot view that league", 403)
    if role in ("club_manager", "player") and club_id != claims.get("club_id"):
        return make_error("FORBIDDEN", "This account cannot view that club", 403)
    league = standings_service.get_league(league_id)
    if league is None:
        return make_error("LEAGUE_NOT_FOUND", "League not found", 404)
    if league_id not in tenant_leagues and role != "super_admin":
        return make_error("FORBIDDEN", "This account cannot view that league", 403)
    fixture_ids = tuple(fixture["match_id"] for fixture in league["fixtures"])
    fixtures = [
        scoring_service.project_match(match_id)
        for match_id in fixture_ids
        if scoring_service.get_match_events(match_id) is not None
    ]
    standings = standings_service.compute_standings(league_id)
    club = roster_service.get_squad_profile(club_id)
    if club is not None and club.get("tenant_id") != tenant_id:
        return make_error("FORBIDDEN", "This account cannot view that club", 403)
    visible_leagues = list(tenant_leagues)
    if role == "league_organizer":
        visible_leagues = [claims["league_id"]]
    visible_clubs = list(tenant_clubs)
    if role in ("club_manager", "player"):
        visible_clubs = [claims["club_id"]]
    return {
        "fixtures": fixtures,
        "standings": standings or {"league_id": league_id, "table": []},
        "league_ids": visible_leagues,
        "league_id": league_id,
        "tenant_id": tenant_id,
        "tenant_ids": ["tenant-alpha", "tenant-beta"] if role == "super_admin" else [tenant_id],
        "club_id": club_id,
        "club_ids": visible_clubs,
        "club_names": {
            item: (roster_service.get_squad_profile(item) or {}).get("name", item)
            for item in visible_clubs
        },
        "club": club or {},
        "team_names": {
            "t1": "Alpha United",
            "t2": "Capital Rangers",
            "t3": "River City FC",
            "t4": "North Stars",
            "t5": "Beta United",
            "t6": "Central Athletic",
            "t7": "Eastside FC",
            "t8": "Westport City",
            "b1": "Beta United",
            "b2": "Eastshore FC",
            "b3": "Desert Rovers",
            "b4": "Highland City",
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
    if not res.get("is_replay"):
        projection = scoring_service.project_match(mid)
        if projection:
            update_match_state(mid, projection.get("status", "upcoming"))
            await publish_match_event(mid, etype, projection)
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
    return JSONResponse(status_code=st_code, content=res["response"])

@app.get("/audit/v1/roster/history/{player_id}")
def get_roster_history(player_id: str, as_of: Optional[str] = None):
    res = roster_service.get_history(player_id, as_of)
    if not res:
        return make_error("PLAYER_NOT_FOUND", f"Player {player_id} not found", 404)
    return res


@app.patch("/audit/v1/players/{player_id}")
async def update_player_profile(player_id: str, request: Request):
    authorization = request.headers.get("authorization", "")
    claims = auth_service.authenticate(authorization[7:].strip()) if authorization.startswith("Bearer ") else None
    if claims is None:
        return make_error("UNAUTHENTICATED", "Sign in to update a player profile", 401)

    player = roster_service.get_player(player_id)
    if player is None:
        return make_error("PLAYER_NOT_FOUND", f"Player {player_id} not found", 404)
    role = claims.get("role")
    if role == "player":
        allowed = claims.get("player_id") == player_id
    elif role == "club_manager":
        allowed = claims.get("club_id") == player.get("squad_id")
    else:
        allowed = role == "super_admin"
    if not allowed:
        return make_error("FORBIDDEN", "This account cannot update that player", 403)
    if role != "super_admin" and claims.get("tenant_id") != player.get("tenant_id"):
        return make_error("FORBIDDEN", "This account cannot update players in that tenant", 403)

    try:
        body = await request.json()
    except Exception:
        return make_error("VALIDATION_ERROR", "Malformed JSON", 400)
    if not isinstance(body, dict):
        return make_error("VALIDATION_ERROR", "Request body must be a JSON object", 400)
    result = roster_service.update_player_profile(player_id, body, claims.get("sub", "unknown"))
    if "error" in result:
        if result["error"] == "PLAYER_NOT_FOUND":
            return make_error("PLAYER_NOT_FOUND", f"Player {player_id} not found", 404)
        return make_error("VALIDATION_ERROR", "Invalid profile fields", 400, {"fields": result.get("fields", [])})
    return result

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
    if st_code == 201 and res.get("response", {}).get("status") == "paid":
        try:
            enqueue_order_paid_event(res["response"])
        except Exception:
            # Checkout remains available if Redis is briefly unreachable.
            logger.exception("Could not enqueue paid order event for %s", res["response"].get("order_id"))
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


app.include_router(day2_router)
