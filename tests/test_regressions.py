import base64
from concurrent.futures import ThreadPoolExecutor

from app.core.config import settings
from app.core.security import sign, verify_role_jwt
from app.services.auth import auth_service
from app.services.scoring import ScoringService
from app.services.scoring import scoring_service
from app.services.sports import sports_service
from app.services.standings import StandingsService
from app.services.store import StoreService, generate_pdf_receipt
from app.services.sync import OfflineSyncService


def test_match_init_signature_uses_exact_request_payload():
    sports_service.init_baseline()
    scoring = ScoringService()
    payload = {"sport_id": "football", "home_team_id": "home", "away_team_id": "away"}

    result = scoring.append_event("init-event", "match-1", "match_init", payload)

    event = scoring.get_match_events("match-1")[0]
    assert result["signature"] == sign(settings.DAY1_SALT, "init-event", payload)
    assert event["payload"] == payload
    assert "league_id" not in event["payload"]


def test_second_match_init_returns_conflict_instead_of_raising():
    sports_service.init_baseline()
    scoring = ScoringService()
    payload = {"sport_id": "football", "home_team_id": "home", "away_team_id": "away"}
    scoring.append_event("init-1", "match-1", "match_init", payload)

    result = scoring.append_event("init-2", "match-1", "match_init", payload)

    assert result == {"error": "MATCH_EXISTS"}


def test_order_replay_requires_identical_payment_body():
    store = StoreService()
    store.init_baseline({"var-jersey-001-M": 1})
    request = {
        "order_id": "order-1",
        "customer_id": "customer-1",
        "items": [{"variant_id": "var-jersey-001-M", "quantity": 1}],
        "discount_code": None,
        "payment": {"token": "tok_success"},
    }

    assert store.checkout(request)["is_replay"] is False
    replay = store.checkout(request)
    changed_body = {**request, "payment": {"token": "tok_declined"}}

    assert replay["is_replay"] is True
    assert store.checkout(changed_body) == {"error": "DUPLICATE_ORDER_ID"}


def test_reset_seed_is_default_for_future_lots():
    standings = StandingsService()
    standings.reset(seed=73)
    assert standings._reset_seed == 73


def test_demo_login_returns_signed_role_token():
    result = auth_service.login("ADMIN@NASCON.TEST", "Nascon@26")

    assert result is not None
    claims = verify_role_jwt(result["access_token"])
    assert claims["role"] == "super_admin"
    assert claims["sub"] == "admin-01"
    assert auth_service.login("admin@nascon.test", "incorrect") is None


def test_offline_resend_reports_duplicate_and_does_not_append_twice():
    sports_service.init_baseline()
    scoring_service.reset()
    scoring_service.append_event(
        "offline-init", "offline-match", "match_init",
        {"sport_id": "football", "home_team_id": "home", "away_team_id": "away"},
    )
    scoring_service.append_event("offline-start", "offline-match", "match_start", {})
    sync = OfflineSyncService()
    request = {
        "device_id": "device-1",
        "actions": [{
            "action_id": "action-1",
            "type": "score_event",
            "queued_at": "2026-10-17T11:00:00Z",
            "base_version": 2,
            "payload": {
                "event_id": "offline-score",
                "match_id": "offline-match",
                "type": "score",
                "payload": {"team_id": "home", "event_type": "goal"},
            },
        }],
    }

    first = sync.sync_actions(request["device_id"], request["actions"])
    resent = sync.sync_actions(request["device_id"], request["actions"])

    assert first["results"][0]["status"] == "applied"
    assert resent["results"][0]["status"] == "duplicate"
    assert resent["results"][0]["resolution"] == "skipped_duplicate"
    assert resent["summary"] == {"applied": 0, "rejected": 0, "duplicate": 1}
    assert len(scoring_service.get_match_events("offline-match")) == 3


def test_offline_sync_requires_second_precision_utc_timestamps():
    sync = OfflineSyncService()
    action = {
        "action_id": "action-utc",
        "type": "score_event",
        "queued_at": "2026-10-17T11:00:00.000Z",
        "base_version": 0,
        "payload": {},
    }

    result = sync.sync_actions("device-utc", [action])

    assert result == {"error": "VALIDATION_ERROR", "fields": ["actions.queued_at"]}


def test_concurrent_offline_replay_applies_action_only_once():
    sports_service.init_baseline()
    scoring_service.reset()
    scoring_service.append_event(
        "race-init", "race-match", "match_init",
        {"sport_id": "football", "home_team_id": "home", "away_team_id": "away"},
    )
    scoring_service.append_event("race-start", "race-match", "match_start", {})
    sync = OfflineSyncService()
    actions = [{
        "action_id": "race-action",
        "type": "score_event",
        "queued_at": "2026-10-17T11:00:00Z",
        "base_version": 2,
        "payload": {
            "event_id": "race-score",
            "match_id": "race-match",
            "type": "score",
            "payload": {"team_id": "home", "event_type": "goal"},
        },
    }]

    with ThreadPoolExecutor(max_workers=8) as pool:
        responses = list(pool.map(lambda _: sync.sync_actions("race-device", actions), range(8)))

    statuses = [response["results"][0]["status"] for response in responses]
    assert statuses.count("applied") == 1
    assert statuses.count("duplicate") == 7
    assert len(scoring_service.get_match_events("race-match")) == 3


def test_receipt_is_a_valid_pdf_with_required_order_and_total_text():
    pdf_bytes = base64.b64decode(generate_pdf_receipt("ord-001", "PKR 4500.00"))

    assert pdf_bytes.startswith(b"%PDF-")
    xref_offset = int(pdf_bytes.rsplit(b"startxref\n", 1)[1].splitlines()[0])
    assert pdf_bytes[xref_offset:xref_offset + 4] == b"xref"
    assert b"(ord-001) Tj" in pdf_bytes
    assert b"PKR 4500.00" in pdf_bytes
