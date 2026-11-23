"""ShotChart: shot locations and outcomes for a player, a team or a goalkeeper, like nba_api's
ShotChartDetail.

The data behind the livescore page's "Lövési statisztikák" (shooting) and "Védési statisztikák"
(goalkeeping) panels, built from the play-by-play log of one or many matches:

    ShotChart(match_id="32021352123", player_name="Ocvirk")                  # one player, one match
    ShotChart(season="2025/26", league="NBI", gender="F", player_id=313252)  # one player, a season
    ShotChart(match_id="32021352123", team_id=3810367232)                    # a team's shots
    ShotChart(match_id="32021352123", opponent_team_id=3810367232)           # shots a team conceded
    ShotChart(season="2025/26", league="NBI", gender="F", goalkeeper_id=2640) # shots a keeper faced

Datasets: Shots (one row per attempt), ZoneSummary (18 court zones), GoalSummary (9 goal cells),
Summary (totals), MissingMatches (matches in scope without a livescore log).

Counting follows the site: blocked shots count as attempts from their court zone, 7 m throws are zone 17,
and the goal grid only counts attempts with a recorded goal cell.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any

from mksz_api.endpoints._base import DataSet, Endpoint, NoDataError, make_dataset, pct
from mksz_api.endpoints._season import matches_in_scope, scope_args
from mksz_api.endpoints.playbyplay import PlayByPlay, parse_events
from mksz_api.library.transport import TransportError
from mksz_api.library.zones import COURT_ZONES, GOAL_ROWS, GOAL_SIDES, SEVEN_METRE_ZONE, court_zone, goal_cell

from mksz_api.library.events import SHOT_EVENTS  # noqa: E402  (re-exported: EVENT_TYPE -> (outcome, is 7 m))
from mksz_api.library.events import SHOT_OUTCOMES as OUTCOMES  # noqa: E402


def _norm(text: str | None) -> str:
    text = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def _counts(shots: list[dict]) -> dict:
    """Attempt counts per outcome plus derived percentages, shared by all summaries."""
    c = {o: sum(1 for s in shots if s["OUTCOME"] == o) for o in OUTCOMES}
    attempts = len(shots)
    on_target = c["GOAL"] + c["SAVED"]
    return {
        "ATTEMPTS": attempts,
        "GOALS": c["GOAL"],
        "GOAL_PCT": pct(c["GOAL"], attempts),
        "SAVED": c["SAVED"],
        "MISSED": c["MISSED"],
        "POST": c["POST"],
        "BLOCKED": c["BLOCKED"],
        "ON_TARGET": on_target,
        "SAVE_PCT": pct(c["SAVED"], on_target),
    }


COUNT_HEADERS = ["ATTEMPTS", "GOALS", "GOAL_PCT", "SAVED", "MISSED", "POST", "BLOCKED", "ON_TARGET", "SAVE_PCT"]


class ShotChart(Endpoint):
    endpoint = "shotchart"

    SHOT_HEADERS = [
        "MATCH_ID", "MATCH_DATE", "EVENT_NUM", "PERIOD", "CLOCK", "ELAPSED_SEC", "TEAM_ID", "TEAM_NAME",
        "HOME_AWAY", "OPPONENT_TEAM_ID", "OPPONENT_TEAM_NAME", "PLAYER_ID", "PLAYER_NAME", "JERSEY", "OUTCOME",
        "IS_GOAL", "IS_7M", "SHOT_ZONE", "SHOT_ZONE_NAME", "SHOT_ZONE_NAME_HU", "GOAL_CELL", "GOAL_ROW",
        "GOAL_SIDE", "GOALKEEPER_ID", "GOALKEEPER_NAME", "HOME_SCORE", "AWAY_SCORE",
    ]
    ZONE_HEADERS = ["SHOT_ZONE", "SHOT_ZONE_NAME", "SHOT_ZONE_NAME_HU", "COURT_X", "COURT_Y", *COUNT_HEADERS, "SHARE_PCT"]
    GOAL_HEADERS = ["GOAL_CELL", "GOAL_ROW", "GOAL_SIDE", *COUNT_HEADERS, "SHARE_PCT"]
    SUMMARY_HEADERS = [
        "MATCHES", "PLAYERS", *COUNT_HEADERS, "ATTEMPTS_7M", "GOALS_7M", "ATTEMPTS_NO_ZONE", "ATTEMPTS_NO_GOAL_CELL",
    ]
    MISSING_HEADERS = ["MATCH_ID", "REASON"]

    def __init__(
        self,
        match_id: str | int | None = None,
        match_ids: list[str | int] | None = None,
        season: int | str | None = None,
        league: str | None = None,
        gender: str | None = None,
        competition_id: int | None = None,
        stage_id: int | None = None,
        player_id: int | None = None,
        player_name: str | None = None,
        team_id: int | None = None,
        opponent_team_id: int | None = None,
        goalkeeper_id: int | None = None,
        **kwargs,
    ):
        self.match_ids, self.scope = scope_args(match_id, match_ids, season, league, gender, competition_id, stage_id)
        self.player_id = int(player_id) if player_id is not None else None
        self.player_name = player_name
        self.team_id = int(team_id) if team_id is not None else None
        self.opponent_team_id = int(opponent_team_id) if opponent_team_id is not None else None
        self.goalkeeper_id = int(goalkeeper_id) if goalkeeper_id is not None else None
        super().__init__(**kwargs)

    def parameters(self) -> dict:
        params = {"match_ids": self.match_ids or None, **self.scope, "player_id": self.player_id,
                  "player_name": self.player_name, "team_id": self.team_id,
                  "opponent_team_id": self.opponent_team_id, "goalkeeper_id": self.goalkeeper_id}
        return {k: v for k, v in params.items() if v is not None}

    # -- loading ------------------------------------------------------------------------------------------
    def _matches_in_scope(self) -> list[dict]:
        teams = {t for t in (self.team_id, self.opponent_team_id) if t is not None}  # only the team's matches
        return matches_in_scope(self.match_ids, self.scope, teams, transport=self.transport, cache=self.cache,
                                refresh=self.refresh)

    def _load(self) -> dict[str, Any]:
        matches = self._matches_in_scope()
        events, missing = {}, []
        for m in matches:
            try:
                pbp = PlayByPlay(m["MATCH_ID"], transport=self.transport, cache=self.cache, refresh=self.refresh)
                events[m["MATCH_ID"]] = pbp.raw["events"]
            except NoDataError as e:
                missing.append({"MATCH_ID": m["MATCH_ID"], "REASON": str(e)})
            except TransportError as e:
                missing.append({"MATCH_ID": m["MATCH_ID"], "REASON": f"page load failed: {e}"})
        if not events:
            reasons = "; ".join(x["REASON"] for x in missing) or "no matches in scope"
            raise NoDataError(f"no play-by-play data for {self.parameters()}: {reasons}")
        return {"matches": matches, "events": events, "missing": missing}

    # -- parsing ------------------------------------------------------------------------------------------
    def _parse(self, raw: dict[str, Any]) -> list[DataSet]:
        dates = {m["MATCH_ID"]: m.get("MATCH_DATE") for m in raw["matches"]}
        shots = []
        for match_id, payload in raw["events"].items():
            shots += shot_rows(parse_events(payload, match_id), dates.get(match_id))
        shots = self._filter(shots)

        zone_rows = []
        for zid, zone in COURT_ZONES.items():
            in_zone = [s for s in shots if s["SHOT_ZONE"] == zid]
            zone_rows.append({
                "SHOT_ZONE": zid, "SHOT_ZONE_NAME": zone.name, "SHOT_ZONE_NAME_HU": zone.name_hu,
                "COURT_X": zone.x, "COURT_Y": zone.y, **_counts(in_zone),
                "SHARE_PCT": pct(len(in_zone), len(shots)),
            })
        with_cell = [s for s in shots if s["GOAL_CELL"] is not None]
        goal_rows = []
        for cell in GOAL_ROWS:
            in_cell = [s for s in with_cell if s["GOAL_CELL"] == cell]
            goal_rows.append({
                "GOAL_CELL": cell, "GOAL_ROW": GOAL_ROWS[cell], "GOAL_SIDE": GOAL_SIDES[cell],
                **_counts(in_cell), "SHARE_PCT": pct(len(in_cell), len(with_cell)),
            })
        seven = [s for s in shots if s["IS_7M"]]
        summary = {
            "MATCHES": len({s["MATCH_ID"] for s in shots}),
            "PLAYERS": len({s["PLAYER_ID"] for s in shots if s["PLAYER_ID"] is not None}),
            **_counts(shots),
            "ATTEMPTS_7M": len(seven),
            "GOALS_7M": sum(1 for s in seven if s["IS_GOAL"]),
            "ATTEMPTS_NO_ZONE": sum(1 for s in shots if s["SHOT_ZONE"] is None),
            "ATTEMPTS_NO_GOAL_CELL": sum(1 for s in shots if s["GOAL_CELL"] is None and s["OUTCOME"] in ("GOAL", "SAVED")),
        }
        return [
            make_dataset("Shots", self.SHOT_HEADERS, shots),
            make_dataset("ZoneSummary", self.ZONE_HEADERS, zone_rows),
            make_dataset("GoalSummary", self.GOAL_HEADERS, goal_rows),
            make_dataset("Summary", self.SUMMARY_HEADERS, [summary]),
            make_dataset("MissingMatches", self.MISSING_HEADERS, raw.get("missing") or []),
        ]

    def _filter(self, shots: list[dict]) -> list[dict]:
        if self.player_id is not None:
            shots = [s for s in shots if s["PLAYER_ID"] == self.player_id]
        if self.player_name:
            needle = _norm(self.player_name)
            shots = [s for s in shots if needle in _norm(s["PLAYER_NAME"])]
            found = {s["PLAYER_ID"]: s["PLAYER_NAME"] for s in shots}
            if len(found) > 1:
                names = ", ".join(f"{n} ({pid})" for pid, n in sorted(found.items(), key=lambda kv: str(kv[1])))
                raise ValueError(f"player name {self.player_name!r} is ambiguous: {names}; use player_id")
        if self.team_id is not None:
            shots = [s for s in shots if s["TEAM_ID"] == self.team_id]
        if self.opponent_team_id is not None:
            shots = [s for s in shots if s["OPPONENT_TEAM_ID"] == self.opponent_team_id]
        if self.goalkeeper_id is not None:
            shots = [s for s in shots if s["GOALKEEPER_ID"] == self.goalkeeper_id]
        return shots


def shot_rows(events: list[dict], match_date: str | None = None) -> list[dict]:
    """Shot attempts from parsed PlayByPlay rows of one match."""
    teams = {}
    for e in events:
        if e["HOME_AWAY"] and e["TEAM_ID"] is not None:
            teams.setdefault(e["HOME_AWAY"], (e["TEAM_ID"], e["TEAM_NAME"]))
    rows = []
    for e in events:
        if e["EVENT_TYPE"] not in SHOT_EVENTS or not e["HOME_AWAY"]:
            continue
        outcome, is_7m = SHOT_EVENTS[e["EVENT_TYPE"]]
        zone = SEVEN_METRE_ZONE if is_7m else court_zone(e["SHOT_AREA"])
        cell = goal_cell(e["GOAL_AREA"])
        opp_id, opp_name = teams.get("AWAY" if e["HOME_AWAY"] == "HOME" else "HOME", (None, None))
        rows.append({
            "MATCH_ID": e["MATCH_ID"],
            "MATCH_DATE": match_date,
            "EVENT_NUM": e["EVENT_NUM"],
            "PERIOD": e["PERIOD"],
            "CLOCK": e["CLOCK"],
            "ELAPSED_SEC": e["ELAPSED_SEC"],
            "TEAM_ID": e["TEAM_ID"],
            "TEAM_NAME": e["TEAM_NAME"],
            "HOME_AWAY": e["HOME_AWAY"],
            "OPPONENT_TEAM_ID": opp_id,
            "OPPONENT_TEAM_NAME": opp_name,
            "PLAYER_ID": e["PLAYER_ID"],
            "PLAYER_NAME": e["PLAYER_NAME"],
            "JERSEY": e["JERSEY"],
            "OUTCOME": outcome,
            "IS_GOAL": outcome == "GOAL",
            "IS_7M": is_7m,
            "SHOT_ZONE": zone,
            "SHOT_ZONE_NAME": COURT_ZONES[zone].name if zone is not None else None,
            "SHOT_ZONE_NAME_HU": COURT_ZONES[zone].name_hu if zone is not None else None,
            "GOAL_CELL": cell,
            "GOAL_ROW": GOAL_ROWS.get(cell) if cell else None,
            "GOAL_SIDE": GOAL_SIDES.get(cell) if cell else None,
            "GOALKEEPER_ID": e["OPP_GOALKEEPER_ID"],
            "GOALKEEPER_NAME": e["OPP_GOALKEEPER_NAME"],
            "HOME_SCORE": e["HOME_SCORE"],
            "AWAY_SCORE": e["AWAY_SCORE"],
        })
    return rows
