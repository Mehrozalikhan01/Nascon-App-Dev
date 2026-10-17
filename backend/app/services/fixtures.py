import math
from typing import List, Dict, Any, Optional, Tuple, Set

# --- ROUND ROBIN ---
def generate_round_robin(teams: List[str], params: Dict[str, Any] = None) -> Dict[str, Any]:
    params = params or {}
    legs = params.get("legs", 1)
    withdrawals = params.get("withdrawals", [])
    withdrawal_policy = params.get("withdrawal_policy", "forfeit_remaining")

    n = len(teams)
    if n < 2 or n > 40:
        raise ValueError("Team count must be between 2 and 40")
    if len(set(teams)) != n:
        raise ValueError("Teams must be unique")

    rounds_per_leg = n - 1 if n % 2 == 0 else n
    total_rounds = rounds_per_leg * legs

    team_set = set(teams)
    for w in withdrawals:
        if w.get("team_id") not in team_set:
            raise ValueError(f"Unknown team in withdrawals: {w.get('team_id')}")
        ar = w.get("after_round", 0)
        if ar < 0 or ar > total_rounds:
            raise ValueError(f"after_round out of range: {ar}")

    circle_teams = list(teams)
    if n % 2 != 0:
        circle_teams.append(None)
    N = len(circle_teams)
    half = N // 2

    leg1_rounds = []
    current_circle = list(circle_teams)
    for r_idx in range(rounds_per_leg):
        round_num = r_idx + 1
        matches = []
        byes = []
        for i in range(half):
            t1 = current_circle[i]
            t2 = current_circle[N - 1 - i]
            if t1 is None:
                byes.append(t2)
            elif t2 is None:
                byes.append(t1)
            else:
                if (r_idx + i) % 2 == 0:
                    home, away = t1, t2
                else:
                    home, away = t2, t1
                matches.append((home, away))

        leg1_rounds.append({"round": round_num, "matches": matches, "byes": byes})
        current_circle = [current_circle[0]] + [current_circle[-1]] + current_circle[1:-1]

    rounds_list = []
    summary = {"total": 0, "scheduled": 0, "forfeit": 0, "voided": 0}

    for l in range(1, legs + 1):
        for r_obj in leg1_rounds:
            global_round = (l - 1) * rounds_per_leg + r_obj["round"]
            raw_matches = r_obj["matches"] if l == 1 else [(a, h) for (h, a) in r_obj["matches"]]
            
            formatted_matches = []
            for m_idx, (home, away) in enumerate(raw_matches, start=1):
                match_id = f"RR-L{l}-R{global_round}-M{m_idx}"
                status = "scheduled"
                awarded_to = None

                w_home = next((w for w in withdrawals if w["team_id"] == home), None)
                w_away = next((w for w in withdrawals if w["team_id"] == away), None)

                h_withdrawn = (w_home is not None) and (withdrawal_policy == "void_all" or global_round > w_home["after_round"])
                a_withdrawn = (w_away is not None) and (withdrawal_policy == "void_all" or global_round > w_away["after_round"])

                if h_withdrawn and a_withdrawn:
                    status = "voided"
                    awarded_to = None
                elif h_withdrawn:
                    if withdrawal_policy == "void_all":
                        status = "voided"
                        awarded_to = None
                    else:
                        status = "forfeit"
                        awarded_to = away
                elif a_withdrawn:
                    if withdrawal_policy == "void_all":
                        status = "voided"
                        awarded_to = None
                    else:
                        status = "forfeit"
                        awarded_to = home

                summary["total"] += 1
                summary[status] += 1

                formatted_matches.append({
                    "match_id": match_id,
                    "leg": l,
                    "home": home,
                    "away": away,
                    "status": status,
                    "awarded_to": awarded_to
                })

            rounds_list.append({
                "round": global_round,
                "matches": formatted_matches,
                "byes": list(r_obj["byes"])
            })

    return {
        "format": "round_robin",
        "legs": legs,
        "team_count": n,
        "round_count": total_rounds,
        "rounds": rounds_list,
        "summary": summary
    }


# --- SWISS SYSTEM ---
class NoValidPairingException(Exception):
    def __init__(self, round_num: int):
        self.round_num = round_num

def generate_swiss(teams: List[str], params: Dict[str, Any] = None) -> Dict[str, Any]:
    params = params or {}
    n = len(teams)
    if n < 2 or n > 64:
        raise ValueError("Team count must be between 2 and 64")
    if len(set(teams)) != n:
        raise ValueError("Teams must be unique")

    default_total = math.ceil(math.log2(n)) if n > 1 else 1
    total_rounds = params.get("total_rounds", default_total)
    round_num = params.get("round", 1)
    history = params.get("history", [])

    if round_num < 1 or round_num > total_rounds:
        raise ValueError("round out of range")
    if len(history) != round_num - 1:
        raise ValueError(f"history must contain exactly {round_num - 1} rounds")

    # Compute scores, played matches, and byes from history
    scores = {t: 0.0 for t in teams}
    opponents: Dict[str, List[str]] = {t: [] for t in teams}
    had_bye: Set[str] = set()

    for h in history:
        b = h.get("bye")
        if b:
            had_bye.add(b)
            scores[b] += 1.0
        for m in h.get("matches", []):
            ht = m["home"]
            at = m["away"]
            res = m["result"]
            opponents[ht].append(at)
            opponents[at].append(ht)
            if res == "home":
                scores[ht] += 1.0
            elif res == "away":
                scores[at] += 1.0
            elif res == "draw":
                scores[ht] += 0.5
                scores[at] += 0.5

    # Calculate Buchholz
    buchholz = {t: sum(scores[opp] for opp in opponents[t]) for t in teams}
    seed_idx = {t: idx for idx, t in enumerate(teams)}

    # Standings sorted by score desc, buchholz desc, seed asc
    standings_teams = sorted(teams, key=lambda t: (-scores[t], -buchholz[t], seed_idx[t]))
    standings = []
    for rank, t in enumerate(standings_teams, start=1):
        standings.append({
            "rank": rank,
            "team_id": t,
            "score": scores[t],
            "buchholz": buchholz[t]
        })

    # Round 1 exact pairing
    if round_num == 1:
        m_half = n // 2
        bye_team = teams[-1] if n % 2 != 0 else None
        pairings = []
        for i in range(m_half):
            home = teams[i]
            away = teams[i + m_half]
            pairings.append({
                "match_id": f"SW-R1-M{i+1}",
                "home": home,
                "away": away
            })
        return {
            "format": "swiss",
            "round": 1,
            "total_rounds": total_rounds,
            "pairings": pairings,
            "bye": bye_team,
            "standings": standings
        }

    # Rounds >= 2 with backtracking and minimum score difference
    is_odd = (n % 2 != 0)
    bye_candidates = []
    if is_odd:
        # candidate lowest scoring without bye (tie: largest index in teams)
        eligible_for_bye = [t for t in teams if t not in had_bye]
        if not eligible_for_bye:
            eligible_for_bye = list(teams)
        bye_candidates = sorted(eligible_for_bye, key=lambda t: (scores[t], -seed_idx[t]))
    else:
        bye_candidates = [None]

    all_met = {t: set(opponents[t]) for t in teams}

    def solve_pairs(available: List[str]) -> Optional[Tuple[float, List[Tuple[str, str]]]]:
        if not available:
            return 0.0, []
        t1 = available[0]
        best_cost = float("inf")
        best_solution = None

        for idx in range(1, len(available)):
            t2 = available[idx]
            if t2 not in all_met[t1]:
                cost = abs(scores[t1] - scores[t2])
                rem = [t for j, t in enumerate(available) if j != 0 and j != idx]
                sub = solve_pairs(rem)
                if sub is not None:
                    tot_cost = cost + sub[0]
                    if tot_cost < best_cost:
                        best_cost = tot_cost
                        best_solution = [(t1, t2)] + sub[1]
        if best_solution is not None:
            return best_cost, best_solution
        return None

    for bye_t in bye_candidates:
        active = [t for t in teams if t != bye_t]
        # Sort active by score desc to optimize search
        active.sort(key=lambda t: (-scores[t], seed_idx[t]))
        res = solve_pairs(active)
        if res is not None:
            _, pair_tuples = res
            pairings = []
            for m_idx, (tA, tB) in enumerate(pair_tuples, start=1):
                pairings.append({
                    "match_id": f"SW-R{round_num}-M{m_idx}",
                    "home": tA,
                    "away": tB
                })
            return {
                "format": "swiss",
                "round": round_num,
                "total_rounds": total_rounds,
                "pairings": pairings,
                "bye": bye_t,
                "standings": standings
            }

    raise NoValidPairingException(round_num)


# --- RECURSIVE SEED ORDER HELPER ---
def get_seed_order(bracket_size: int) -> List[int]:
    order = [1, 2]
    k = 2
    while k < bracket_size:
        next_order = []
        target = 2 * k + 1
        for s in order:
            next_order.extend([s, target - s])
        order = next_order
        k *= 2
    return order


# --- SINGLE ELIMINATION ---
def generate_single_elim(teams: List[str], params: Dict[str, Any] = None) -> Dict[str, Any]:
    params = params or {}
    n = len(teams)
    if n < 2 or n > 128:
        raise ValueError("Team count must be between 2 and 128")
    if len(set(teams)) != n:
        raise ValueError("Teams must be unique")

    third_place = params.get("third_place_match", False)
    results = params.get("results", [])

    # Bracket size
    B = 1
    while B < n:
        B *= 2

    rounds_count = int(math.log2(B))
    seed_order = get_seed_order(B)

    # Initialize all matches in tournament structure
    all_rounds = []
    matches_by_id = {}

    for r in range(1, rounds_count + 1):
        num_matches = B // (2 ** r)
        round_matches = []
        for m in range(1, num_matches + 1):
            match_id = f"R{r}M{m}"
            match_obj = {
                "match_id": match_id,
                "bracket": "main",
                "round": r,
                "index": m,
                "home": None,
                "away": None,
                "home_seed": None,
                "away_seed": None,
                "bye": False,
                "winner": None,
                "loser": None,
                "winner_to": None,
                "loser_to": None
            }
            if r < rounds_count:
                next_m = math.ceil(m / 2)
                slot = "home" if m % 2 != 0 else "away"
                match_obj["winner_to"] = {"match_id": f"R{r+1}M{next_m}", "slot": slot}

            matches_by_id[match_id] = match_obj
            round_matches.append(match_obj)
        all_rounds.append({"round": r, "matches": round_matches})

    # Third place match setup
    tp_match = None
    if third_place and rounds_count >= 2:
        tp_match = {
            "match_id": "TP",
            "bracket": "third_place",
            "round": rounds_count,
            "index": 2,
            "home": None,
            "away": None,
            "home_seed": None,
            "away_seed": None,
            "bye": False,
            "winner": None,
            "loser": None,
            "winner_to": None,
            "loser_to": None
        }
        matches_by_id["TP"] = tp_match
        # Semi-finals losers point to TP
        semi_r = rounds_count - 1
        matches_by_id[f"R{semi_r}M1"]["loser_to"] = {"match_id": "TP", "slot": "home"}
        matches_by_id[f"R{semi_r}M2"]["loser_to"] = {"match_id": "TP", "slot": "away"}

    # Seed Round 1
    r1_matches = all_rounds[0]["matches"]
    for i, m_obj in enumerate(r1_matches):
        s1 = seed_order[2 * i]
        s2 = seed_order[2 * i + 1]
        home_seed = min(s1, s2)
        away_seed = max(s1, s2)
        m_obj["home_seed"] = home_seed
        m_obj["away_seed"] = away_seed

        t_home = teams[home_seed - 1] if home_seed <= n else None
        t_away = teams[away_seed - 1] if away_seed <= n else None

        m_obj["home"] = t_home
        m_obj["away"] = t_away

        if t_away is None:
            m_obj["bye"] = True
            m_obj["winner"] = t_home
            # Immediately advance bye winner
            if m_obj["winner_to"]:
                target_m = matches_by_id[m_obj["winner_to"]["match_id"]]
                target_m[m_obj["winner_to"]["slot"]] = t_home
                target_m[f"{m_obj['winner_to']['slot']}_seed"] = home_seed

    # Apply provided results
    for res in results:
        mid = res.get("match_id")
        win = res.get("winner")
        if mid not in matches_by_id:
            raise ValueError(f"Unknown match_id: {mid}")
        m = matches_by_id[mid]
        if win not in (m["home"], m["away"]):
            raise ValueError(f"Winner {win} not participant in {mid}")
        m["winner"] = win
        m["loser"] = m["away"] if win == m["home"] else m["home"]

        # Propagate winner
        if m["winner_to"]:
            target_m = matches_by_id[m["winner_to"]["match_id"]]
            target_m[m["winner_to"]["slot"]] = win
            w_seed = m["home_seed"] if win == m["home"] else m["away_seed"]
            target_m[f"{m['winner_to']['slot']}_seed"] = w_seed
        # Propagate loser
        if m["loser_to"]:
            target_l = matches_by_id[m["loser_to"]["match_id"]]
            target_l[m["loser_to"]["slot"]] = m["loser"]
            l_seed = m["away_seed"] if win == m["home"] else m["home_seed"]
            target_l[f"{m['loser_to']['slot']}_seed"] = l_seed

    if tp_match:
        all_rounds[-1]["matches"].append(tp_match)

    return {
        "format": "single_elim",
        "bracket_size": B,
        "rounds": all_rounds
    }


# --- DOUBLE ELIMINATION ---
def generate_double_elim(teams: List[str], params: Dict[str, Any] = None) -> Dict[str, Any]:
    params = params or {}
    n = len(teams)
    if n < 3 or n > 128:
        raise ValueError("Team count must be between 3 and 128")
    if len(set(teams)) != n:
        raise ValueError("Teams must be unique")

    bracket_reset = params.get("bracket_reset", True)
    results = params.get("results", [])

    B = 1
    while B < n:
        B *= 2
    k = int(math.log2(B))
    seed_order = get_seed_order(B)

    matches_by_id: Dict[str, Any] = {}
    winners_rounds = []
    losers_rounds = []

    # Winners Bracket WR{r}M{i}
    for r in range(1, k + 1):
        num_m = B // (2 ** r)
        r_matches = []
        for i in range(1, num_m + 1):
            mid = f"WR{r}M{i}"
            m = {
                "match_id": mid,
                "bracket": "winners",
                "round": r,
                "index": i,
                "home": None,
                "away": None,
                "home_seed": None,
                "away_seed": None,
                "bye": False,
                "winner": None,
                "loser": None,
                "winner_to": None,
                "loser_to": None
            }
            if r < k:
                next_m = math.ceil(i / 2)
                slot = "home" if i % 2 != 0 else "away"
                m["winner_to"] = {"match_id": f"WR{r+1}M{next_m}", "slot": slot}
            matches_by_id[mid] = m
            r_matches.append(m)
        winners_rounds.append({"round": r, "matches": r_matches})

    # Losers Bracket LR{r}M{i}
    total_lr_rounds = 2 * (k - 1)
    for r in range(1, total_lr_rounds + 1):
        j = math.ceil(r / 2)
        num_m = B // (2 ** (j + 1))
        r_matches = []
        for i in range(1, num_m + 1):
            mid = f"LR{r}M{i}"
            m = {
                "match_id": mid,
                "bracket": "losers",
                "round": r,
                "index": i,
                "home": None,
                "away": None,
                "home_seed": None,
                "away_seed": None,
                "bye": False,
                "winner": None,
                "loser": None,
                "winner_to": None,
                "loser_to": None
            }
            if r < total_lr_rounds:
                # Losers bracket advancement
                if r % 2 != 0:
                    # odd round -> feeds next even round same index
                    m["winner_to"] = {"match_id": f"LR{r+1}M{i}", "slot": "home"}
                else:
                    # even round -> drops down to next odd round ceil(i/2)
                    next_i = math.ceil(i / 2)
                    slot = "home" if i % 2 != 0 else "away"
                    m["winner_to"] = {"match_id": f"LR{r+1}M{next_i}", "slot": slot}

            matches_by_id[mid] = m
            r_matches.append(m)
        losers_rounds.append({"round": r, "matches": r_matches})

    # Routing from Winners to Losers
    # WR1 losers meet in LR1
    for i in range(1, len(winners_rounds[0]["matches"]) + 1):
        target_lr_m = math.ceil(i / 2)
        slot = "home" if i % 2 != 0 else "away"
        matches_by_id[f"WR1M{i}"]["loser_to"] = {"match_id": f"LR1M{target_lr_m}", "slot": slot}

    # WRr (r >= 2) losers drop into LR(2r-2)
    for r in range(2, k):
        target_lr_r = 2 * r - 2
        for i in range(1, len(winners_rounds[r-1]["matches"]) + 1):
            slot = "away"
            matches_by_id[f"WR{r}M{i}"]["loser_to"] = {"match_id": f"LR{target_lr_r}M{i}", "slot": slot}

    # WR final (WRkM1) loser drops to LB final (LR{total_lr_rounds}M1.away)
    matches_by_id[f"WR{k}M1"]["loser_to"] = {"match_id": f"LR{total_lr_rounds}M1", "slot": "away"}

    # Grand Final GF1 & GF2
    gf1 = {
        "match_id": "GF1",
        "bracket": "grand_final",
        "round": 1,
        "index": 1,
        "home": None,
        "away": None,
        "home_seed": None,
        "away_seed": None,
        "bye": False,
        "winner": None,
        "loser": None,
        "winner_to": None,
        "loser_to": None
    }
    matches_by_id["GF1"] = gf1
    matches_by_id[f"WR{k}M1"]["winner_to"] = {"match_id": "GF1", "slot": "home"}
    matches_by_id[f"LR{total_lr_rounds}M1"]["winner_to"] = {"match_id": "GF1", "slot": "away"}

    grand_final_list = [gf1]
    gf2 = None
    if bracket_reset:
        gf2 = {
            "match_id": "GF2",
            "bracket": "grand_final",
            "round": 2,
            "index": 1,
            "home": None,
            "away": None,
            "home_seed": None,
            "away_seed": None,
            "bye": False,
            "winner": None,
            "loser": None,
            "winner_to": None,
            "loser_to": None,
            "conditional": True,
            "required": None
        }
        matches_by_id["GF2"] = gf2
        grand_final_list.append(gf2)

    # Seed WR1
    for i, m_obj in enumerate(winners_rounds[0]["matches"]):
        s1 = seed_order[2 * i]
        s2 = seed_order[2 * i + 1]
        home_seed = min(s1, s2)
        away_seed = max(s1, s2)
        m_obj["home_seed"] = home_seed
        m_obj["away_seed"] = away_seed

        t_home = teams[home_seed - 1] if home_seed <= n else None
        t_away = teams[away_seed - 1] if away_seed <= n else None
        m_obj["home"] = t_home
        m_obj["away"] = t_away

        if t_away is None:
            m_obj["bye"] = True
            m_obj["winner"] = t_home
            if m_obj["winner_to"]:
                target_m = matches_by_id[m_obj["winner_to"]["match_id"]]
                target_m[m_obj["winner_to"]["slot"]] = t_home

    # Apply results
    for res in results:
        mid = res.get("match_id")
        win = res.get("winner")
        if mid not in matches_by_id:
            raise ValueError(f"Unknown match_id: {mid}")
        m = matches_by_id[mid]
        if win not in (m["home"], m["away"]):
            raise ValueError(f"Winner {win} not participant in {mid}")
        m["winner"] = win
        m["loser"] = m["away"] if win == m["home"] else m["home"]

        if m["winner_to"]:
            target_m = matches_by_id[m["winner_to"]["match_id"]]
            target_m[m["winner_to"]["slot"]] = win
        if m["loser_to"]:
            target_l = matches_by_id[m["loser_to"]["match_id"]]
            target_l[m["loser_to"]["slot"]] = m["loser"]

        # Check GF1 result for GF2 reset
        if mid == "GF1" and gf2:
            gf2["home"] = m["home"]
            gf2["away"] = m["away"]
            if win == m["away"]:
                gf2["required"] = True
            else:
                gf2["required"] = False

    return {
        "format": "double_elim",
        "bracket_size": B,
        "bracket_reset": bracket_reset,
        "winners": winners_rounds,
        "losers": losers_rounds,
        "grand_final": grand_final_list
    }
