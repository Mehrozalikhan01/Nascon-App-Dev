from typing import Dict, Any, List, Optional
from app.core.config import settings
from app.core.security import sign, canonical
from app.services.sports import sports_service

class ScoringService:
    def __init__(self):
        # match_id -> list of events
        self._match_events: Dict[str, List[Dict[str, Any]]] = {}
        # event_id -> stored event
        self._events_by_id: Dict[str, Dict[str, Any]] = {}

    def reset(self):
        self._match_events = {}
        self._events_by_id = {}

    def get_match_events(self, match_id: str) -> Optional[List[Dict[str, Any]]]:
        return self._match_events.get(match_id)

    def init_match(self, match_id: str, sport_id: str, home_team_id: str, away_team_id: str, league_id: Optional[str] = None):
        if match_id in self._match_events:
            return {"error": "MATCH_EXISTS"}
        init_evt = {
            "sequence": 1,
            "event_id": f"init-{match_id}",
            "match_id": match_id,
            "type": "match_init",
            "payload": {
                "sport_id": sport_id,
                "home_team_id": home_team_id,
                "away_team_id": away_team_id,
                "league_id": league_id
            },
            "compensated": False,
            "compensated_by": None
        }
        init_evt["signature"] = sign(settings.DAY1_SALT, init_evt["event_id"], init_evt["payload"])
        self._match_events[match_id] = [init_evt]
        self._events_by_id[init_evt["event_id"]] = init_evt
        return {"status": "ok"}

    def append_event(self, event_id: str, match_id: str, evt_type: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        # Idempotency check
        if event_id in self._events_by_id:
            existing = self._events_by_id[event_id]
            if existing["match_id"] == match_id and existing["type"] == evt_type and existing["payload"] == payload:
                # Idempotent replay
                return {
                    "is_replay": True,
                    "event_id": event_id,
                    "match_id": match_id,
                    "type": evt_type,
                    "sequence": existing["sequence"],
                    "match_version": existing["sequence"],
                    "signature": existing["signature"]
                }
            return {"error": "DUPLICATE_EVENT_ID"}

        if evt_type == "match_init":
            if match_id in self._match_events:
                return {"error": "MATCH_EXISTS"}
            sport_id = payload.get("sport_id")
            sport = sports_service.get_sport(sport_id)
            if not sport:
                return {"error": "SPORT_NOT_FOUND"}
            home_t = payload.get("home_team_id")
            away_t = payload.get("away_team_id")
            if not home_t or not away_t:
                return {"error": "VALIDATION_ERROR", "fields": ["home_team_id", "away_team_id"]}
            self.init_match(match_id, sport_id, home_t, away_t, payload.get("league_id"))
            evt = self._match_events[match_id][0]
            evt["event_id"] = event_id
            evt["signature"] = sign(settings.DAY1_SALT, event_id, payload)
            self._events_by_id[event_id] = evt
            return {
                "event_id": event_id,
                "match_id": match_id,
                "type": evt_type,
                "sequence": 1,
                "match_version": 1,
                "signature": evt["signature"]
            }

        # For any other event, match must exist
        if match_id not in self._match_events:
            return {"error": "MATCH_NOT_FOUND"}

        # Compute current match projection
        proj = self.project_match(match_id)
        current_status = proj["status"]
        sport = sports_service.get_sport(proj["sport_id"])

        if evt_type == "match_start":
            if current_status != "upcoming":
                return {"error": "ILLEGAL_TRANSITION"}

        elif evt_type == "score":
            if current_status != "in_progress":
                return {"error": "MATCH_NOT_IN_PROGRESS"}
            team_id = payload.get("team_id")
            if team_id not in (proj["home"]["team_id"], proj["away"]["team_id"]):
                return {"error": "VALIDATION_ERROR", "fields": ["team_id"]}
            event_type = payload.get("event_type")
            if not event_type or event_type not in sport["scoring_events"]:
                return {"error": "UNKNOWN_EVENT_TYPE"}

            # Check model limits
            model = sport["scoring_model"]
            if model == "innings":
                p = sport["periods"]
                max_balls = p["innings_per_team"] * p["overs_per_innings"] * p["balls_per_over"]
                max_wickets = p["innings_per_team"] * p["wickets_per_innings"]
                t_stats = proj["home"] if team_id == proj["home"]["team_id"] else proj["away"]
                if t_stats["balls"] >= max_balls or t_stats["wickets"] >= max_wickets:
                    return {"error": "INNINGS_COMPLETE"}
            elif model == "round":
                best_of = sport["periods"]["best_of"]
                req_wins = (best_of + 1) // 2
                t_stats = proj["home"] if team_id == proj["home"]["team_id"] else proj["away"]
                if t_stats["score"] >= req_wins:
                    return {"error": "SERIES_ALREADY_DECIDED"}

        elif evt_type == "discipline":
            if current_status != "in_progress":
                return {"error": "MATCH_NOT_IN_PROGRESS"}
            team_id = payload.get("team_id")
            if team_id not in (proj["home"]["team_id"], proj["away"]["team_id"]):
                return {"error": "VALIDATION_ERROR", "fields": ["team_id"]}

        elif evt_type == "tiebreak_winner":
            if current_status != "in_progress":
                return {"error": "MATCH_NOT_IN_PROGRESS"}
            team_id = payload.get("team_id")
            if team_id not in (proj["home"]["team_id"], proj["away"]["team_id"]):
                return {"error": "VALIDATION_ERROR", "fields": ["team_id"]}

        elif evt_type == "match_end":
            if current_status != "in_progress":
                return {"error": "ILLEGAL_TRANSITION"}
            # Check draw / level score
            if proj["home"]["score"] == proj["away"]["score"]:
                if not sport["draw_allowed"] and not proj["tiebreak_winner_team_id"]:
                    return {"error": "TIE_NOT_ALLOWED"}

        elif evt_type == "compensation":
            comp_id = payload.get("compensates")
            if not comp_id or comp_id not in self._events_by_id:
                return {"error": "EVENT_NOT_FOUND"}
            target = self._events_by_id[comp_id]
            if target["match_id"] != match_id:
                return {"error": "INVALID_COMPENSATION"}
            if target["type"] not in ("score", "discipline", "tiebreak_winner"):
                return {"error": "INVALID_COMPENSATION"}
            if target["compensated"]:
                return {"error": "INVALID_COMPENSATION"}

        else:
            return {"error": "VALIDATION_ERROR", "fields": ["type"]}

        seq = len(self._match_events[match_id]) + 1
        sig = sign(settings.DAY1_SALT, event_id, payload)
        new_event = {
            "sequence": seq,
            "event_id": event_id,
            "match_id": match_id,
            "type": evt_type,
            "payload": payload,
            "signature": sig,
            "compensated": False,
            "compensated_by": None
        }

        if evt_type == "compensation":
            target = self._events_by_id[payload["compensates"]]
            target["compensated"] = True
            target["compensated_by"] = event_id

        self._match_events[match_id].append(new_event)
        self._events_by_id[event_id] = new_event

        return {
            "is_replay": False,
            "event_id": event_id,
            "match_id": match_id,
            "type": evt_type,
            "sequence": seq,
            "match_version": seq,
            "signature": sig
        }

    def project_match(self, match_id: str) -> Dict[str, Any]:
        events = self._match_events.get(match_id, [])
        if not events:
            return {}

        init_evt = events[0]
        init_p = init_evt["payload"]
        sport_id = init_p["sport_id"]
        sport = sports_service.get_sport(sport_id)
        home_t = init_p["home_team_id"]
        away_t = init_p["away_team_id"]
        league_id = init_p.get("league_id")

        model = sport["scoring_model"]
        bpo = sport["periods"].get("balls_per_over", 6)

        status = "upcoming"
        home_score = 0
        away_score = 0
        home_fp = 0
        away_fp = 0
        home_balls = 0 if model == "innings" else None
        away_balls = 0 if model == "innings" else None
        home_wickets = 0 if model == "innings" else None
        away_wickets = 0 if model == "innings" else None
        tiebreak_winner = None

        for evt in events:
            if evt["type"] == "match_start":
                status = "in_progress"
            elif evt["type"] == "match_end":
                status = "completed"

            # Skip compensated events
            if evt["compensated"]:
                continue

            if evt["type"] == "score":
                p = evt["payload"]
                team = p["team_id"]
                etype = p["event_type"]
                rule = sport["scoring_events"][etype]

                if model == "event":
                    pts = rule.get("points", 1)
                    if team == home_t:
                        home_score += pts
                    else:
                        away_score += pts

                elif model == "round":
                    rds = rule.get("rounds", 1)
                    if team == home_t:
                        home_score += rds
                    else:
                        away_score += rds

                elif model == "innings":
                    runs = rule.get("runs", 0)
                    is_legal = rule.get("legal_ball", True)
                    is_wicket = rule.get("wicket", False)
                    if team == home_t:
                        home_score += runs
                        if is_legal:
                            home_balls += 1
                        if is_wicket:
                            home_wickets += 1
                    else:
                        away_score += runs
                        if is_legal:
                            away_balls += 1
                        if is_wicket:
                            away_wickets += 1

            elif evt["type"] == "discipline":
                p = evt["payload"]
                team = p["team_id"]
                pts = p.get("points", 0)
                if team == home_t:
                    home_fp += pts
                else:
                    away_fp += pts

            elif evt["type"] == "tiebreak_winner":
                tiebreak_winner = evt["payload"]["team_id"]

        outcome = "pending"
        winner_team = None
        if status == "completed":
            if home_score > away_score:
                outcome = "home_win"
                winner_team = home_t
            elif away_score > home_score:
                outcome = "away_win"
                winner_team = away_t
            else:
                if sport["draw_allowed"]:
                    outcome = "draw"
                    winner_team = None
                else:
                    if tiebreak_winner:
                        outcome = "home_win" if tiebreak_winner == home_t else "away_win"
                        winner_team = tiebreak_winner
                    else:
                        outcome = "undecided"
                        winner_team = None

        home_overs = None
        away_overs = None
        if model == "innings":
            home_overs = f"{home_balls // bpo}.{home_balls % bpo}"
            away_overs = f"{away_balls // bpo}.{away_balls % bpo}"

        return {
            "match_id": match_id,
            "sport_id": sport_id,
            "league_id": league_id,
            "status": status,
            "outcome": outcome,
            "winner_team_id": winner_team,
            "tiebreak_winner_team_id": tiebreak_winner,
            "home": {
                "team_id": home_t,
                "score": home_score,
                "wickets": home_wickets,
                "balls": home_balls,
                "overs": home_overs,
                "fair_play": home_fp
            },
            "away": {
                "team_id": away_t,
                "score": away_score,
                "wickets": away_wickets,
                "balls": away_balls,
                "overs": away_overs,
                "fair_play": away_fp
            },
            "version": len(events),
            "events": [
                {
                    "sequence": e["sequence"],
                    "event_id": e["event_id"],
                    "type": e["type"],
                    "payload": e["payload"],
                    "signature": e["signature"],
                    "compensated": e["compensated"],
                    "compensated_by": e["compensated_by"]
                }
                for e in events
            ]
        }

scoring_service = ScoringService()
