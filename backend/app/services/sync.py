from typing import Dict, Any, List, Optional
from datetime import datetime, timezone
import re
import threading
from app.services.scoring import scoring_service
from app.services.roster import roster_service

class OfflineSyncService:
    def __init__(self):
        # (device_id, action_id) -> stored result
        self._processed_actions: Dict[tuple[str, str], Dict[str, Any]] = {}
        self._lock = threading.RLock()

    def reset(self):
        with self._lock:
            self._processed_actions = {}

    def sync_actions(self, device_id: str, actions: List[Dict[str, Any]]) -> Dict[str, Any]:
        # A replay must not race the first attempt and apply the same action twice.
        with self._lock:
            return self._sync_actions(device_id, actions)

    def _sync_actions(self, device_id: str, actions: List[Dict[str, Any]]) -> Dict[str, Any]:
        if (not isinstance(device_id, str) or re.fullmatch(r"[A-Za-z0-9_.:-]{1,64}", device_id) is None or
                not isinstance(actions, list) or not actions or len(actions) > 100):
            return {"error": "VALIDATION_ERROR", "fields": ["actions"]}

        # Tag original order
        indexed_actions = []
        seen_action_ids = set()
        for idx, act in enumerate(actions):
            if not isinstance(act, dict):
                return {"error": "VALIDATION_ERROR", "fields": ["actions"]}
            action_id = act.get("action_id")
            action_type = act.get("type")
            queued_at = act.get("queued_at")
            base_version = act.get("base_version")
            payload = act.get("payload", {})
            if (not isinstance(action_id, str) or re.fullmatch(r"[A-Za-z0-9_.:-]{1,64}", action_id) is None or
                    not isinstance(action_type, str) or not action_type or
                    not isinstance(queued_at, str) or not isinstance(payload, dict) or
                    isinstance(base_version, bool) or not isinstance(base_version, int) or base_version < 0):
                return {"error": "VALIDATION_ERROR", "fields": ["actions"]}
            if action_id in seen_action_ids:
                return {"error": "VALIDATION_ERROR", "fields": ["actions.action_id"]}
            seen_action_ids.add(action_id)
            if re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", queued_at) is None:
                return {"error": "VALIDATION_ERROR", "fields": ["actions.queued_at"]}
            try:
                # The contract requires UTC timestamps in second precision.
                # Reject local/offset timestamps instead of sorting by host TZ.
                sort_time = datetime.strptime(
                    queued_at, "%Y-%m-%dT%H:%M:%SZ"
                ).replace(tzinfo=timezone.utc).timestamp()
            except (ValueError, OverflowError):
                return {"error": "VALIDATION_ERROR", "fields": ["actions.queued_at"]}
            indexed_actions.append((idx, act, sort_time))

        # Sort by queued_at ascending, tie breaker: array order
        indexed_actions.sort(key=lambda item: (item[2], item[0]))

        applied_count = 0
        rejected_count = 0
        duplicate_count = 0

        action_results: Dict[int, Dict[str, Any]] = {}

        for processed_order, (orig_idx, act, _) in enumerate(indexed_actions, start=1):
            act_id = act["action_id"]
            act_type = act["type"]
            base_ver = act["base_version"]
            payload = act.get("payload", {})

            dedup_key = (device_id, act_id)
            if dedup_key in self._processed_actions:
                prev = self._processed_actions[dedup_key]
                res_obj = dict(prev)
                res_obj["status"] = "duplicate"
                res_obj["resolution"] = "skipped_duplicate"
                res_obj["error_code"] = None
                res_obj["processed_order"] = processed_order
                if act_type == "score_event":
                    events = scoring_service.get_match_events(payload.get("match_id"))
                    if events is not None:
                        res_obj["server_version"] = len(events)
                elif act_type == "roster_transfer":
                    history = roster_service.get_history(payload.get("player_id"))
                    if history is not None:
                        res_obj["server_version"] = len(history["history"])
                action_results[orig_idx] = res_obj
                duplicate_count += 1
                continue

            if act_type == "score_event":
                mid = payload.get("match_id")
                events = scoring_service.get_match_events(mid)
                if events is None:
                    res_obj = {
                        "action_id": act_id,
                        "status": "rejected",
                        "resolution": "server_wins",
                        "error_code": "MATCH_NOT_FOUND",
                        "processed_order": processed_order,
                        "server_version": 0,
                        "signature": None
                    }
                    rejected_count += 1
                    self._processed_actions[dedup_key] = res_obj
                    action_results[orig_idx] = res_obj
                    continue

                curr_ver = len(events)
                if base_ver > curr_ver:
                    res_obj = {
                        "action_id": act_id,
                        "status": "rejected",
                        "resolution": "server_wins",
                        "error_code": "INVALID_BASE_VERSION",
                        "processed_order": processed_order,
                        "server_version": curr_ver,
                        "signature": None
                    }
                    rejected_count += 1
                    self._processed_actions[dedup_key] = res_obj
                    action_results[orig_idx] = res_obj
                    continue

                # Try append event
                append_res = scoring_service.append_event(
                    payload.get("event_id"),
                    mid,
                    payload.get("type"),
                    payload.get("payload", {})
                )

                if "error" in append_res:
                    res_obj = {
                        "action_id": act_id,
                        "status": "rejected",
                        "resolution": "server_wins",
                        "error_code": append_res["error"],
                        "processed_order": processed_order,
                        "server_version": curr_ver,
                        "signature": None
                    }
                    rejected_count += 1
                elif append_res.get("is_replay"):
                    res_obj = {
                        "action_id": act_id,
                        "status": "duplicate",
                        "resolution": "skipped_duplicate",
                        "error_code": None,
                        "processed_order": processed_order,
                        "server_version": append_res["match_version"],
                        "signature": append_res["signature"]
                    }
                    duplicate_count += 1
                else:
                    new_ver = append_res["match_version"]
                    resolution = "appended" if base_ver == curr_ver else "rebased_append"
                    res_obj = {
                        "action_id": act_id,
                        "status": "applied",
                        "resolution": resolution,
                        "error_code": None,
                        "processed_order": processed_order,
                        "server_version": new_ver,
                        "signature": append_res["signature"]
                    }
                    applied_count += 1

                self._processed_actions[dedup_key] = res_obj
                action_results[orig_idx] = res_obj

            elif act_type == "roster_transfer":
                pid = payload.get("player_id")
                history_resp = roster_service.get_history(pid)
                if history_resp is None:
                    res_obj = {
                        "action_id": act_id,
                        "status": "rejected",
                        "resolution": "server_wins",
                        "error_code": "PLAYER_NOT_FOUND",
                        "processed_order": processed_order,
                        "server_version": 0,
                        "signature": None
                    }
                    rejected_count += 1
                    self._processed_actions[dedup_key] = res_obj
                    action_results[orig_idx] = res_obj
                    continue

                curr_ver = len(history_resp["history"])
                if base_ver > curr_ver:
                    res_obj = {
                        "action_id": act_id,
                        "status": "rejected",
                        "resolution": "server_wins",
                        "error_code": "INVALID_BASE_VERSION",
                        "processed_order": processed_order,
                        "server_version": curr_ver,
                        "signature": None
                    }
                    rejected_count += 1
                    self._processed_actions[dedup_key] = res_obj
                    action_results[orig_idx] = res_obj
                    continue

                tr_res = roster_service.transfer_player(payload)
                if "error" in tr_res:
                    res_obj = {
                        "action_id": act_id,
                        "status": "rejected",
                        "resolution": "server_wins",
                        "error_code": tr_res["error"],
                        "processed_order": processed_order,
                        "server_version": curr_ver,
                        "signature": None
                    }
                    rejected_count += 1
                elif tr_res.get("is_replay"):
                    new_ver = len(roster_service.get_history(pid)["history"])
                    res_obj = {
                        "action_id": act_id,
                        "status": "duplicate",
                        "resolution": "skipped_duplicate",
                        "error_code": None,
                        "processed_order": processed_order,
                        "server_version": new_ver,
                        "signature": None
                    }
                    duplicate_count += 1
                else:
                    new_ver = len(roster_service.get_history(pid)["history"])
                    resolution = "appended" if base_ver == curr_ver else "rebased_append"
                    res_obj = {
                        "action_id": act_id,
                        "status": "applied",
                        "resolution": resolution,
                        "error_code": None,
                        "processed_order": processed_order,
                        "server_version": new_ver,
                        "signature": None
                    }
                    applied_count += 1

                self._processed_actions[dedup_key] = res_obj
                action_results[orig_idx] = res_obj

            else:
                res_obj = {
                    "action_id": act_id,
                    "status": "rejected",
                    "resolution": "server_wins",
                    "error_code": "VALIDATION_ERROR",
                    "processed_order": processed_order,
                    "server_version": 0,
                    "signature": None
                }
                rejected_count += 1
                self._processed_actions[dedup_key] = res_obj
                action_results[orig_idx] = res_obj

        # Return results in original request order
        final_results = [action_results[i] for i in range(len(actions))]

        return {
            "device_id": device_id,
            "results": final_results,
            "summary": {
                "applied": applied_count,
                "rejected": rejected_count,
                "duplicate": duplicate_count
            }
        }

offline_sync_service = OfflineSyncService()
