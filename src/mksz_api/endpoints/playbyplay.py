"""PlayByPlay: the livescore event log of one match, like nba_api's PlayByPlayV2.

Source: livescore.mksz.hu/<match_id>. The site stores every action entered by the two scorer devices
(shots with court/goal zones, saves, turnovers, sanctions, timeouts, substitutions). Raw events are not in
chronological order and their teamScore field lags, so events are sorted by period and game time and the
score is recomputed from the goal events.

Team "A" in the log is the home team (same team id as the match report).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from mksz_api.endpoints._base import DataSet, Endpoint, NoDataError, make_dataset, mmss
from mksz_api.library import pages

TEN_MINUTES = 600
END_PERIOD = "13"  # "Mérkőzés vége"

EVENT_TYPES = {
    "gol": "GOAL",
    "gol7m": "GOAL_7M",
    "vedett": "SHOT_SAVED",
    "vedett7m": "SHOT_7M_SAVED",
    "rontott": "SHOT_MISSED",
    "rontott7m": "SHOT_7M_MISSED",
    "kapufa": "SHOT_POST",
    "kapufa7m": "SHOT_7M_POST",
    "blokkolt": "SHOT_BLOCKED",
    "eladott": "TURNOVER",
    "technikai": "TECHNICAL_FAULT",
    "2perc": "TWO_MINUTES",
    "sargalap": "YELLOW_CARD",
    "piroslap": "RED_CARD",
    "keklap": "BLUE_CARD",
    "timeOut": "TIMEOUT",
    "Kezdőjátékos": "STARTER",
    "Csere": "SUBSTITUTION",
    "Kiállítás": "SUSPENSION_LEAVE",  # player leaves the court to serve a suspension
    "Felmegy": "SUSPENSION_RETURN",
    "periodchange": "PERIOD_START",
    "gamedatachange": "GOALKEEPER_CHANGE",
}
SKIP_ACTIONS = {"init", "playtime"}
GOAL_ACTIONS = {"gol", "gol7m"}
SIDE = {"teamA": "A", "teamB": "B", "staffA": "A", "staffB": "B", "A": "A", "B": "B"}
HOME_AWAY = {"A": "HOME", "B": "AWAY"}


def events_are_final(payload: list) -> bool:
    return any(_event(x).get("period") == END_PERIOD for x in payload or [])


def _elapsed(e: dict) -> int | None:
    """Cumulative game seconds; goalkeeper changes keep it inside params."""
    if e.get("totalGameTime") is not None:
        return e["totalGameTime"]
    params = e.get("params") if isinstance(e.get("params"), dict) else {}
    return params.get("totalGameTime")


def _event(item: dict) -> dict:
    e = item.get("event")
    return json.loads(e) if isinstance(e, str) else (e or {})


class PlayByPlay(Endpoint):
    endpoint = "playbyplay"
    HEADERS = [
        "MATCH_ID", "EVENT_NUM", "PERIOD", "PERIOD_NAME", "CLOCK", "ELAPSED_SEC", "HOME_AWAY", "TEAM_ID",
        "TEAM_NAME", "EVENT_TYPE", "ACTION", "ACTION_NAME", "PLAYER_ID", "JERSEY", "PLAYER_NAME", "IS_STAFF",
        "PLAYER_IN_ID", "PLAYER_IN_NAME", "PLAYER_OUT_ID", "PLAYER_OUT_NAME", "OPP_GOALKEEPER_ID", "OPP_GOALKEEPER_NAME",
        "SHOT_AREA", "GOAL_AREA", "HOME_SCORE", "AWAY_SCORE", "WALLCLOCK_UTC", "SOURCE_ID",
    ]

    def __init__(self, match_id: str | int, **kwargs):
        self.match_id = str(match_id)
        super().__init__(**kwargs)

    def parameters(self) -> dict:
        return {"match_id": self.match_id}

    def _load(self) -> dict[str, Any]:
        events = self._fetch(
            "events", self.match_id, pages.match_events(self.match_id), "events",
            max_age=TEN_MINUTES, is_final=events_are_final,
        )
        if not events:
            raise NoDataError(f"no livescore event log for match {self.match_id}")
        return {"events": events}

    def _parse(self, raw: dict[str, Any]) -> list[DataSet]:
        return [make_dataset("PlayByPlay", self.HEADERS, parse_events(raw["events"], self.match_id))]


def parse_events(payload: list[dict], match_id: str) -> list[dict]:
    events = [_event(x) for x in payload]
    inits = [e["params"] for e in events if e.get("action") == "init" and isinstance(e.get("params"), dict)]
    init = inits[-1] if inits else {}
    teams = {
        "A": (init.get("team_a_id"), init.get("team_a_name")),
        "B": (init.get("team_b_id"), init.get("team_b_name")),
    }
    people: dict[Any, tuple[int | None, str | None, bool]] = {}  # id -> (jersey, name, is_staff)
    for side in ("a", "b"):
        for p in init.get(f"team_{side}_players") or []:
            people[p.get("id")] = (p.get("jersey"), p.get("name"), False)
        for o in init.get(f"team_{side}_officials") or []:
            if isinstance(o, dict) and o.get("id") is not None:
                people.setdefault(o["id"], (None, o.get("name"), True))
    periods = {str(p.get("value")): p for p in init.get("periods") or []}

    def order(period: str | None) -> int:
        p = periods.get(str(period))
        if p and p.get("order") is not None:
            return int(p["order"])
        try:
            return int(period)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return 999

    kept, seen_periods = [], set()
    for e in events:
        action = e.get("action")
        if action is None or action in SKIP_ACTIONS:
            continue
        params = e.get("params") if isinstance(e.get("params"), dict) else {}
        if params.get("isDraft") or e.get("isDraft"):
            continue
        if action == "periodchange":  # both devices log it; keep one per period
            period = str(params.get("period") or e.get("period"))
            if period in seen_periods:
                continue
            seen_periods.add(period)
        if action == "gamedatachange" and not str(params.get("name", "")).endswith("actual_goalkeeper"):
            continue
        kept.append(e)

    def sort_key(e: dict):
        params = e.get("params") if isinstance(e.get("params"), dict) else {}
        period = str(params.get("period") if e.get("action") == "periodchange" else e.get("period"))
        elapsed = _elapsed(e)
        if e.get("action") == "periodchange" or elapsed is None:
            elapsed = -1  # period starts (and timeless events) go first in their period
        return (order(period), elapsed, e.get("date") or 0)

    kept.sort(key=sort_key)

    rows, score = [], {"A": 0, "B": 0}
    for num, e in enumerate(kept, start=1):
        action = e["action"]
        params = e.get("params") if isinstance(e.get("params"), dict) else {}
        period = str(params.get("period")) if action == "periodchange" else str(e.get("period"))
        side = SIDE.get(params.get("selectedTeamId") or e.get("team") or "")
        player_id = params.get("selectedPlayer")
        if action == "gamedatachange":
            side = "A" if params["name"].startswith("team_a") else "B"
            player_id = (params.get("value") or {}).get("id")
        if action in GOAL_ACTIONS and side:
            score[side] += 1
        jersey, name, is_staff = people.get(player_id, (None, None, False))
        gk_id = (params.get("otherTeamGoalKeeper") or {}).get("id")
        up, down = e.get("up") or {}, e.get("down") or {}
        if action == "Kiállítás":  # suspension: the excluded player leaves
            down = e.get("excluded") or down
        if action in ("Kezdőjátékos", "Csere", "Kiállítás", "Felmegy"):
            # substitution events name players directly; report the main player as the one entering
            # (or leaving, for a suspension)
            main = down if action == "Kiállítás" else up
            player_id, jersey, name = main.get("id"), main.get("jersey"), main.get("name")
        elapsed = _elapsed(e)
        selected_action = params.get("selectedAction") or {}
        team_id, team_name = teams.get(side, (None, None)) if side else (None, None)
        rows.append({
            "MATCH_ID": str(match_id),
            "EVENT_NUM": num,
            "PERIOD": period,
            "PERIOD_NAME": (periods.get(period) or {}).get("text"),
            "CLOCK": mmss(elapsed) if action != "periodchange" else None,
            "ELAPSED_SEC": elapsed if action != "periodchange" else None,
            "HOME_AWAY": HOME_AWAY.get(side) if side else None,
            "TEAM_ID": team_id,
            "TEAM_NAME": team_name,
            "EVENT_TYPE": EVENT_TYPES.get(action, action.upper()),
            "ACTION": action,
            "ACTION_NAME": selected_action.get("actionName") or action,
            "PLAYER_ID": player_id,
            "JERSEY": jersey,
            "PLAYER_NAME": name,
            "IS_STAFF": is_staff or str(params.get("selectedTeamId", "")).startswith("staff"),
            "PLAYER_IN_ID": up.get("id"),
            "PLAYER_IN_NAME": up.get("name"),
            "PLAYER_OUT_ID": down.get("id"),
            "PLAYER_OUT_NAME": down.get("name"),
            "OPP_GOALKEEPER_ID": gk_id,
            "OPP_GOALKEEPER_NAME": people.get(gk_id, (None, None, False))[1] if gk_id is not None else None,
            "SHOT_AREA": params.get("selectedArea"),
            "GOAL_AREA": params.get("selectedGateArea"),
            "HOME_SCORE": score["A"],
            "AWAY_SCORE": score["B"],
            "WALLCLOCK_UTC": (
                datetime.fromtimestamp(e["date"] / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
                if e.get("date") else None
            ),
            "SOURCE_ID": e.get("_id"),
        })
    return rows
