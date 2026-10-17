import re
from typing import Dict, Any, List, Optional

class SportsService:
    def __init__(self):
        self._sports: Dict[str, Dict[str, Any]] = {}
        self.init_baseline()

    def init_baseline(self):
        self._sports = {
            "football": {
                "sport_id": "football",
                "name": "Football",
                "scoring_model": "event",
                "periods": {"count": 2, "duration_minutes": 45},
                "scoring_events": {"goal": {"points": 1}},
                "draw_allowed": True,
                "overtime": {"policy": "none"},
                "tie_break_order": ["h2h_points", "h2h_diff", "overall_diff", "scored", "fair_play", "draw_lots"],
                "roster": {"min_squad_size": 11, "max_squad_size": 25, "required_positions": {"gk": 1}}
            },
            "basketball": {
                "sport_id": "basketball",
                "name": "Basketball",
                "scoring_model": "event",
                "periods": {"count": 4, "duration_minutes": 12},
                "scoring_events": {
                    "free_throw": {"points": 1},
                    "two_pointer": {"points": 2},
                    "three_pointer": {"points": 3}
                },
                "draw_allowed": False,
                "overtime": {"policy": "extra_time", "duration_minutes": 5},
                "tie_break_order": ["h2h_points", "h2h_diff", "overall_diff", "scored", "fair_play", "draw_lots"],
                "roster": {"min_squad_size": 5, "max_squad_size": 15, "required_positions": {}}
            },
            "cricket": {
                "sport_id": "cricket",
                "name": "Cricket",
                "scoring_model": "innings",
                "periods": {
                    "innings_per_team": 1,
                    "overs_per_innings": 20,
                    "balls_per_over": 6,
                    "wickets_per_innings": 10
                },
                "scoring_events": {
                    "dot_ball": {"runs": 0, "legal_ball": True, "wicket": False},
                    "single": {"runs": 1, "legal_ball": True, "wicket": False},
                    "two": {"runs": 2, "legal_ball": True, "wicket": False},
                    "four": {"runs": 4, "legal_ball": True, "wicket": False},
                    "six": {"runs": 6, "legal_ball": True, "wicket": False},
                    "wide": {"runs": 1, "legal_ball": False, "wicket": False},
                    "no_ball": {"runs": 1, "legal_ball": False, "wicket": False},
                    "wicket": {"runs": 0, "legal_ball": True, "wicket": True}
                },
                "draw_allowed": True,
                "overtime": {"policy": "super_over"},
                "tie_break_order": ["h2h_points", "h2h_diff", "overall_diff", "scored", "fair_play", "draw_lots"],
                "roster": {"min_squad_size": 11, "max_squad_size": 18, "required_positions": {"wicketkeeper": 1}}
            },
            "esports": {
                "sport_id": "esports",
                "name": "Esports",
                "scoring_model": "round",
                "periods": {"best_of": 3},
                "scoring_events": {"round_win": {"rounds": 1}},
                "draw_allowed": False,
                "overtime": {"policy": "none"},
                "tie_break_order": ["h2h_points", "h2h_diff", "overall_diff", "scored", "fair_play", "draw_lots"],
                "roster": {"min_squad_size": 5, "max_squad_size": 8, "required_positions": {}}
            }
        }

    def get_all(self) -> List[str]:
        return list(self._sports.keys())

    def get_sport(self, sport_id: str) -> Optional[Dict[str, Any]]:
        return self._sports.get(sport_id)

    def register_sport(self, data: Dict[str, Any]) -> Dict[str, Any]:
        bad_fields = []
        sport_id = data.get("sport_id")
        if not sport_id or not isinstance(sport_id, str) or not re.match(r"^[a-z0-9_-]{2,40}$", sport_id):
            bad_fields.append("sport_id")

        name = data.get("name")
        if not name or not isinstance(name, str) or len(name) < 1 or len(name) > 80:
            bad_fields.append("name")

        model = data.get("scoring_model")
        if model not in ("event", "innings", "round"):
            bad_fields.append("scoring_model")

        periods = data.get("periods")
        if not isinstance(periods, dict):
            bad_fields.append("periods")
        else:
            if model == "event":
                if periods.get("count", 0) < 1 or periods.get("duration_minutes", 0) < 1:
                    bad_fields.append("periods")
            elif model == "innings":
                if any(periods.get(k, 0) < 1 for k in ("innings_per_team", "overs_per_innings", "balls_per_over", "wickets_per_innings")):
                    bad_fields.append("periods")
            elif model == "round":
                best_of = periods.get("best_of", 0)
                if best_of < 1 or best_of % 2 == 0:
                    bad_fields.append("periods")

        scoring_events = data.get("scoring_events")
        if not isinstance(scoring_events, dict) or len(scoring_events) < 1:
            bad_fields.append("scoring_events")
        else:
            for k, v in scoring_events.items():
                if not re.match(r"^[a-z0-9_]{1,40}$", k):
                    bad_fields.append("scoring_events")
                    break

        if "draw_allowed" not in data or not isinstance(data["draw_allowed"], bool):
            bad_fields.append("draw_allowed")

        overtime = data.get("overtime")
        if not isinstance(overtime, dict) or overtime.get("policy") not in ("none", "extra_time", "shootout", "golden_score", "super_over"):
            bad_fields.append("overtime")

        roster = data.get("roster")
        if not isinstance(roster, dict) or roster.get("min_squad_size", 0) < 1 or roster.get("max_squad_size", 0) < roster.get("min_squad_size", 0):
            bad_fields.append("roster")

        if bad_fields:
            return {"error": "VALIDATION_ERROR", "fields": sorted(list(set(bad_fields)))}

        if sport_id in self._sports:
            return {"error": "SPORT_EXISTS"}

        # Store default tie break order if omitted
        config = dict(data)
        if "tie_break_order" not in config:
            config["tie_break_order"] = ["h2h_points", "h2h_diff", "overall_diff", "scored", "fair_play", "draw_lots"]
        elif "draw_lots" not in config["tie_break_order"]:
            config["tie_break_order"].append("draw_lots")

        self._sports[sport_id] = config
        return {"status": "created", "sport_id": sport_id, "config": config}

sports_service = SportsService()
