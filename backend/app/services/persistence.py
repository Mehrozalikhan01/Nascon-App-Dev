"""PostgreSQL snapshot persistence for the in-memory competition domain services.

The audit API is intentionally kept compatible with the competition contract,
while this repository protects the current domain state across API restarts.
"""

from typing import Any, Dict

from app.services.sports import sports_service
from app.services.scoring import scoring_service
from app.services.standings import standings_service
from app.services.roster import roster_service
from app.services.store import store_service
from app.services.sync import offline_sync_service


SNAPSHOT_VERSION = 1


def export_domain_state() -> Dict[str, Any]:
    """Return JSON-compatible state without including service locks."""
    return {
        "schema_version": SNAPSHOT_VERSION,
        "sports": sports_service._sports,
        "match_events": scoring_service._match_events,
        "leagues": standings_service._leagues,
        "reset_seed": standings_service._reset_seed,
        "roster": {
            "leagues": roster_service._leagues,
            "squads": roster_service._squads,
            "players": roster_service._players,
            "player_history": roster_service._player_history,
            "requests_by_id": roster_service._requests_by_id,
            "audit_seq": roster_service._audit_seq,
        },
        "store": {
            "stock": store_service._stock,
            "reserved": store_service._reserved,
            "orders": store_service._orders,
            "variants": store_service._variants,
        },
        "processed_actions": [
            {"device_id": device_id, "action_id": action_id, "result": result}
            for (device_id, action_id), result in offline_sync_service._processed_actions.items()
        ],
    }


def import_domain_state(state: Any) -> None:
    """Restore a validated snapshot while preserving each service's locks."""
    if not isinstance(state, dict) or state.get("schema_version") != SNAPSHOT_VERSION:
        raise ValueError("Unsupported or malformed domain state snapshot")

    required_maps = ("sports", "match_events", "leagues", "roster", "store", "processed_actions")
    if any(key not in state for key in required_maps):
        raise ValueError("Domain state snapshot is missing required sections")
    if any(not isinstance(state[key], dict) for key in required_maps[:-1]):
        raise ValueError("Domain state snapshot contains an invalid section")
    if not isinstance(state["processed_actions"], list):
        raise ValueError("Domain state snapshot contains invalid processed actions")

    roster = state["roster"]
    store = state["store"]
    if any(not isinstance(roster.get(key), dict) for key in (
        "leagues", "squads", "players", "player_history", "requests_by_id"
    )):
        raise ValueError("Domain state snapshot contains invalid roster data")
    if any(not isinstance(store.get(key), dict) for key in (
        "stock", "reserved", "orders", "variants"
    )):
        raise ValueError("Domain state snapshot contains invalid store data")
    if isinstance(state.get("reset_seed"), bool) or not isinstance(state.get("reset_seed"), int):
        raise ValueError("Domain state snapshot contains invalid reset seed")

    processed_actions = {}
    for item in state["processed_actions"]:
        if not isinstance(item, dict):
            raise ValueError("Domain state snapshot contains invalid processed action")
        device_id = item.get("device_id")
        action_id = item.get("action_id")
        result = item.get("result")
        if not isinstance(device_id, str) or not isinstance(action_id, str) or not isinstance(result, dict):
            raise ValueError("Domain state snapshot contains invalid processed action")
        processed_actions[(device_id, action_id)] = result

    # Replace state only after validation, leaving service lock objects intact.
    sports_service._sports = state["sports"]
    scoring_service._match_events = state["match_events"]
    scoring_service._events_by_id = {
        event["event_id"]: event
        for events in scoring_service._match_events.values()
        for event in events
    }
    standings_service._leagues = state["leagues"]
    standings_service._reset_seed = state["reset_seed"]
    roster_service._leagues = roster["leagues"]
    roster_service._squads = roster["squads"]
    roster_service._players = roster["players"]
    roster_service._player_history = roster["player_history"]
    roster_service._requests_by_id = roster["requests_by_id"]
    roster_service._audit_seq = roster.get("audit_seq", 0)
    with store_service._checkout_lock, store_service._lock:
        store_service._stock = store["stock"]
        store_service._reserved = store["reserved"]
        store_service._orders = store["orders"]
        store_service._variants = store["variants"]
    with offline_sync_service._lock:
        offline_sync_service._processed_actions = processed_actions


def load_domain_state(database_url: str) -> bool:
    """Load the latest snapshot, returning False when the database is fresh."""
    import psycopg

    with psycopg.connect(database_url, connect_timeout=3) as connection:
        connection.execute(
            "CREATE TABLE IF NOT EXISTS nascon_domain_state ("
            "id SMALLINT PRIMARY KEY CHECK (id = 1), "
            "schema_version SMALLINT NOT NULL, state JSONB NOT NULL, "
            "updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW())"
        )
        row = connection.execute(
            "SELECT schema_version, state FROM nascon_domain_state WHERE id = 1"
        ).fetchone()
    if row is None:
        return False
    if row[0] != SNAPSHOT_VERSION:
        raise ValueError(f"Unsupported domain snapshot schema version: {row[0]}")
    import_domain_state(row[1])
    return True


def save_domain_state(database_url: str) -> None:
    """Atomically replace the latest full snapshot in PostgreSQL."""
    import psycopg
    from psycopg.types.json import Jsonb

    state = export_domain_state()
    with psycopg.connect(database_url, connect_timeout=3) as connection:
        connection.execute(
            "CREATE TABLE IF NOT EXISTS nascon_domain_state ("
            "id SMALLINT PRIMARY KEY CHECK (id = 1), "
            "schema_version SMALLINT NOT NULL, state JSONB NOT NULL, "
            "updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW())"
        )
        connection.execute(
            "INSERT INTO nascon_domain_state (id, schema_version, state) "
            "VALUES (1, %s, %s) ON CONFLICT (id) DO UPDATE SET "
            "schema_version = EXCLUDED.schema_version, state = EXCLUDED.state, "
            "updated_at = NOW()",
            (SNAPSHOT_VERSION, Jsonb(state)),
        )
