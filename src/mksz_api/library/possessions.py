"""Possessions rebuilt from the play-by-play.

A possession ends with a terminal event of the attacking team: a goal, any other shot outcome (saved,
missed, post, blocked; 7 m throws included), a turnover or a technical fault. If the next terminal event in
the same period belongs to the same team after a non-goal shot, the team kept the ball (rebound, block out
of bounds, ...): the possession continues and RETAINED goes up by one, like an offensive rebound in
basketball. A new period always starts a new possession.

Each possession carries the game state when it ended: the score margin before its last event, the
numerical situation (suspensions), whether the attacking team had pulled its goalkeeper, both lineups,
whether it is in clutch time, and whether it followed a timeout of the attacking team.
"""

from __future__ import annotations

from mksz_api.library.events import LIVE_BALL_OUTCOMES, OTHER_SIDE, SHOT_EVENTS, TERMINAL_EVENTS
from mksz_api.library.oncourt import Lineups, situation
from mksz_api.library.zones import SEVEN_METRE_ZONE, court_zone, zone_group

CLUTCH_FROM = 3300  # last 5 minutes of the second half, and all of overtime
CLUTCH_MARGIN = 2  # goals

HEADERS = [
    "MATCH_ID", "POSS_NUM", "PERIOD", "OFFENSE", "TEAM_ID", "TEAM_NAME", "START_SEC", "END_SEC", "DURATION",
    "END_TYPE", "END_EVENT_NUM", "END_PLAYER_ID", "END_PLAYER_NAME", "GOALS", "ATTEMPTS", "RETAINED", "IS_7M", "SHOT_ZONE",
    "SHOT_ZONE_GROUP", "SCORE_MARGIN_START", "SCORE_MARGIN_END", "OFF_FIELD_PLAYERS", "DEF_FIELD_PLAYERS",
    "SITUATION", "OFF_EMPTY_NET", "DEF_EMPTY_NET", "IS_CLUTCH", "AFTER_TIMEOUT", "OFF_LINEUP", "DEF_LINEUP",
]


def is_clutch(elapsed: int | None, margin: int) -> bool:
    """Clutch time: last 5 minutes of the second half or overtime, with the margin within 2 goals."""
    return elapsed is not None and elapsed >= CLUTCH_FROM and abs(margin) <= CLUTCH_MARGIN


def clutch_windows(rows: list[dict], game_end: int) -> list[tuple[int, int]]:
    """(start, end) game-second windows of clutch time, from the score after each goal."""
    goals = [(r["ELAPSED_SEC"] or 0, r["HOME_SCORE"] - r["AWAY_SCORE"])
             for r in rows if r["EVENT_TYPE"] in ("GOAL", "GOAL_7M")]
    windows, margin, t0 = [], 0, CLUTCH_FROM
    margin = next((m for t, m in reversed(goals) if t < CLUTCH_FROM), 0)  # margin entering clutch time
    for t, m in [(t, m) for t, m in goals if t >= CLUTCH_FROM] + [(game_end, None)]:
        if abs(margin) <= CLUTCH_MARGIN and t > t0:
            windows.append((t0, t))
        t0 = max(t0, t)
        if m is not None:
            margin = m
    merged: list[tuple[int, int]] = []
    for a, b in windows:
        if merged and merged[-1][1] == a:
            merged[-1] = (merged[-1][0], b)
        else:
            merged.append((a, b))
    return merged


def _period_starts(rows: list[dict]) -> dict[str, int]:
    starts: dict[str, int] = {}
    for r in rows:
        t = r["ELAPSED_SEC"]
        if t is not None and r["PERIOD"] not in starts:
            starts[r["PERIOD"]] = (t // 300) * 300 if r["PERIOD"] not in ("1", "3") else (0 if r["PERIOD"] == "1" else 1800)
    return starts


def _lineup(state, side) -> str | None:
    if state is None or not state.team(side).on:
        return None
    return " ".join(str(p) for p in sorted(state.team(side).on, key=str))


def possession_log(rows: list[dict], lineups: Lineups | None = None) -> list[dict]:
    """One dict per possession (see HEADERS) from one match's PlayByPlay rows."""
    if not rows:
        return []
    teams = {}
    for r in rows:
        if r["HOME_AWAY"] in ("HOME", "AWAY") and r["TEAM_ID"] is not None:
            teams.setdefault(r["HOME_AWAY"], (r["TEAM_ID"], r["TEAM_NAME"]))
    starts = _period_starts(rows)
    score = {"HOME": 0, "AWAY": 0}
    pending_timeout: dict[str, int | None] = {"HOME": None, "AWAY": None}
    out: list[dict] = []
    cur: dict | None = None
    last_outcome: str | None = None
    for r in rows:
        et, side, t = r["EVENT_TYPE"], r["HOME_AWAY"], r["ELAPSED_SEC"]
        if side not in ("HOME", "AWAY"):
            continue
        if et == "TIMEOUT":
            pending_timeout[side] = t
            continue
        if et not in TERMINAL_EVENTS or r["IS_STAFF"]:
            continue
        t = t or 0
        opp = OTHER_SIDE[side]
        outcome, is_7m = SHOT_EVENTS.get(et, (et, False))
        keeps = (cur is not None and cur["OFFENSE"] == side and cur["PERIOD"] == r["PERIOD"]
                 and last_outcome in LIVE_BALL_OUTCOMES)
        if keeps:
            cur["RETAINED"] += 1
        else:
            same_period = cur is not None and cur["PERIOD"] == r["PERIOD"]
            team_id, team_name = teams.get(side, (None, None))
            cur = {
                "MATCH_ID": r["MATCH_ID"], "POSS_NUM": len(out) + 1, "PERIOD": r["PERIOD"], "OFFENSE": side,
                "TEAM_ID": team_id, "TEAM_NAME": team_name,
                "START_SEC": cur["END_SEC"] if same_period else starts.get(r["PERIOD"], t),
                "GOALS": 0, "ATTEMPTS": 0, "RETAINED": 0, "IS_7M": False, "SHOT_ZONE": None,
                "SHOT_ZONE_GROUP": None, "SCORE_MARGIN_START": score[side] - score[opp], "AFTER_TIMEOUT": False,
            }
            out.append(cur)
        if pending_timeout[side] is not None and pending_timeout[side] <= t:
            cur["AFTER_TIMEOUT"] = True
            pending_timeout[side] = None
        margin_before = score[side] - score[opp]
        cur["END_SEC"] = t
        cur["DURATION"] = max(0, t - cur["START_SEC"])
        cur["END_TYPE"] = outcome
        cur["END_EVENT_NUM"] = r["EVENT_NUM"]
        cur["END_PLAYER_ID"], cur["END_PLAYER_NAME"] = r["PLAYER_ID"], r["PLAYER_NAME"]
        cur["IS_CLUTCH"] = is_clutch(t, margin_before)
        if et in SHOT_EVENTS:
            zone = SEVEN_METRE_ZONE if is_7m else court_zone(r["SHOT_AREA"])
            cur["ATTEMPTS"] += 1
            cur["IS_7M"] = cur["IS_7M"] or is_7m
            cur["SHOT_ZONE"], cur["SHOT_ZONE_GROUP"] = zone, zone_group(zone)
        if outcome == "GOAL":
            cur["GOALS"] += 1
            score[side] += 1
        cur["SCORE_MARGIN_END"] = score[side] - score[opp]
        state = lineups.at(r["EVENT_NUM"]) if lineups else None
        if state is not None:
            off, dfn = state.team(side), state.team(opp)
            cur.update({
                "OFF_FIELD_PLAYERS": off.field_players, "DEF_FIELD_PLAYERS": dfn.field_players,
                "SITUATION": situation(state, side), "OFF_EMPTY_NET": not off.goalkeeper_on,
                "DEF_EMPTY_NET": not dfn.goalkeeper_on,
                "OFF_LINEUP": _lineup(state, side), "DEF_LINEUP": _lineup(state, opp),
            })
        last_outcome = outcome
    for p in out:
        for h in HEADERS:
            p.setdefault(h, None)
    return out
