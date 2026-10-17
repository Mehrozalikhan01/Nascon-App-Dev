import math
from typing import Dict, Any, List, Optional
from app.services.sports import sports_service
from app.services.scoring import scoring_service
from app.core.security import lots_key

class StandingsService:
    def __init__(self):
        # league_id -> config
        self._leagues: Dict[str, Dict[str, Any]] = {}

    def reset(self):
        self._leagues = {}

    def get_league(self, league_id: str) -> Optional[Dict[str, Any]]:
        return self._leagues.get(league_id)

    def register_league(self, data: Dict[str, Any]) -> Dict[str, Any]:
        league_id = data.get("league_id")
        sport_id = data.get("sport_id")
        teams = data.get("teams", [])
        fixtures = data.get("fixtures", [])

        if not league_id or not sport_id or not teams or not fixtures:
            return {"error": "VALIDATION_ERROR", "fields": ["league_id", "sport_id", "teams", "fixtures"]}

        sport = sports_service.get_sport(sport_id)
        if not sport:
            return {"error": "SPORT_NOT_FOUND"}

        if len(teams) < 2 or len(teams) > 40 or len(set(teams)) != len(teams):
            return {"error": "VALIDATION_ERROR", "fields": ["teams"]}

        if league_id in self._leagues:
            return {"error": "LEAGUE_EXISTS"}

        team_set = set(teams)
        fixture_ids = set()
        for f in fixtures:
            mid = f.get("match_id")
            h = f.get("home")
            a = f.get("away")
            if not mid or not h or not a or h == a or h not in team_set or a not in team_set:
                return {"error": "VALIDATION_ERROR", "fields": ["fixtures"]}
            if mid in fixture_ids or scoring_service.get_match_events(mid) is not None:
                return {"error": "MATCH_EXISTS"}
            fixture_ids.add(mid)

        points_cfg = data.get("points", {"win": 3, "draw": 1, "loss": 0})
        tie_break_order = data.get("tie_break_order", list(sport["tie_break_order"]))
        if "draw_lots" not in tie_break_order:
            tie_break_order.append("draw_lots")

        qual = data.get("qualification", {"spots": 1})
        spots = qual.get("spots", 1)
        if spots < 1 or spots > len(teams):
            return {"error": "VALIDATION_ERROR", "fields": ["qualification.spots"]}

        lots_seed = data.get("lots_seed", 42)

        # Register fixtures in scoring engine
        for f in fixtures:
            scoring_service.init_match(f["match_id"], sport_id, f["home"], f["away"], league_id)

        league_obj = {
            "league_id": league_id,
            "sport_id": sport_id,
            "teams": list(teams),
            "fixtures": list(fixtures),
            "points": points_cfg,
            "tie_break_order": tie_break_order,
            "qualification": {"spots": spots},
            "lots_seed": lots_seed
        }
        self._leagues[league_id] = league_obj

        return {
            "status": "created",
            "league_id": league_id,
            "team_count": len(teams),
            "fixture_count": len(fixtures)
        }

    def compute_standings(self, league_id: str) -> Optional[Dict[str, Any]]:
        league = self._leagues.get(league_id)
        if not league:
            return None

        sport_id = league["sport_id"]
        sport = sports_service.get_sport(sport_id)
        teams = league["teams"]
        fixtures = league["fixtures"]
        points_cfg = league["points"]
        win_pts = points_cfg.get("win", 3)
        draw_pts = points_cfg.get("draw", 1)
        loss_pts = points_cfg.get("loss", 0)
        tie_break_order = league["tie_break_order"]
        k_spots = league["qualification"]["spots"]
        lots_seed = league["lots_seed"]
        model = sport["scoring_model"]
        bpo = sport["periods"].get("balls_per_over", 6)
        max_wickets = sport["periods"].get("wickets_per_innings", 10)
        max_overs = sport["periods"].get("overs_per_innings", 20)

        # Collect match projections
        completed_matches = []
        remaining_matches_count = {t: 0 for t in teams}

        for f in fixtures:
            mid = f["match_id"]
            proj = scoring_service.project_match(mid)
            if proj.get("status") == "completed" and proj.get("outcome") != "undecided":
                completed_matches.append(proj)
            else:
                remaining_matches_count[f["home"]] += 1
                remaining_matches_count[f["away"]] += 1

        # Build H2H table for all pairs
        h2h: Dict[str, Dict[str, Dict[str, int]]] = {
            t1: {t2: {"played": 0, "points": 0, "scored": 0, "conceded": 0} for t2 in teams}
            for t1 in teams
        }

        # Global stats
        stats = {
            t: {
                "played": 0, "won": 0, "drawn": 0, "lost": 0,
                "scored": 0, "conceded": 0, "diff": 0.0, "points": 0, "fair_play": 0,
                # cricket specifics
                "runs_scored": 0, "overs_faced_dec": 0.0,
                "runs_conceded": 0, "overs_bowled_dec": 0.0
            }
            for t in teams
        }

        for m in completed_matches:
            ht = m["home"]["team_id"]
            at = m["away"]["team_id"]
            hs = m["home"]["score"]
            as_ = m["away"]["score"]
            outcome = m["outcome"]

            h2h[ht][at]["played"] += 1
            h2h[at][ht]["played"] += 1
            h2h[ht][at]["scored"] += hs
            h2h[ht][at]["conceded"] += as_
            h2h[at][ht]["scored"] += as_
            h2h[at][ht]["conceded"] += hs

            stats[ht]["played"] += 1
            stats[at]["played"] += 1
            stats[ht]["scored"] += hs
            stats[ht]["conceded"] += as_
            stats[at]["scored"] += as_
            stats[at]["conceded"] += hs
            stats[ht]["fair_play"] += m["home"]["fair_play"]
            stats[at]["fair_play"] += m["away"]["fair_play"]

            h_pts = 0
            a_pts = 0
            if outcome == "home_win":
                stats[ht]["won"] += 1
                stats[at]["lost"] += 1
                h_pts = win_pts
                a_pts = loss_pts
            elif outcome == "away_win":
                stats[at]["won"] += 1
                stats[ht]["lost"] += 1
                h_pts = loss_pts
                a_pts = win_pts
            elif outcome == "draw":
                stats[ht]["drawn"] += 1
                stats[at]["drawn"] += 1
                h_pts = draw_pts
                a_pts = draw_pts

            stats[ht]["points"] += h_pts
            stats[at]["points"] += a_pts
            h2h[ht][at]["points"] += h_pts
            h2h[at][ht]["points"] += a_pts

            if model == "innings":
                # Overs faced calculation
                # If all out (wickets >= wickets_per_innings), count full overs_per_innings
                h_wickets = m["home"]["wickets"] or 0
                h_balls = m["home"]["balls"] or 0
                if h_wickets >= max_wickets:
                    h_overs_dec = float(max_overs)
                else:
                    h_overs_dec = (h_balls // bpo) + (h_balls % bpo) / bpo

                a_wickets = m["away"]["wickets"] or 0
                a_balls = m["away"]["balls"] or 0
                if a_wickets >= max_wickets:
                    a_overs_dec = float(max_overs)
                else:
                    a_overs_dec = (a_balls // bpo) + (a_balls % bpo) / bpo

                stats[ht]["runs_scored"] += hs
                stats[ht]["overs_faced_dec"] += h_overs_dec
                stats[ht]["runs_conceded"] += as_
                stats[ht]["overs_bowled_dec"] += a_overs_dec

                stats[at]["runs_scored"] += as_
                stats[at]["overs_faced_dec"] += a_overs_dec
                stats[at]["runs_conceded"] += hs
                stats[at]["overs_bowled_dec"] += h_overs_dec

        # Compute diffs
        for t in teams:
            if model == "innings":
                rf = stats[t]["runs_scored"]
                of = stats[t]["overs_faced_dec"]
                rc = stats[t]["runs_conceded"]
                ob = stats[t]["overs_bowled_dec"]
                rate_for = (rf / of) if of > 0 else 0.0
                rate_against = (rc / ob) if ob > 0 else 0.0
                stats[t]["diff"] = round(rate_for - rate_against, 3)
            else:
                stats[t]["diff"] = stats[t]["scored"] - stats[t]["conceded"]

        # Recursive Resolution Algorithm (Section 5.10)
        def compute_criterion(group: List[str], crit: str) -> Dict[str, Any]:
            res = {}
            if crit == "h2h_points":
                for t in group:
                    pts = 0
                    for m in completed_matches:
                        ht = m["home"]["team_id"]
                        at = m["away"]["team_id"]
                        if ht in group and at in group:
                            if t == ht:
                                pts += win_pts if m["outcome"] == "home_win" else (draw_pts if m["outcome"] == "draw" else loss_pts)
                            elif t == at:
                                pts += win_pts if m["outcome"] == "away_win" else (draw_pts if m["outcome"] == "draw" else loss_pts)
                    res[t] = pts
            elif crit == "h2h_diff":
                for t in group:
                    if model == "innings":
                        rf, of, rc, ob = 0, 0.0, 0, 0.0
                        for m in completed_matches:
                            ht = m["home"]["team_id"]
                            at = m["away"]["team_id"]
                            if ht in group and at in group:
                                hs = m["home"]["score"]
                                as_ = m["away"]["score"]
                                hw = m["home"]["wickets"] or 0
                                hb = m["home"]["balls"] or 0
                                aw = m["away"]["wickets"] or 0
                                ab = m["away"]["balls"] or 0
                                h_ov = float(max_overs) if hw >= max_wickets else ((hb // bpo) + (hb % bpo) / bpo)
                                a_ov = float(max_overs) if aw >= max_wickets else ((ab // bpo) + (ab % bpo) / bpo)
                                if t == ht:
                                    rf += hs
                                    of += h_ov
                                    rc += as_
                                    ob += a_ov
                                else:
                                    rf += as_
                                    of += a_ov
                                    rc += hs
                                    ob += h_ov
                        rate_for = (rf / of) if of > 0 else 0.0
                        rate_against = (rc / ob) if ob > 0 else 0.0
                        res[t] = round(rate_for - rate_against, 3)
                    else:
                        s_for = 0
                        s_against = 0
                        for m in completed_matches:
                            ht = m["home"]["team_id"]
                            at = m["away"]["team_id"]
                            if ht in group and at in group:
                                if t == ht:
                                    s_for += m["home"]["score"]
                                    s_against += m["away"]["score"]
                                elif t == at:
                                    s_for += m["away"]["score"]
                                    s_against += m["home"]["score"]
                        res[t] = s_for - s_against
            elif crit == "overall_diff":
                for t in group:
                    res[t] = stats[t]["diff"]
            elif crit == "scored":
                for t in group:
                    res[t] = stats[t]["scored"]
            elif crit == "fair_play":
                for t in group:
                    res[t] = stats[t]["fair_play"]
            elif crit == "draw_lots":
                for t in group:
                    # Ascending: lower SHA256 hex is better!
                    res[t] = lots_key(lots_seed, league_id, t)
            return res

        # Map of (teamA, teamB) -> criterion that separated them
        separation_reason: Dict[str, Optional[str]] = {t: None for t in teams}

        def resolve(group: List[str]) -> List[str]:
            if len(group) <= 1:
                return group

            for crit in tie_break_order:
                vals = compute_criterion(group, crit)
                unique_vals = set(vals.values())
                if len(unique_vals) > 1:
                    # Values are not all equal, split into sub-groups
                    if crit == "draw_lots":
                        # ascending
                        sorted_unique = sorted(list(unique_vals))
                    else:
                        # descending (higher is better)
                        sorted_unique = sorted(list(unique_vals), reverse=True)

                    ordered_res = []
                    subgroups = []
                    for v in sorted_unique:
                        sub = [t for t in group if vals[t] == v]
                        subgroups.append((v, sub))

                    # For teams placed above the next sub-group, assign resolved_by
                    for idx in range(len(subgroups) - 1):
                        current_sub = subgroups[idx][1]
                        for member in current_sub:
                            separation_reason[member] = crit

                    for _, sub in subgroups:
                        if len(sub) > 1:
                            ordered_res.extend(resolve(sub))
                        else:
                            ordered_res.extend(sub)
                    return ordered_res

            # Fallback to draw_lots
            vals = compute_criterion(group, "draw_lots")
            sorted_by_lots = sorted(group, key=lambda t: vals[t])
            for i in range(len(sorted_by_lots) - 1):
                separation_reason[sorted_by_lots[i]] = "draw_lots"
            return sorted_by_lots

        # Group by points descending
        points_groups: Dict[int, List[str]] = {}
        for t in teams:
            pts = stats[t]["points"]
            points_groups.setdefault(pts, []).append(t)

        final_ranking = []
        for pts_val in sorted(points_groups.keys(), reverse=True):
            grp = points_groups[pts_val]
            if len(grp) == 1:
                final_ranking.append(grp[0])
            else:
                resolved_grp = resolve(grp)
                final_ranking.extend(resolved_grp)

        # Qualification Status (uses points only, tie-breaks ignored!)
        # Let W = win points, R(T) = remaining fixtures of team T
        # max(T) = points(T) + W * R(T), and k = qualification.spots
        max_points = {t: stats[t]["points"] + win_pts * remaining_matches_count[t] for t in teams}

        qualification_status = {}
        for t in teams:
            cur_pts = stats[t]["points"]
            t_max = max_points[t]

            # clinched if fewer than k other teams X satisfy max(X) >= points(T)
            others_can_beat_or_tie = sum(1 for x in teams if x != t and max_points[x] >= cur_pts)
            # eliminated if at least k other teams X satisfy points(X) > max(T)
            others_already_beat = sum(1 for x in teams if x != t and stats[x]["points"] > t_max)

            if others_can_beat_or_tie < k_spots:
                qualification_status[t] = "clinched"
            elif others_already_beat >= k_spots:
                qualification_status[t] = "eliminated"
            else:
                qualification_status[t] = "still_possible"

        # Build final table
        table = []
        for rank, t in enumerate(final_ranking, start=1):
            table.append({
                "rank": rank,
                "team_id": t,
                "played": stats[t]["played"],
                "won": stats[t]["won"],
                "drawn": stats[t]["drawn"],
                "lost": stats[t]["lost"],
                "scored": stats[t]["scored"],
                "conceded": stats[t]["conceded"],
                "diff": stats[t]["diff"],
                "points": stats[t]["points"],
                "fair_play": stats[t]["fair_play"],
                "status": qualification_status[t],
                "resolved_by": separation_reason[t]
            })

        return {
            "league_id": league_id,
            "sport_id": sport_id,
            "qualification_spots": k_spots,
            "table": table,
            "h2h": h2h
        }

standings_service = StandingsService()
