"""Advanced statistics: per-match counting components, season sums, and the rates computed from them.

The flow is the same for one match and for a season:

    comps = match_components(report_datasets, pbp_rows)   # counts only, one dict per team / player / ...
    summed = aggregate([comps, comps2, ...])              # add counts up (never average rates)
    rows = team_table(summed["teams"], "Advanced", "Per50Possessions")

Ratings are per 50 possessions (the EHF convention: about one team's possessions in a match), "/60"
values per 60 minutes (on-court minutes for players, game time for teams), and every *_PCT is 0-100.
Rates that divide by possessions only use counts from matches with a play-by-play log, so a season with a
few missing logs still gives unbiased ratings.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Callable, Iterable

from mksz_api.library.events import BOX_COLUMNS, OTHER_SIDE, SHOT_EVENTS
from mksz_api.library.oncourt import Lineups, build_lineups, game_end_seconds, situation
from mksz_api.library.possessions import clutch_windows, is_clutch, possession_log
from mksz_api.library.zones import SEVEN_METRE_ZONE, ZONE_GROUPS, court_zone, zone_group

PER_MODES = ("Totals", "PerGame", "Per60", "Per50Possessions")
POSS_SCALE = 50
SITUATIONS = ("EVEN", "ADVANTAGE", "DISADVANTAGE", "EMPTY_NET")
GROUPS = list(ZONE_GROUPS)
FIELD_GROUPS = [g for g in GROUPS if g != "SEVEN_M"]

# official report counting columns (BoxScore.PlayerStats / TeamStats)
BOX = [
    "GOALS", "SHOTS", "GOALS_7M", "SHOTS_7M", "SHOTS_SAVED", "SHOTS_POST", "SHOTS_MISSED", "SAVES",
    "SHOTS_FACED", "TURNOVERS", "TECHNICAL_FAULTS", "YELLOW_CARDS", "TWO_MINUTES", "RED_CARDS", "BLUE_CARDS",
]
MAX_COLUMNS = {"LARGEST_LEAD", "LONGEST_RUN"}  # season value = best match, not a sum
ID_COLUMNS = {"TEAM_ID", "PLAYER_ID", "OPPONENT_TEAM_ID", "JERSEY"}  # identifiers: kept, never added up


def pct(made, attempts) -> float | None:
    if not attempts:
        return None
    return round(100.0 * (made or 0) / attempts, 1)


def per(made, basis, scale) -> float | None:
    if not basis:
        return None
    return round(scale * (made or 0) / basis, 1)


def _sub(a, b):
    return None if a is None or b is None else a - b


# -- play-by-play box score ----------------------------------------------------------------------------------

def pbp_box(rows: list[dict], include: Callable[[dict], bool] | None = None):
    """The official box score rebuilt from play-by-play rows: ({side: counts}, {player_id: counts}).

    Counts use the report's column names (plus BLOCKED, which the report leaves out). For NB I matches it
    equals the report exactly; used for clutch-time stats, which the report cannot give."""
    teams: dict[str, defaultdict] = {s: defaultdict(int) for s in ("HOME", "AWAY")}
    players: dict[Any, defaultdict] = defaultdict(lambda: defaultdict(int))
    for r in rows:
        side = r["HOME_AWAY"]
        if side not in teams or (include is not None and not include(r)):
            continue
        et = r["EVENT_TYPE"]
        for col in BOX_COLUMNS.get(et, ()):
            teams[side][col] += 1
            if r["PLAYER_ID"] is not None and not r["IS_STAFF"]:
                players[r["PLAYER_ID"]][col] += 1
        if et in SHOT_EVENTS and SHOT_EVENTS[et][0] in ("GOAL", "SAVED"):
            saved = SHOT_EVENTS[et][0] == "SAVED"
            opp = OTHER_SIDE[side]
            teams[opp]["SHOTS_FACED"] += 1
            teams[opp]["SAVES"] += saved
            gk = r["OPP_GOALKEEPER_ID"]
            if gk:
                players[gk]["SHOTS_FACED"] += 1
                players[gk]["SAVES"] += saved
    return teams, players


# -- game flow -----------------------------------------------------------------------------------------------

def game_flow(rows: list[dict], game_end: int) -> dict[str, dict]:
    """Lead changes, ties, largest lead, time leading/trailing/tied and longest scoring run, per side."""
    goals = [(r["ELAPSED_SEC"] or 0, r["HOME_AWAY"]) for r in rows
             if r["EVENT_TYPE"] in ("GOAL", "GOAL_7M") and r["HOME_AWAY"] in ("HOME", "AWAY")]
    margin, leader, lead_changes, ties = 0, None, 0, 0
    largest = {"HOME": 0, "AWAY": 0}
    time = {"HOME": 0, "AWAY": 0, "TIED": 0}
    runs = {"HOME": 0, "AWAY": 0}
    run_side, run = None, 0
    last_t = 0
    for t, side in goals + [(game_end, None)]:
        holder = "TIED" if margin == 0 else ("HOME" if margin > 0 else "AWAY")
        time[holder] += max(0, t - last_t)
        last_t = max(last_t, t)
        if side is None:
            break
        margin += 1 if side == "HOME" else -1
        largest["HOME"], largest["AWAY"] = max(largest["HOME"], margin), max(largest["AWAY"], -margin)
        if margin == 0:
            ties += 1
        else:
            new_leader = "HOME" if margin > 0 else "AWAY"
            if leader is not None and new_leader != leader:
                lead_changes += 1
            leader = new_leader
        run = run + 1 if side == run_side else 1
        run_side = side
        runs[side] = max(runs[side], run)
    out = {}
    for side in ("HOME", "AWAY"):
        opp = OTHER_SIDE[side]
        out[side] = {
            "LEAD_CHANGES": lead_changes, "TIMES_TIED": ties, "LARGEST_LEAD": largest[side],
            "TIME_LEADING_SEC": time[side], "TIME_TRAILING_SEC": time[opp], "TIME_TIED_SEC": time["TIED"],
            "LONGEST_RUN": runs[side],
        }
    return out


# -- per-match components ------------------------------------------------------------------------------------

def _clip(intervals, windows):
    """Yield (start, end, state) pieces of `intervals` that fall inside `windows` (all if windows is None)."""
    for a, b, state in intervals:
        if windows is None:
            yield a, b, state
            continue
        for wa, wb in windows:
            lo, hi = max(a, wa), min(b, wb)
            if hi > lo:
                yield lo, hi, state


def match_components(report: dict[str, list[dict]], rows: list[dict] | None = None, clutch: bool = False) -> dict:
    """Counting components of one match.

    report: BoxScore datasets as records ({"MatchInfo": [...], "PlayerStats": [...], "TeamStats": [...]}).
    rows: PlayByPlay rows, or None when the match has no livescore log.
    clutch: count only clutch time (needs rows; box counts then come from the play-by-play)."""
    info = report["MatchInfo"][0]
    match_id = str(info["MATCH_ID"])
    report_teams = {t["HOME_AWAY"]: t for t in report["TeamStats"]}
    report_players = report["PlayerStats"]
    has_pbp = bool(rows)
    if clutch and not has_pbp:
        return {"MATCH_ID": match_id, "HAS_PBP": False, "HAS_LINEUPS": False, "teams": [], "players": [],
                "goalkeepers": [], "situations": [], "possessions": []}

    goalkeepers = {p["PLAYER_ID"] for p in report_players if p["GK"]}
    lineups: Lineups | None = None
    possessions: list[dict] = []
    windows = None
    game_end = 3600
    if has_pbp:
        game_end = game_end_seconds(rows)
        lineups = build_lineups(rows, goalkeepers, game_end)
        possessions = possession_log(rows, lineups)
        if clutch:
            windows = clutch_windows(rows, game_end)
    has_lineups = bool(lineups and lineups.has_lineups)
    margin_before: dict[int, int] = {}  # EVENT_NUM -> home margin before the event
    if has_pbp:
        prev = 0
        for r in rows:
            margin_before[r["EVENT_NUM"]] = prev
            prev = r["HOME_SCORE"] - r["AWAY_SCORE"]

    def in_clutch(r: dict) -> bool:
        m = margin_before.get(r["EVENT_NUM"], 0)
        return is_clutch(r["ELAPSED_SEC"], m)

    event_filter = in_clutch if clutch else None
    poss_used = [p for p in possessions if p["IS_CLUTCH"]] if clutch else possessions
    game_seconds = sum(b - a for a, b in windows) if clutch else game_end
    if clutch:
        team_box, player_box = pbp_box(rows, event_filter)
    else:
        team_box = {side: {c: report_teams[side].get(c) or 0 for c in BOX} for side in ("HOME", "AWAY")}
        player_box = {p["PLAYER_ID"]: {c: p.get(c) or 0 for c in BOX} for p in report_players}
    pbp_teams, pbp_players = pbp_box(rows, event_filter) if has_pbp else ({}, {})
    flow = game_flow(rows, game_end) if has_pbp else {}

    # shots by zone group, from the play-by-play
    shots = []
    for r in rows or []:
        if r["EVENT_TYPE"] in SHOT_EVENTS and r["HOME_AWAY"] in ("HOME", "AWAY") and (event_filter is None or event_filter(r)):
            outcome, is_7m = SHOT_EVENTS[r["EVENT_TYPE"]]
            zone = SEVEN_METRE_ZONE if is_7m else court_zone(r["SHOT_AREA"])
            shots.append({"side": r["HOME_AWAY"], "pid": r["PLAYER_ID"], "gk": r["OPP_GOALKEEPER_ID"],
                          "outcome": outcome, "is_7m": is_7m, "group": zone_group(zone), "num": r["EVENT_NUM"],
                          "clutch": in_clutch(r)})

    teams_out, situations_out = [], []
    for side in ("HOME", "AWAY"):
        opp = OTHER_SIDE[side]
        own, other = report_teams[side], report_teams[opp]
        score, opp_score = info[f"{side}_SCORE"], info[f"{opp}_SCORE"]
        t = {
            "MATCH_ID": match_id, "TEAM_ID": own["TEAM_ID"], "TEAM_NAME": own["TEAM_NAME"], "HOME_AWAY": side,
            "OPPONENT_TEAM_ID": other["TEAM_ID"], "OPPONENT_TEAM_NAME": other["TEAM_NAME"],
            "GP": 1, "W": int(score > opp_score), "D": int(score == opp_score), "L": int(score < opp_score),
            "PTS": info.get(f"{side}_POINTS"), "SECONDS": game_seconds,
            "PBP_GAMES": int(has_pbp), "LINEUP_GAMES": int(has_lineups),
            "PBP_SECONDS": game_seconds if has_pbp else 0,
            "TIMEOUTS": own.get("TIMEOUTS") if not clutch and own.get("TIMEOUTS") is not None else (
                sum(1 for r in rows if r["EVENT_TYPE"] == "TIMEOUT" and r["HOME_AWAY"] == side
                    and (event_filter is None or event_filter(r))) if has_pbp else None),
        }
        for c in BOX:
            t[c] = team_box[side].get(c, 0)
            t[f"OPP_{c}"] = team_box[opp].get(c, 0)
        if has_pbp:
            off = [p for p in poss_used if p["OFFENSE"] == side]
            dfn = [p for p in poss_used if p["OFFENSE"] == opp]
            pt, po = pbp_teams[side], pbp_teams[opp]
            own_shots = [s for s in shots if s["side"] == side]
            opp_shots = [s for s in shots if s["side"] == opp]
            t.update({
                "POSS": len(off), "OPP_POSS": len(dfn), "POSS_SECONDS": sum(p["DURATION"] for p in off),
                "POSS_GOALS": sum(p["GOALS"] for p in off), "OPP_POSS_GOALS": sum(p["GOALS"] for p in dfn),
                "ATTEMPTS": len(own_shots), "OPP_ATTEMPTS": len(opp_shots),
                "BLOCKED": pt["BLOCKED"], "OPP_BLOCKED": po["BLOCKED"],
                "RETAINED": sum(p["RETAINED"] for p in off), "OPP_RETAINED": sum(p["RETAINED"] for p in dfn),
                "RETAIN_CHANCES": sum(p["ATTEMPTS"] - p["GOALS"] for p in off),
                "OPP_RETAIN_CHANCES": sum(p["ATTEMPTS"] - p["GOALS"] for p in dfn),
                "PBP_TURNOVERS": pt["TURNOVERS"], "PBP_TECHNICAL_FAULTS": pt["TECHNICAL_FAULTS"],
                "OPP_PBP_TURNOVERS": po["TURNOVERS"], "OPP_PBP_TECHNICAL_FAULTS": po["TECHNICAL_FAULTS"],
                "PBP_SHOTS_7M": pt["SHOTS_7M"], "OPP_PBP_SHOTS_7M": po["SHOTS_7M"],
                "PBP_SAVES": pt["SAVES"], "PBP_SHOTS_FACED": pt["SHOTS_FACED"],
                "OPP_PBP_SAVES": po["SAVES"], "OPP_PBP_SHOTS_FACED": po["SHOTS_FACED"],
                "PBP_TWO_MINUTES": pt["TWO_MINUTES"], "OPP_PBP_TWO_MINUTES": po["TWO_MINUTES"],
                "CLUTCH_POSS": sum(p["IS_CLUTCH"] for p in off), "CLUTCH_GOALS": sum(p["GOALS"] for p in off if p["IS_CLUTCH"]),
                "CLUTCH_OPP_POSS": sum(p["IS_CLUTCH"] for p in dfn),
                "CLUTCH_OPP_GOALS": sum(p["GOALS"] for p in dfn if p["IS_CLUTCH"]),
                "POST_TIMEOUT_POSS": sum(p["AFTER_TIMEOUT"] for p in off),
                "POST_TIMEOUT_GOALS": sum(p["GOALS"] for p in off if p["AFTER_TIMEOUT"]),
                **flow[side],
            })
            for g in GROUPS:
                t[f"{g}_ATT"] = sum(1 for s in own_shots if s["group"] == g)
                t[f"{g}_GOALS"] = sum(1 for s in own_shots if s["group"] == g and s["outcome"] == "GOAL")
                t[f"OPP_{g}_ATT"] = sum(1 for s in opp_shots if s["group"] == g)
                t[f"OPP_{g}_GOALS"] = sum(1 for s in opp_shots if s["group"] == g and s["outcome"] == "GOAL")

            # numerical situations, from this team's point of view
            for sit in SITUATIONS:
                if sit == "EMPTY_NET":
                    secs = sum(b - a for a, b, st in _clip(lineups.intervals, windows) if not st.team(side).goalkeeper_on)
                    o = [p for p in off if p["OFF_EMPTY_NET"]]
                    d = [p for p in dfn if p["DEF_EMPTY_NET"]]
                else:
                    secs = sum(b - a for a, b, st in _clip(lineups.intervals, windows) if situation(st, side) == sit)
                    o = [p for p in off if p["SITUATION"] == sit]
                    mirrored = {"EVEN": "EVEN", "ADVANTAGE": "DISADVANTAGE", "DISADVANTAGE": "ADVANTAGE"}[sit]
                    d = [p for p in dfn if p["SITUATION"] == mirrored]
                situations_out.append({
                    "MATCH_ID": match_id, "TEAM_ID": own["TEAM_ID"], "TEAM_NAME": own["TEAM_NAME"],
                    "SITUATION": sit, "GP": 1, "SECONDS": secs, "POSS": len(o), "GOALS": sum(p["GOALS"] for p in o),
                    "OPP_POSS": len(d), "OPP_GOALS": sum(p["GOALS"] for p in d),
                    "STRENGTH_SOURCE": lineups.strength_source,
                })
        teams_out.append(t)

    # players
    team_poss = {side: [p for p in poss_used if p["OFFENSE"] == side] for side in ("HOME", "AWAY")}
    has_minutes = any(p.get("SECONDS_PLAYED") for p in report_players)
    players_out, gks_out = [], []
    for rp in report_players:
        pid, side = rp["PLAYER_ID"], rp["HOME_AWAY"]
        opp = OTHER_SIDE[side]
        box = player_box.get(pid, {})
        if clutch:
            seconds = lineups.overlap_seconds(pid, windows) if has_lineups else 0
        else:
            seconds = rp.get("SECONDS_PLAYED") or 0
        p = {
            "MATCH_ID": match_id, "PLAYER_ID": pid, "PLAYER_NAME": rp["PLAYER_NAME"], "JERSEY": rp["JERSEY"],
            "TEAM_ID": rp["TEAM_ID"], "TEAM_NAME": rp["TEAM_NAME"], "HOME_AWAY": side, "GK": rp["GK"],
            "SECONDS": seconds, **{c: box.get(c, 0) for c in BOX},
            "PBP_GAMES": int(has_pbp), "LINEUP_GAMES": int(has_lineups),
        }
        p["GP"] = int(bool(seconds) or any(p[c] for c in BOX) or (not clutch and not has_minutes))
        if has_pbp:
            mine = [s for s in shots if s["pid"] == pid and s["side"] == side]
            team_shots = [s for s in shots if s["side"] == side]
            pp = pbp_players.get(pid, {})
            p.update({
                "ATTEMPTS": len(mine), "BLOCKED": sum(1 for s in mine if s["outcome"] == "BLOCKED"),
                "PLAYS": len(mine) + pp.get("TURNOVERS", 0) + pp.get("TECHNICAL_FAULTS", 0),
                "TEAM_GOALS": sum(1 for s in team_shots if s["outcome"] == "GOAL"), "TEAM_ATTEMPTS": len(team_shots),
                "CLUTCH_GOALS": sum(1 for s in mine if s["outcome"] == "GOAL" and s["clutch"]),
            })
            for g in GROUPS:
                p[f"{g}_ATT"] = sum(1 for s in mine if s["group"] == g)
                p[f"{g}_GOALS"] = sum(1 for s in mine if s["group"] == g and s["outcome"] == "GOAL")
            if has_lineups:
                _player_lineup_stats(p, pid, side, opp, rows, lineups, team_poss, event_filter)
        players_out.append(p)

        faced = [s for s in shots if s["gk"] == pid and s["side"] == opp and s["outcome"] in ("GOAL", "SAVED")]
        if rp["GK"] or faced or box.get("SHOTS_FACED"):
            gk = {k: p[k] for k in ("MATCH_ID", "PLAYER_ID", "PLAYER_NAME", "JERSEY", "TEAM_ID", "TEAM_NAME",
                                    "HOME_AWAY", "GK", "SECONDS", "PBP_GAMES", "LINEUP_GAMES")}
            gk.update({"GP": int(bool(seconds) or bool(box.get("SHOTS_FACED")) or (not clutch and not has_minutes)),
                       "SAVES": box.get("SAVES", 0), "SHOTS_FACED": box.get("SHOTS_FACED", 0)})
            if has_pbp:
                gk["SAVES_7M"] = sum(1 for s in faced if s["is_7m"] and s["outcome"] == "SAVED")
                gk["SHOTS_FACED_7M"] = sum(1 for s in faced if s["is_7m"])
                for g in FIELD_GROUPS:
                    gk[f"{g}_FACED"] = sum(1 for s in faced if s["group"] == g)
                    gk[f"{g}_SAVES"] = sum(1 for s in faced if s["group"] == g and s["outcome"] == "SAVED")
            if has_lineups:
                dfn = [x for x in team_poss[opp]
                       if (st := lineups.at(x["END_EVENT_NUM"])) is not None and pid in st.team(side).on]
                gk["DEF_POSS_ON"] = len(dfn)
                gk["DEF_GOALS_ON"] = sum(x["GOALS"] for x in dfn)
            gks_out.append(gk)

    return {
        "MATCH_ID": match_id, "HAS_PBP": has_pbp, "HAS_LINEUPS": has_lineups,
        "LINEUP_ERROR_PCT": round(100 * lineups.lineup_error, 1) if lineups and lineups.lineup_error is not None else None,
        "LINEUPS_REJECTED": bool(lineups and lineups.lineups_rejected),
        "teams": teams_out, "players": players_out, "goalkeepers": gks_out, "situations": situations_out,
        "possessions": possessions,
    }


def _player_lineup_stats(p, pid, side, opp, rows, lineups, team_poss, event_filter) -> None:
    """On-court possessions, on/off goals, plus-minus, usage denominators and situational goals."""
    def on(event_num: int, s: str = side) -> bool:
        state = lineups.at(event_num)
        return state is not None and pid in state.team(s).on

    off_on = [x for x in team_poss[side] if on(x["END_EVENT_NUM"])]
    def_on = [x for x in team_poss[opp] if on(x["END_EVENT_NUM"])]
    p["OFF_POSS_ON"], p["DEF_POSS_ON"] = len(off_on), len(def_on)
    p["OFF_GOALS_ON"], p["DEF_GOALS_ON"] = sum(x["GOALS"] for x in off_on), sum(x["GOALS"] for x in def_on)
    p["TEAM_OFF_POSS"], p["TEAM_DEF_POSS"] = len(team_poss[side]), len(team_poss[opp])
    p["TEAM_OFF_GOALS"] = sum(x["GOALS"] for x in team_poss[side])
    p["TEAM_DEF_GOALS"] = sum(x["GOALS"] for x in team_poss[opp])
    plus_minus, team_plays_on, adv, dis, empty = 0, 0, 0, 0, 0
    for r in rows:
        et = r["EVENT_TYPE"]
        if r["HOME_AWAY"] not in ("HOME", "AWAY") or (event_filter is not None and not event_filter(r)):
            continue
        if not on(r["EVENT_NUM"]):
            continue
        if et in ("GOAL", "GOAL_7M"):
            plus_minus += 1 if r["HOME_AWAY"] == side else -1
        if r["HOME_AWAY"] == side and not r["IS_STAFF"] and (et in SHOT_EVENTS or et in ("TURNOVER", "TECHNICAL_FAULT")):
            team_plays_on += 1
        if r["HOME_AWAY"] == side and r["PLAYER_ID"] == pid and et in ("GOAL", "GOAL_7M"):
            state = lineups.at(r["EVENT_NUM"])
            sit = situation(state, side)
            adv += sit == "ADVANTAGE"
            dis += sit == "DISADVANTAGE"
            empty += not state.team(side).goalkeeper_on
    p.update({"PLUS_MINUS": plus_minus, "TEAM_PLAYS_ON": team_plays_on, "ADVANTAGE_GOALS": adv,
              "DISADVANTAGE_GOALS": dis, "EMPTY_NET_GOALS": empty})


# -- aggregation ---------------------------------------------------------------------------------------------

def aggregate(components: Iterable[dict]) -> dict[str, list[dict]]:
    """Sum the components of many matches: teams by TEAM_ID, players and goalkeepers by (PLAYER_ID, TEAM_ID),
    situations by (TEAM_ID, SITUATION). Names are kept from the latest match."""
    keys = {
        "teams": lambda r: r["TEAM_ID"],
        "players": lambda r: (r["PLAYER_ID"], r["TEAM_ID"]),
        "goalkeepers": lambda r: (r["PLAYER_ID"], r["TEAM_ID"]),
        "situations": lambda r: (r["TEAM_ID"], r["SITUATION"]),
    }
    out: dict[str, dict] = {k: {} for k in keys}
    for comp in components:
        for kind, key in keys.items():
            for row in comp.get(kind, []):
                k = key(row)
                acc = out[kind].setdefault(k, {})
                for col, v in row.items():
                    if col == "MATCH_ID":
                        continue
                    if col in ID_COLUMNS:
                        acc[col] = v if v is not None else acc.get(col)
                    elif isinstance(v, bool) or not isinstance(v, (int, float)):
                        if v is not None:
                            acc[col] = v if not isinstance(v, bool) else (acc.get(col) or v)
                        else:
                            acc.setdefault(col, None)
                    elif col in MAX_COLUMNS:
                        acc[col] = max(acc.get(col) or 0, v)
                    else:
                        acc[col] = (acc.get(col) or 0) + v
    return {kind: list(rows.values()) for kind, rows in out.items()}


# -- rates ---------------------------------------------------------------------------------------------------

def team_rates(c: dict) -> dict:
    g = lambda k: c.get(k)  # noqa: E731
    r = {
        "MIN": round((g("SECONDS") or 0) / 60, 1),
        "GOAL_DIFF": (g("GOALS") or 0) - (g("OPP_GOALS") or 0),
        "SHOT_PCT": pct(g("GOALS"), g("SHOTS")),
        "SEVEN_M_PCT": pct(g("GOALS_7M"), g("SHOTS_7M")),
        "NON7M_PCT": pct(_sub(g("GOALS"), g("GOALS_7M")), _sub(g("SHOTS"), g("SHOTS_7M"))),
        "SAVE_PCT": pct(g("SAVES"), g("SHOTS_FACED")),
        "OPP_SHOT_PCT": pct(g("OPP_GOALS"), g("OPP_SHOTS")),
        "SUSPENSIONS": g("TWO_MINUTES"),
        "SUSPENSIONS_DRAWN": g("OPP_TWO_MINUTES"),
    }
    poss, opp_poss = g("POSS"), g("OPP_POSS")
    pbp_seconds = g("PBP_SECONDS")
    r.update({
        "PACE": per(((poss or 0) + (opp_poss or 0)) / 2, pbp_seconds, 3600) if poss else None,
        "AVG_POSS_SEC": round(g("POSS_SECONDS") / poss, 1) if poss else None,
        "ORTG": per(g("POSS_GOALS"), poss, POSS_SCALE),
        "DRTG": per(g("OPP_POSS_GOALS"), opp_poss, POSS_SCALE),
        "ATTACK_EFF_PCT": pct(g("POSS_GOALS"), poss),
        "ATT_GOAL_PCT": pct(g("POSS_GOALS"), g("ATTEMPTS")),
        "OPP_ATT_GOAL_PCT": pct(g("OPP_POSS_GOALS"), g("OPP_ATTEMPTS")),
        "TOV_PCT": pct((g("PBP_TURNOVERS") or 0) + (g("PBP_TECHNICAL_FAULTS") or 0), poss),
        "OPP_TOV_PCT": pct((g("OPP_PBP_TURNOVERS") or 0) + (g("OPP_PBP_TECHNICAL_FAULTS") or 0), opp_poss),
        "LOST_BALL_PCT": pct(g("PBP_TURNOVERS"), poss),
        "TECH_FAULT_PCT": pct(g("PBP_TECHNICAL_FAULTS"), poss),
        "RETAIN_PCT": pct(g("RETAINED"), g("RETAIN_CHANCES")),
        "OPP_RETAIN_PCT": pct(g("OPP_RETAINED"), g("OPP_RETAIN_CHANCES")),
        "SEVEN_M_RATE": pct(g("PBP_SHOTS_7M"), poss),
        "OPP_SEVEN_M_RATE": pct(g("OPP_PBP_SHOTS_7M"), opp_poss),
        "BLOCKED_PCT": pct(g("BLOCKED"), g("ATTEMPTS")),
        "OPP_BLOCKED_PCT": pct(g("OPP_BLOCKED"), g("OPP_ATTEMPTS")),
        "OPP_SAVE_PCT": pct(g("OPP_SAVES"), g("OPP_SHOTS_FACED")),
        "FAST_BREAK_GOALS": g("FAST_BREAK_GOALS"),
        "FAST_BREAK_GOALS_PER_50": per(g("FAST_BREAK_GOALS"), poss, POSS_SCALE),
        "CLUTCH_ORTG": per(g("CLUTCH_GOALS"), g("CLUTCH_POSS"), POSS_SCALE),
        "CLUTCH_DRTG": per(g("CLUTCH_OPP_GOALS"), g("CLUTCH_OPP_POSS"), POSS_SCALE),
        "POST_TIMEOUT_EFF_PCT": pct(g("POST_TIMEOUT_GOALS"), g("POST_TIMEOUT_POSS")),
        "TIME_LEADING_MIN": _min(g("TIME_LEADING_SEC")),
        "TIME_TRAILING_MIN": _min(g("TIME_TRAILING_SEC")),
        "TIME_TIED_MIN": _min(g("TIME_TIED_SEC")),
    })
    r["NET_RTG"] = _sub(r["ORTG"], r["DRTG"]) if r["ORTG"] is not None and r["DRTG"] is not None else None
    r["CLUTCH_NET"] = (round(r["CLUTCH_ORTG"] - r["CLUTCH_DRTG"], 1)
                       if r["CLUTCH_ORTG"] is not None and r["CLUTCH_DRTG"] is not None else None)
    if r["NET_RTG"] is not None:
        r["NET_RTG"] = round(r["NET_RTG"], 1)
    for grp in GROUPS:
        r[f"{grp}_PCT"] = pct(g(f"{grp}_GOALS"), g(f"{grp}_ATT"))
        r[f"{grp}_SHARE"] = pct(g(f"{grp}_ATT"), g("ATTEMPTS"))
    return r


def player_rates(c: dict) -> dict:
    g = lambda k: c.get(k)  # noqa: E731
    r = {
        "MIN": round((g("SECONDS") or 0) / 60, 1),
        "SHOT_PCT": pct(g("GOALS"), g("SHOTS")),
        "SEVEN_M_PCT": pct(g("GOALS_7M"), g("SHOTS_7M")),
        "NON7M_PCT": pct(_sub(g("GOALS"), g("GOALS_7M")), _sub(g("SHOTS"), g("SHOTS_7M"))),
        "ATT_GOAL_PCT": pct(_pbp_goals(c), g("ATTEMPTS")),
        "TOV_RATIO": pct((g("TURNOVERS") or 0) + (g("TECHNICAL_FAULTS") or 0),
                         (g("ATTEMPTS") or 0) + (g("TURNOVERS") or 0) + (g("TECHNICAL_FAULTS") or 0))
        if g("ATTEMPTS") is not None else None,
        "GOAL_SHARE": pct(_pbp_goals(c), g("TEAM_GOALS")),
        "SHOT_SHARE": pct(g("ATTEMPTS"), g("TEAM_ATTEMPTS")),
        "USG_PCT": pct(g("PLAYS"), g("TEAM_PLAYS_ON")) if g("TEAM_PLAYS_ON") is not None else None,
    }
    off_on, def_on = g("OFF_POSS_ON"), g("DEF_POSS_ON")
    if off_on is not None:
        r["POSS_ON"] = (off_on or 0) + (def_on or 0)
        r["ON_ORTG"] = per(g("OFF_GOALS_ON"), off_on, POSS_SCALE)
        r["ON_DRTG"] = per(g("DEF_GOALS_ON"), def_on, POSS_SCALE)
        r["ON_NET"] = _net(r["ON_ORTG"], r["ON_DRTG"])
        off_ortg = per(_sub(g("TEAM_OFF_GOALS"), g("OFF_GOALS_ON")), _sub(g("TEAM_OFF_POSS"), off_on), POSS_SCALE)
        off_drtg = per(_sub(g("TEAM_DEF_GOALS"), g("DEF_GOALS_ON")), _sub(g("TEAM_DEF_POSS"), def_on), POSS_SCALE)
        r["OFF_ORTG"], r["OFF_DRTG"] = off_ortg, off_drtg
        r["OFF_NET"] = _net(off_ortg, off_drtg)
        r["ON_OFF_NET"] = _net(r["ON_NET"], r["OFF_NET"])
    for grp in GROUPS:
        r[f"{grp}_PCT"] = pct(g(f"{grp}_GOALS"), g(f"{grp}_ATT"))
        r[f"{grp}_SHARE"] = pct(g(f"{grp}_ATT"), g("ATTEMPTS"))
    return r


def goalkeeper_rates(c: dict) -> dict:
    g = lambda k: c.get(k)  # noqa: E731
    r = {
        "MIN": round((g("SECONDS") or 0) / 60, 1),
        "SAVE_PCT": pct(g("SAVES"), g("SHOTS_FACED")),
        "SEVEN_M_SAVE_PCT": pct(g("SAVES_7M"), g("SHOTS_FACED_7M")),
        "SAVES_PER_60": per(g("SAVES"), g("SECONDS"), 3600),
        "ON_DRTG": per(g("DEF_GOALS_ON"), g("DEF_POSS_ON"), POSS_SCALE),
    }
    for grp in FIELD_GROUPS:
        r[f"{grp}_SAVE_PCT"] = pct(g(f"{grp}_SAVES"), g(f"{grp}_FACED"))
    return r


def situation_rates(c: dict) -> dict:
    ortg = per(c.get("GOALS"), c.get("POSS"), POSS_SCALE)
    drtg = per(c.get("OPP_GOALS"), c.get("OPP_POSS"), POSS_SCALE)
    return {"MIN": round((c.get("SECONDS") or 0) / 60, 1), "ORTG": ortg, "DRTG": drtg, "NET_RTG": _net(ortg, drtg),
            "GOAL_DIFF": (c.get("GOALS") or 0) - (c.get("OPP_GOALS") or 0)}


def _pbp_goals(c: dict):
    return sum(c.get(f"{g}_GOALS") or 0 for g in GROUPS) if c.get("ATTEMPTS") is not None else None


def _net(a, b):
    return round(a - b, 1) if a is not None and b is not None else None


def _min(seconds):
    return round(seconds / 60, 1) if seconds is not None else None


# -- tables: measure types and per-mode scaling --------------------------------------------------------------

TEAM_ID_COLUMNS = ["TEAM_ID", "TEAM_NAME", "GP", "W", "D", "L", "PTS", "MIN"]
PLAYER_ID_COLUMNS = ["PLAYER_ID", "PLAYER_NAME", "TEAM_ID", "TEAM_NAME", "GK", "GP", "MIN"]

# measure -> (columns, counting columns that per_mode scales)
TEAM_MEASURES: dict[str, tuple[list[str], set[str]]] = {
    "Base": ([
        "GOALS", "OPP_GOALS", "GOAL_DIFF", "SHOTS", "SHOT_PCT", "GOALS_7M", "SHOTS_7M", "SEVEN_M_PCT",
        "SHOTS_SAVED", "SHOTS_POST", "SHOTS_MISSED", "TURNOVERS", "TECHNICAL_FAULTS", "SAVES", "SHOTS_FACED",
        "SAVE_PCT", "YELLOW_CARDS", "TWO_MINUTES", "RED_CARDS", "BLUE_CARDS", "TIMEOUTS",
    ], {"GOALS", "OPP_GOALS", "GOAL_DIFF", "SHOTS", "GOALS_7M", "SHOTS_7M", "SHOTS_SAVED", "SHOTS_POST",
        "SHOTS_MISSED", "TURNOVERS", "TECHNICAL_FAULTS", "SAVES", "SHOTS_FACED", "YELLOW_CARDS", "TWO_MINUTES",
        "RED_CARDS", "BLUE_CARDS", "TIMEOUTS"}),
    "Advanced": ([
        "POSS", "OPP_POSS", "PACE", "AVG_POSS_SEC", "ORTG", "DRTG", "NET_RTG", "ATTACK_EFF_PCT", "ATTEMPTS",
        "ATT_GOAL_PCT", "NON7M_PCT", "SEVEN_M_PCT", "TOV_PCT", "LOST_BALL_PCT", "TECH_FAULT_PCT", "BLOCKED_PCT",
        "OPP_BLOCKED_PCT", "SAVE_PCT", "OPP_SAVE_PCT", "SUSPENSIONS", "SUSPENSIONS_DRAWN", "PBP_GAMES",
    ], {"POSS", "OPP_POSS", "ATTEMPTS", "SUSPENSIONS", "SUSPENSIONS_DRAWN"}),
    "FourFactors": ([
        "ATT_GOAL_PCT", "TOV_PCT", "RETAIN_PCT", "SEVEN_M_RATE",
        "OPP_ATT_GOAL_PCT", "OPP_TOV_PCT", "OPP_RETAIN_PCT", "OPP_SEVEN_M_RATE", "PBP_GAMES",
    ], set()),
    "ShotProfile": (
        ["ATTEMPTS"] + [f"{g}_{s}" for g in GROUPS for s in ("ATT", "GOALS", "PCT", "SHARE")]
        + ["FAST_BREAK_GOALS_PER_50", "PBP_GAMES"],
        {"ATTEMPTS"} | {f"{g}_{s}" for g in GROUPS for s in ("ATT", "GOALS")},
    ),
    "GameFlow": ([
        "LEAD_CHANGES", "TIMES_TIED", "LARGEST_LEAD", "LONGEST_RUN", "TIME_LEADING_MIN", "TIME_TRAILING_MIN",
        "TIME_TIED_MIN", "CLUTCH_POSS", "CLUTCH_GOALS", "CLUTCH_OPP_POSS", "CLUTCH_OPP_GOALS", "CLUTCH_ORTG",
        "CLUTCH_DRTG", "CLUTCH_NET", "POST_TIMEOUT_POSS", "POST_TIMEOUT_GOALS", "POST_TIMEOUT_EFF_PCT",
        "ATTACK_EFF_PCT", "PBP_GAMES",
    ], {"LEAD_CHANGES", "TIMES_TIED", "TIME_LEADING_MIN", "TIME_TRAILING_MIN", "TIME_TIED_MIN", "CLUTCH_POSS",
        "CLUTCH_GOALS", "CLUTCH_OPP_POSS", "CLUTCH_OPP_GOALS", "POST_TIMEOUT_POSS", "POST_TIMEOUT_GOALS"}),
}
SITUATION_COLUMNS = (
    ["TEAM_ID", "TEAM_NAME", "SITUATION", "GP", "MIN", "POSS", "GOALS", "ORTG", "OPP_POSS", "OPP_GOALS", "DRTG",
     "NET_RTG", "GOAL_DIFF", "STRENGTH_SOURCE"],
    {"POSS", "GOALS", "OPP_POSS", "OPP_GOALS", "GOAL_DIFF"},
)
PLAYER_MEASURES: dict[str, tuple[list[str], set[str]]] = {
    "Base": ([
        "GOALS", "SHOTS", "SHOT_PCT", "GOALS_7M", "SHOTS_7M", "SEVEN_M_PCT", "SHOTS_SAVED", "SHOTS_POST",
        "SHOTS_MISSED", "TURNOVERS", "TECHNICAL_FAULTS", "YELLOW_CARDS", "TWO_MINUTES", "RED_CARDS", "BLUE_CARDS",
    ], {"GOALS", "SHOTS", "GOALS_7M", "SHOTS_7M", "SHOTS_SAVED", "SHOTS_POST", "SHOTS_MISSED", "TURNOVERS",
        "TECHNICAL_FAULTS", "YELLOW_CARDS", "TWO_MINUTES", "RED_CARDS", "BLUE_CARDS"}),
    "Advanced": ([
        "POSS_ON", "OFF_POSS_ON", "DEF_POSS_ON", "ATTEMPTS", "ATT_GOAL_PCT", "NON7M_PCT", "SEVEN_M_PCT", "USG_PCT",
        "TOV_RATIO", "GOAL_SHARE", "SHOT_SHARE", "PLUS_MINUS", "ON_ORTG", "ON_DRTG", "ON_NET", "OFF_ORTG",
        "OFF_DRTG", "OFF_NET", "ON_OFF_NET", "ADVANTAGE_GOALS", "DISADVANTAGE_GOALS", "EMPTY_NET_GOALS",
        "CLUTCH_GOALS",
    ] + [f"{g}_{s}" for g in GROUPS for s in ("SHARE", "PCT")] + ["PBP_GAMES", "LINEUP_GAMES"],
        {"POSS_ON", "OFF_POSS_ON", "DEF_POSS_ON", "ATTEMPTS", "PLUS_MINUS", "ADVANTAGE_GOALS",
         "DISADVANTAGE_GOALS", "EMPTY_NET_GOALS", "CLUTCH_GOALS"}),
    "Goalkeeping": ([
        "SAVES", "SHOTS_FACED", "SAVE_PCT", "SAVES_7M", "SHOTS_FACED_7M", "SEVEN_M_SAVE_PCT", "SAVES_PER_60",
        "DEF_POSS_ON", "ON_DRTG",
    ] + [f"{g}_{s}" for g in FIELD_GROUPS for s in ("FACED", "SAVE_PCT")] + ["PBP_GAMES", "LINEUP_GAMES"],
        {"SAVES", "SHOTS_FACED", "SAVES_7M", "SHOTS_FACED_7M", "DEF_POSS_ON"} | {f"{g}_FACED" for g in FIELD_GROUPS}),
}


def scale(row: dict, counting: set[str], per_mode: str, games, seconds, poss) -> dict:
    """Scale the counting columns of a finished row; rates are left alone."""
    if per_mode not in PER_MODES:
        raise ValueError(f"per_mode must be one of {PER_MODES}, not {per_mode!r}")
    if per_mode == "Totals":
        return row
    basis, factor = {"PerGame": (games, 1), "Per60": (seconds, 3600), "Per50Possessions": (poss, POSS_SCALE)}[per_mode]
    out = dict(row)
    for col in counting:
        if col in out and out[col] is not None:
            out[col] = round(factor * out[col] / basis, 2) if basis else None
    if per_mode == "PerGame" and out.get("MIN") is not None and games:
        out["MIN"] = round(out["MIN"] / games, 1)
    return out


def _pick(row: dict, columns: list[str]) -> dict:
    return {c: row.get(c) for c in columns}


def team_table(teams: list[dict], measure: str = "Base", per_mode: str = "Totals") -> tuple[list[str], list[dict]]:
    if measure not in TEAM_MEASURES:
        raise ValueError(f"team measure_type must be one of {list(TEAM_MEASURES) + ['Situations']}, not {measure!r}")
    columns, counting = TEAM_MEASURES[measure]
    headers = TEAM_ID_COLUMNS + [c for c in columns if c not in TEAM_ID_COLUMNS]
    ordered = sorted(teams, key=lambda c: (-(2 * (c.get("W") or 0) + (c.get("D") or 0)),
                                           -((c.get("GOALS") or 0) - (c.get("OPP_GOALS") or 0)), str(c.get("TEAM_NAME"))))
    out = []
    for c in ordered:
        row = {**c, **team_rates(c)}
        poss_basis = ((c.get("POSS") or 0) + (c.get("OPP_POSS") or 0)) / 2 if c.get("POSS") is not None else None
        out.append(_pick(scale(row, counting, per_mode, c.get("GP"), c.get("SECONDS"), poss_basis), headers))
    return headers, out


def situation_table(situations: list[dict], per_mode: str = "Totals") -> tuple[list[str], list[dict]]:
    columns, counting = SITUATION_COLUMNS
    order = {s: i for i, s in enumerate(SITUATIONS)}
    out = []
    for c in situations:
        row = {**c, **situation_rates(c)}
        poss_basis = ((c.get("POSS") or 0) + (c.get("OPP_POSS") or 0)) / 2
        out.append(_pick(scale(row, counting, per_mode, c.get("GP"), c.get("SECONDS"), poss_basis), columns))
    out.sort(key=lambda r: (str(r["TEAM_NAME"]), order.get(r["SITUATION"], 9)))
    return columns, out


def player_table(players: list[dict], measure: str = "Base", per_mode: str = "Totals",
                 min_minutes: float = 0) -> tuple[list[str], list[dict]]:
    if measure not in PLAYER_MEASURES:
        raise ValueError(f"player measure_type must be one of {list(PLAYER_MEASURES)}, not {measure!r}")
    columns, counting = PLAYER_MEASURES[measure]
    headers = PLAYER_ID_COLUMNS + [c for c in columns if c not in PLAYER_ID_COLUMNS]
    rates = goalkeeper_rates if measure == "Goalkeeping" else player_rates
    key = "SAVES" if measure == "Goalkeeping" else "GOALS"
    out = []
    for c in sorted(players, key=lambda c: (-(c.get(key) or 0), -(c.get("SECONDS") or 0), str(c.get("PLAYER_NAME")))):
        if not c.get("GP") or (c.get("SECONDS") or 0) / 60 < min_minutes:
            continue
        row = {**c, **rates(c)}
        if measure == "Goalkeeping":
            poss_basis = c.get("DEF_POSS_ON")
        elif c.get("OFF_POSS_ON") is not None:
            poss_basis = ((c.get("OFF_POSS_ON") or 0) + (c.get("DEF_POSS_ON") or 0)) / 2
        else:
            poss_basis = None
        out.append(_pick(scale(row, counting, per_mode, c.get("GP"), c.get("SECONDS"), poss_basis), headers))
    return headers, out


__all__ = [
    "PER_MODES", "SITUATIONS", "TEAM_MEASURES", "PLAYER_MEASURES", "match_components", "aggregate", "team_table",
    "situation_table", "player_table", "pbp_box", "game_flow",
]
