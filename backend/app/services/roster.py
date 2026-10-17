from datetime import datetime, date
from typing import Dict, Any, List, Optional
import uuid

class RosterService:
    def __init__(self):
        # league_id -> league config
        self._leagues: Dict[str, Dict[str, Any]] = {}
        # squad_id -> squad info
        self._squads: Dict[str, Dict[str, Any]] = {}
        # player_id -> player info
        self._players: Dict[str, Dict[str, Any]] = {}
        # player_id -> list of audit entries
        self._player_history: Dict[str, List[Dict[str, Any]]] = {}
        # request_id -> stored transfer response
        self._requests_by_id: Dict[str, Dict[str, Any]] = {}
        self._audit_seq = 0

    def reset(self):
        self._leagues = {}
        self._squads = {}
        self._players = {}
        self._player_history = {}
        self._requests_by_id = {}
        self._audit_seq = 0

    def setup_roster(self, data: Dict[str, Any]) -> Dict[str, Any]:
        league = data.get("league", {})
        league_id = league.get("league_id")
        tenant_id = league.get("tenant_id")
        sport_id = league.get("sport_id")
        tw = league.get("transfer_window", {})
        max_size = league.get("max_squad_size", 25)
        req_pos = league.get("required_positions", {})

        squads = data.get("squads", [])
        players = data.get("players", [])
        matches_in_prog = data.get("matches_in_progress", [])

        # Clean existing state for this league
        self._leagues[league_id] = {
            "league_id": league_id,
            "tenant_id": tenant_id,
            "sport_id": sport_id,
            "transfer_window": tw,
            "max_squad_size": max_size,
            "required_positions": req_pos,
            "matches_in_progress": matches_in_prog
        }

        squad_counts = {}
        for sq in squads:
            sq_id = sq["squad_id"]
            self._squads[sq_id] = {
                "squad_id": sq_id,
                "club_id": sq.get("club_id"),
                "tenant_id": sq.get("tenant_id"),
                "name": sq.get("name"),
                "venue": sq.get("venue"),
                "staff": dict(sq.get("staff", {})),
                "league_id": league_id,
                "player_ids": []
            }
            squad_counts[sq_id] = 0

        for pl in players:
            pid = pl["player_id"]
            sq_id = pl["squad_id"]
            pos = pl.get("position", "generic")
            role = pl.get("role", "starter")
            self._players[pid] = {
                "player_id": pid,
                "name": pl.get("name"),
                "position": pos,
                "squad_id": sq_id,
                "league_id": league_id,
                "role": role,
                "is_on_loan": False,
                "parent_squad_id": None,
                "loan_end_date": None
            }
            if sq_id in self._squads:
                self._squads[sq_id]["player_ids"].append(pid)
                squad_counts[sq_id] = len(self._squads[sq_id]["player_ids"])

            if pid not in self._player_history:
                self._player_history[pid] = []

        return {
            "league_id": league_id,
            "status": "configured",
            "squad_counts": squad_counts
        }

    def get_squad_profile(self, squad_id: str) -> Optional[Dict[str, Any]]:
        squad = self._squads.get(squad_id)
        if squad is None:
            return None
        profile = {key: value for key, value in squad.items() if key != "player_ids"}
        profile["roster"] = [
            dict(self._players[player_id])
            for player_id in squad["player_ids"]
            if player_id in self._players
        ]
        return profile

    def transfer_player(self, data: Dict[str, Any]) -> Dict[str, Any]:
        req_id = data.get("request_id")
        if req_id and req_id in self._requests_by_id:
            return {"is_replay": True, "response": self._requests_by_id[req_id]}

        # Check 1: Schema, dates, loan_end_date > effective_date, to_squad_id present unless release
        tr_type = data.get("type")
        eff_date_str = data.get("effective_date")
        loan_end_str = data.get("loan_end_date")
        to_sq_id = data.get("to_squad_id")

        if tr_type not in ("transfer", "loan", "release"):
            return {"error": "VALIDATION_ERROR", "fields": ["type"]}

        if not eff_date_str:
            return {"error": "VALIDATION_ERROR", "fields": ["effective_date"]}

        try:
            eff_date = date.fromisoformat(eff_date_str)
        except Exception:
            return {"error": "VALIDATION_ERROR", "fields": ["effective_date"]}

        if tr_type in ("transfer", "loan") and not to_sq_id:
            return {"error": "VALIDATION_ERROR", "fields": ["to_squad_id"]}

        if tr_type == "loan":
            if not loan_end_str:
                return {"error": "VALIDATION_ERROR", "fields": ["loan_end_date"]}
            try:
                loan_end = date.fromisoformat(loan_end_str)
                if loan_end <= eff_date:
                    return {"error": "VALIDATION_ERROR", "fields": ["loan_end_date"]}
            except Exception:
                return {"error": "VALIDATION_ERROR", "fields": ["loan_end_date"]}

        # Check 2: League / player / squads exist
        league_id = data.get("league_id")
        player_id = data.get("player_id")
        from_sq_id = data.get("from_squad_id")

        if league_id not in self._leagues:
            return {"error": "LEAGUE_NOT_FOUND"}
        if player_id not in self._players:
            return {"error": "PLAYER_NOT_FOUND"}
        if from_sq_id not in self._squads:
            return {"error": "SQUAD_NOT_FOUND"}
        if tr_type != "release" and to_sq_id not in self._squads:
            return {"error": "SQUAD_NOT_FOUND"}

        league = self._leagues[league_id]
        player = self._players[player_id]
        from_squad = self._squads[from_sq_id]
        to_squad = self._squads.get(to_sq_id)

        # Check 3: From and to squads belong to league's tenant
        if from_squad.get("tenant_id") != league.get("tenant_id"):
            return {"error": "CROSS_TENANT_TRANSFER"}
        if to_squad and to_squad.get("tenant_id") != league.get("tenant_id"):
            return {"error": "CROSS_TENANT_TRANSFER"}

        # Check 4: Player's current squad in this league equals from_squad_id
        if player.get("squad_id") != from_sq_id or player.get("league_id") != league_id:
            return {"error": "NOT_IN_SQUAD"}

        # Check 5: transfer/loan: effective_date inside window (inclusive). Release is allowed on any date.
        if tr_type in ("transfer", "loan"):
            tw = league.get("transfer_window", {})
            try:
                open_d = date.fromisoformat(tw.get("open", "2000-01-01"))
                close_d = date.fromisoformat(tw.get("close", "2099-12-31"))
                if not (open_d <= eff_date <= close_d):
                    return {"error": "TRANSFER_WINDOW_CLOSED"}
            except Exception:
                return {"error": "TRANSFER_WINDOW_CLOSED"}

        # Check 6: Player is not currently on loan
        if player.get("is_on_loan"):
            return {"error": "PLAYER_ON_LOAN"}

        # Check 7: transfer/loan: destination squad count + 1 <= max_squad_size
        if tr_type in ("transfer", "loan"):
            max_squad = league.get("max_squad_size", 25)
            if len(to_squad["player_ids"]) + 1 > max_squad:
                return {"error": "SQUAD_FULL"}

        # Check 8: After move, from squad still has >= required count for player's position
        req_positions = league.get("required_positions", {})
        pos = player.get("position")
        if pos in req_positions:
            req_min = req_positions[pos]
            current_pos_count = sum(
                1 for pid in from_squad["player_ids"]
                if self._players[pid].get("position") == pos
            )
            if current_pos_count - 1 < req_min:
                return {"error": "MIN_POSITION_VIOLATION"}

        # In-progress match check (Edge Case 6)
        blocking_match_id = None
        for m_live in league.get("matches_in_progress", []):
            if from_sq_id in (m_live.get("home_squad_id"), m_live.get("away_squad_id")):
                blocking_match_id = m_live.get("match_id")
                break

        state = "deferred_until_match_end" if blocking_match_id else "active"

        # Apply state changes if active
        if state == "active":
            from_squad["player_ids"].remove(player_id)
            if tr_type == "transfer":
                to_squad["player_ids"].append(player_id)
                player["squad_id"] = to_sq_id
                player["role"] = "substitute"
            elif tr_type == "loan":
                to_squad["player_ids"].append(player_id)
                player["squad_id"] = to_sq_id
                player["is_on_loan"] = True
                player["parent_squad_id"] = from_sq_id
                player["loan_end_date"] = loan_end_str
                player["role"] = "substitute"
            elif tr_type == "release":
                player["squad_id"] = None
                player["role"] = None

        self._audit_seq += 1
        audit_id = f"au-{self._audit_seq:04d}"
        now_iso = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")

        audit_entry = {
            "audit_id": audit_id,
            "action": tr_type,
            "player_id": player_id,
            "league_id": league_id,
            "from_squad_id": from_sq_id,
            "to_squad_id": to_sq_id,
            "effective_date": eff_date_str,
            "state": state,
            "who": data.get("requested_by", "user-system"),
            "when": now_iso,
            "why": data.get("reason", "Roster move")
        }
        self._player_history.setdefault(player_id, []).append(audit_entry)

        registration = None
        if tr_type == "transfer":
            registration = {
                "player_id": player_id,
                "league_id": league_id,
                "squad_id": to_sq_id,
                "parent_squad_id": None,
                "registration_type": "transfer",
                "start_date": eff_date_str,
                "loan_end_date": None,
                "role": "substitute"
            }
        elif tr_type == "loan":
            registration = {
                "player_id": player_id,
                "league_id": league_id,
                "squad_id": to_sq_id,
                "parent_squad_id": from_sq_id,
                "registration_type": "loan",
                "start_date": eff_date_str,
                "loan_end_date": loan_end_str,
                "role": "substitute"
            }

        response = {
            "request_id": req_id,
            "status": "accepted",
            "state": state,
            "blocking_match_id": blocking_match_id,
            "registration": registration,
            "audit_entry": audit_entry
        }

        if req_id:
            self._requests_by_id[req_id] = response

        return {"is_replay": False, "response": response}

    def get_history(self, player_id: str, as_of_str: Optional[str] = None) -> Optional[Dict[str, Any]]:
        if player_id not in self._players:
            return None

        player = self._players[player_id]
        league_id = player.get("league_id")
        history = list(self._player_history.get(player_id, []))

        # Check loan auto-return if as_of is past loan_end_date
        current_sq = player.get("squad_id")
        parent_sq = player.get("parent_squad_id")

        if as_of_str and player.get("is_on_loan") and player.get("loan_end_date"):
            try:
                as_of_date = date.fromisoformat(as_of_str)
                loan_end = date.fromisoformat(player["loan_end_date"])
                if as_of_date > loan_end:
                    # Check if already recorded in history
                    has_return = any(h["action"] == "loan_return" for h in history)
                    if not has_return:
                        self._audit_seq += 1
                        return_entry = {
                            "audit_id": f"au-{self._audit_seq:04d}",
                            "action": "loan_return",
                            "player_id": player_id,
                            "league_id": league_id,
                            "from_squad_id": player.get("squad_id"),
                            "to_squad_id": parent_sq,
                            "effective_date": as_of_str,
                            "state": "active",
                            "who": "system",
                            "when": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
                            "why": "Loan period expired"
                        }
                        history.append(return_entry)
                        self._player_history[player_id].append(return_entry)

                        # Update state back to lender
                        if player["squad_id"] in self._squads:
                            self._squads[player["squad_id"]]["player_ids"].remove(player_id)
                        if parent_sq in self._squads:
                            self._squads[parent_sq]["player_ids"].append(player_id)

                        player["squad_id"] = parent_sq
                        player["parent_squad_id"] = None
                        player["is_on_loan"] = False
                        player["loan_end_date"] = None
                        player["role"] = "substitute"
                        current_sq = parent_sq
                        parent_sq = None
            except Exception:
                pass

        return {
            "player_id": player_id,
            "as_of": as_of_str or datetime.utcnow().strftime("%Y-%m-%d"),
            "league_id": league_id,
            "current_squad_id": current_sq,
            "parent_squad_id": parent_sq,
            "history": history
        }

roster_service = RosterService()
