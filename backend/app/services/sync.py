from typing import Dict, Any, List, Optional
from datetime import datetime
from app.services.scoring import scoring_service
from app.services.roster import roster_service

class OfflineSyncService:
    def __init__(self):
        # (device_id, action_id) -> stored result
        self._processed_actions: Dict[str, Dict[str, Any]] = {}

    def reset(self):
        self._processed_actions = {}

    def sync_actions(self, device_id: str, actions: List[Dict[str, Any]]) -> Dict[str, Any]:
        if not actions or len(actions) < 1 or len(actions) > 100:
            return {"error": "VALIDATION_ERROR", "fields": ["actions"]}

        # Tag original order
        indexed_actions = []
        for idx, act in enumerate(actions):
            if "action_id" not in act or "type" not in act or "queued_at" not in act or "base_version" not in act:
                return {"error": "VALIDATION_ERROR", "fields": ["actions"]}
            indexed_actions.append((idx, act))

        # Sort by queued_at ascending, tie breaker: array order
        indexed_actions.sort(key=lambda item: (item[1]["queued_at"], item[0]))

        applied_count = 0
        rejected_count = 0
        duplicate_count = 0

        action_results: Dict[int, Dict[str, Any]] = {}

        for processed_order, (orig_idx, act) in enumerate(indexed_actions, start=1):
            act_id = act["action_id"]
            act_type = act["type"]
            base_ver = act["base_version"]
            payload = act.get("payload", {})

            dedup_key = f"{device_id}:{act_id}"
            if dedup_key in self._processed_actions:
                prev = self._processed_actions[dedup_key]
                res_obj = dict(prev)
                res_obj["processed_order"] = processed_order
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
