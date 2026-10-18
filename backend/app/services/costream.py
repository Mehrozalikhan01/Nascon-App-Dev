"""Co-stream layout and deterministic host-promotion rules."""


def layout_capacity_error(layout: str, participant_count: int) -> bool:
    return (layout == "solo" and participant_count > 1) or (layout == "grid_2" and participant_count > 2)


def resolve_host_drop(host: str, participants: list[str], simulate_host_drop: bool) -> dict:
    if not simulate_host_drop:
        return {
            "active_host": host, "handoff_occurred": False,
            "active_participants": list(participants), "status": "active",
        }
    active = [participant for participant in participants if participant != host]
    if not active:
        return {"active_host": None, "handoff_occurred": False, "active_participants": [], "status": "terminated"}
    return {"active_host": active[0], "handoff_occurred": True, "active_participants": active, "status": "active"}
