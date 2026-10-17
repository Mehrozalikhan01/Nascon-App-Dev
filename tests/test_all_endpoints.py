import pytest
import threading
from fastapi.testclient import TestClient
from app.main import app
from app.core.security import sign, lots_key
from app.core.config import settings

client = TestClient(app)

# 1. HEALTH PROBE
def test_health():
    res = client.get("/health")
    assert res.status_code == 200
    assert res.json() == {"status": "ok"}

# 2. RESET
def test_reset():
    res = client.post("/audit/v1/reset", json={"seed": 42, "stock": {"var-jersey-001-M": 1, "var-jersey-001-L": 5}})
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "reset_complete"
    assert "football" in data["sports"]
    assert "tenant-alpha" in data["tenants"]

# 3. MYSTERY SPORT
def test_mystery_sport():
    mystery_payload = {
        "sport_id": "kabaddi",
        "name": "Kabaddi",
        "scoring_model": "event",
        "periods": {"count": 2, "duration_minutes": 20},
        "scoring_events": {
            "raid_point": {"points": 1},
            "tackle_point": {"points": 1},
            "all_out": {"points": 2}
        },
        "draw_allowed": False,
        "overtime": {"policy": "golden_score", "duration_minutes": 5},
        "tie_break_order": ["h2h_points", "h2h_diff", "overall_diff", "scored", "fair_play", "draw_lots"],
        "roster": {"min_squad_size": 7, "max_squad_size": 12, "required_positions": {"raider": 2, "defender": 3}}
    }
    res = client.post("/audit/v1/sports", json=mystery_payload)
    assert res.status_code == 201
    assert res.json()["sport_id"] == "kabaddi"

    # Duplicate sport returns 409 SPORT_EXISTS
    dup_res = client.post("/audit/v1/sports", json=mystery_payload)
    assert dup_res.status_code == 409
    assert dup_res.json()["error"]["code"] == "SPORT_EXISTS"

# 4. FIXTURES (All 4 formats)
def test_fixtures_all_formats():
    # A. Round Robin
    rr_res = client.post("/audit/v1/fixtures", json={
        "format": "round_robin",
        "teams": ["t1", "t2", "t3", "t4", "t5"],
        "params": {
            "legs": 1,
            "withdrawals": [{"team_id": "t3", "after_round": 2}],
            "withdrawal_policy": "forfeit_remaining"
        }
    })
    assert rr_res.status_code == 200
    assert rr_res.json()["summary"]["total"] == 10

    # B. Swiss
    sw_res = client.post("/audit/v1/fixtures", json={
        "format": "swiss",
        "teams": ["t1", "t2", "t3", "t4", "t5"],
        "params": {"total_rounds": 3, "round": 1}
    })
    assert sw_res.status_code == 200
    assert sw_res.json()["bye"] == "t5"

    # C. Single Elim
    se_res = client.post("/audit/v1/fixtures", json={
        "format": "single_elim",
        "teams": ["t1", "t2", "t3", "t4", "t5", "t6"],
        "params": {"third_place_match": True, "results": [{"match_id": "R1M2", "winner": "t4"}]}
    })
    assert se_res.status_code == 200
    assert se_res.json()["bracket_size"] == 8

    # D. Double Elim
    de_res = client.post("/audit/v1/fixtures", json={
        "format": "double_elim",
        "teams": ["t1", "t2", "t3", "t4", "t5", "t6", "t7", "t8"],
        "params": {"bracket_reset": True, "results": [{"match_id": "WR1M1", "winner": "t1"}]}
    })
    assert de_res.status_code == 200
    assert de_res.json()["bracket_reset"] is True

# 5. EVENTS & SCORING (Event-sourcing with compensating rollback)
def test_events_and_scoring():
    client.post("/audit/v1/reset")
    # Init match
    init_res = client.post("/audit/v1/events", json={
        "event_id": "evt-001",
        "match_id": "m-test",
        "type": "match_init",
        "payload": {"sport_id": "football", "home_team_id": "t1", "away_team_id": "t2"}
    })
    assert init_res.status_code == 201

    # Start match
    client.post("/audit/v1/events", json={
        "event_id": "evt-002",
        "match_id": "m-test",
        "type": "match_start",
        "payload": {}
    })

    # Score goal
    score_res = client.post("/audit/v1/events", json={
        "event_id": "evt-003",
        "match_id": "m-test",
        "type": "score",
        "payload": {"team_id": "t1", "event_type": "goal"}
    })
    assert score_res.status_code == 201
    assert "signature" in score_res.json()

    # Replay identical event_id (idempotent 200)
    replay_res = client.post("/audit/v1/events", json={
        "event_id": "evt-003",
        "match_id": "m-test",
        "type": "score",
        "payload": {"team_id": "t1", "event_type": "goal"}
    })
    assert replay_res.status_code == 200

    # Compensating event (rollback disallowed goal)
    comp_res = client.post("/audit/v1/events", json={
        "event_id": "evt-004",
        "match_id": "m-test",
        "type": "compensation",
        "payload": {"compensates": "evt-003", "reason": "Handball"}
    })
    assert comp_res.status_code == 201

    # Check projected score (goal should be rolled back to 0)
    score_proj = client.get("/audit/v1/score/m-test")
    assert score_proj.status_code == 200
    assert score_proj.json()["home"]["score"] == 0

# 6. STANDINGS & 3-WAY MINI LEAGUE
def test_standings_and_qualification():
    client.post("/audit/v1/reset")
    lg_res = client.post("/audit/v1/leagues", json={
        "league_id": "lg-test",
        "sport_id": "football",
        "teams": ["t1", "t2", "t3", "t4"],
        "fixtures": [
            {"match_id": "m1", "home": "t1", "away": "t2"},
            {"match_id": "m2", "home": "t3", "away": "t4"}
        ],
        "points": {"win": 3, "draw": 1, "loss": 0},
        "qualification": {"spots": 2},
        "lots_seed": 42
    })
    assert lg_res.status_code == 201

    # Complete match 1
    client.post("/audit/v1/events", json={"event_id": "m1-start", "match_id": "m1", "type": "match_start", "payload": {}})
    client.post("/audit/v1/events", json={"event_id": "m1-s1", "match_id": "m1", "type": "score", "payload": {"team_id": "t1", "event_type": "goal"}})
    client.post("/audit/v1/events", json={"event_id": "m1-end", "match_id": "m1", "type": "match_end", "payload": {}})

    standings = client.get("/audit/v1/standings/lg-test")
    assert standings.status_code == 200
    table = standings.json()["table"]
    assert table[0]["team_id"] == "t1"
    assert table[0]["points"] == 3

# 7. CONCURRENCY-SAFE STORE (20 simultaneous checkout threads on 1 item)
def test_concurrency_safe_checkout():
    client.post("/audit/v1/reset", json={"stock": {"var-jersey-001-M": 1}})
    results = []
    
    def buy(ord_id):
        r = client.post("/audit/v1/checkout", json={
            "order_id": f"ord-{ord_id}",
            "customer_id": f"cust-{ord_id}",
            "items": [{"variant_id": "var-jersey-001-M", "quantity": 1}],
            "discount_code": None,
            "payment": {"token": "tok_success"}
        })
        results.append(r.status_code)

    threads = [threading.Thread(target=buy, args=(i,)) for i in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert results.count(201) == 1, f"Expected exactly 1 success, got: {results.count(201)}"
    assert results.count(409) == 19, f"Expected 19 out of stock, got: {results.count(409)}"

    # Stock must be exactly 0
    inv = client.get("/audit/v1/inventory/var-jersey-001-M")
    assert inv.status_code == 200
    assert inv.json()["stock"] == 0
    assert inv.json()["reserved"] == 0

# 8. RBAC MATRIX & PROBES
def test_rbac_probes():
    matrix_res = client.post("/audit/v1/rbac-matrix")
    assert matrix_res.status_code == 200
    tokens = matrix_res.json()["tokens"]
    ctx = matrix_res.json()["context"]

    # Super admin on P1 -> 200
    p1_admin = client.post("/audit/v1/rbac/tenants", headers={"Authorization": f"Bearer {tokens['super_admin']}"})
    assert p1_admin.status_code == 200

    # League organizer on P1 -> 403
    p1_org = client.post("/audit/v1/rbac/tenants", headers={"Authorization": f"Bearer {tokens['league_organizer']}"})
    assert p1_org.status_code == 403

    # League organizer on own league P2 -> 200
    p2_own = client.post(f"/audit/v1/rbac/leagues/{ctx['own_league_id']}/fixtures", headers={"Authorization": f"Bearer {tokens['league_organizer']}"})
    assert p2_own.status_code == 200

    # League organizer on foreign league P2 -> 403
    p2_for = client.post(f"/audit/v1/rbac/leagues/{ctx['foreign_league_id']}/fixtures", headers={"Authorization": f"Bearer {tokens['league_organizer']}"})
    assert p2_for.status_code == 403

# 9. OFFLINE SYNC
def test_offline_sync():
    client.post("/audit/v1/reset")
    # Setup league and match
    client.post("/audit/v1/leagues", json={
        "league_id": "lg-sync",
        "sport_id": "football",
        "teams": ["t1", "t2"],
        "fixtures": [{"match_id": "m-sync", "home": "t1", "away": "t2"}]
    })
    client.post("/audit/v1/events", json={"event_id": "s-start", "match_id": "m-sync", "type": "match_start", "payload": {}})

    sync_res = client.post("/audit/v1/offline-sync", json={
        "device_id": "dev-1",
        "actions": [
            {
                "action_id": "act-1",
                "type": "score_event",
                "queued_at": "2026-10-17T11:00:00Z",
                "base_version": 2,
                "payload": {
                    "event_id": "sync-evt-1",
                    "match_id": "m-sync",
                    "type": "score",
                    "payload": {"team_id": "t1", "event_type": "goal"}
                }
            }
        ]
    })
    assert sync_res.status_code == 200
    assert sync_res.json()["summary"]["applied"] == 1
