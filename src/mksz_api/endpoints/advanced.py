"""Advanced statistics endpoints, named after their nba_api counterparts.

    BoxScoreAdvanced("32021352123")                                   # one match
    LeagueDashTeamStats(season="2025/26", league="NBI", gender="F", measure_type="Advanced")
    LeagueDashPlayerStats(season="2025/26", league="NBI", gender="F", per_mode="Per60", min_minutes=120)

Everything is computed from the official report (BoxScore) and the livescore log (PlayByPlay); see
mksz_api.library.advanced for the formulas and docs/glossary.md for every column. Matches without a
livescore log still count for report-based columns; possession-based columns use only matches with one
(PBP_GAMES says how many), and such matches are listed in MissingMatches.
"""

from __future__ import annotations

from typing import Any

from mksz_api.endpoints._base import DataSet, Endpoint, NoDataError, make_dataset
from mksz_api.endpoints._season import matches_in_scope, scope_args
from mksz_api.endpoints.boxscore import BoxScore, parse_report
from mksz_api.endpoints.playbyplay import PlayByPlay, parse_events
from mksz_api.library import advanced as adv
from mksz_api.library.possessions import HEADERS as POSSESSION_HEADERS
from mksz_api.library.transport import TransportError

MISSING_HEADERS = ["MATCH_ID", "REASON"]


def _report_records(report: dict, match_id: str) -> dict[str, list[dict]]:
    return {d.name: d.get_records() for d in parse_report(report, match_id)}


def _merge_tables(tables: list[tuple[list[str], list[dict]]], key: str) -> tuple[list[str], list[dict]]:
    """Join several team tables (same rows, different measures) column-wise on `key`."""
    headers: list[str] = []
    for h, _ in tables:
        headers += [c for c in h if c not in headers]
    merged: dict[Any, dict] = {}
    for _, rows in tables:
        for r in rows:
            merged.setdefault(r[key], {}).update(r)
    return headers, list(merged.values())


class BoxScoreAdvanced(Endpoint):
    """Advanced box score of one match, like nba_api's BoxScoreAdvancedV3.

    Datasets: TeamAdvanced, ShotProfile, Situations, GameFlow, PlayerAdvanced, Goalkeeping, Possessions.
    clutch=True restricts everything to clutch time (last 5 minutes and overtime, margin within 2 goals).
    Without a livescore log only report-based columns are filled (HAS_PBP is False). A lineup feed that is
    inconsistent (a team has the wrong number of players on court for over 5% of the match) is not used:
    HAS_LINEUPS is False, LINEUPS_REJECTED True, and LINEUP_ERROR_PCT gives the share."""

    endpoint = "boxscoreadvanced"

    def __init__(self, match_id: str | int, clutch: bool = False, **kwargs):
        self.match_id = str(match_id)
        self.clutch = clutch
        super().__init__(**kwargs)

    def parameters(self) -> dict:
        return {"match_id": self.match_id, "clutch": self.clutch}

    def _load(self) -> dict[str, Any]:
        common = {"transport": self.transport, "cache": self.cache, "refresh": self.refresh}
        box = BoxScore(self.match_id, **common)
        try:
            events = PlayByPlay(self.match_id, **common).raw["events"]
        except NoDataError:
            if self.clutch:
                raise NoDataError(f"clutch stats need a livescore log; match {self.match_id} has none") from None
            events = None
        return {"report": box.raw["report"], "events": events}

    def _parse(self, raw: dict[str, Any]) -> list[DataSet]:
        rows = parse_events(raw["events"], self.match_id) if raw["events"] else None
        comps = adv.match_components(_report_records(raw["report"], self.match_id), rows, clutch=self.clutch)
        summed = adv.aggregate([comps])
        team = _merge_tables([adv.team_table(summed["teams"], m) for m in ("Advanced", "FourFactors")], "TEAM_ID")
        flags = {k: comps[k] for k in ("HAS_PBP", "HAS_LINEUPS", "LINEUP_ERROR_PCT", "LINEUPS_REJECTED")}
        team_headers = ["MATCH_ID"] + team[0] + list(flags)
        team_rows = [{"MATCH_ID": self.match_id, **r, **flags} for r in team[1]]
        datasets = [make_dataset("TeamAdvanced", team_headers, team_rows)]
        for name, measure in (("ShotProfile", "ShotProfile"), ("GameFlow", "GameFlow")):
            h, r = adv.team_table(summed["teams"], measure)
            datasets.append(make_dataset(name, ["MATCH_ID"] + h, [{"MATCH_ID": self.match_id, **x} for x in r]))
        h, r = adv.situation_table(summed["situations"])
        datasets.append(make_dataset("Situations", ["MATCH_ID"] + h, [{"MATCH_ID": self.match_id, **x} for x in r]))
        h, r = adv.player_table(summed["players"], "Advanced")
        datasets.append(make_dataset("PlayerAdvanced", ["MATCH_ID"] + h, [{"MATCH_ID": self.match_id, **x} for x in r]))
        h, r = adv.player_table(summed["goalkeepers"], "Goalkeeping")
        datasets.append(make_dataset("Goalkeeping", ["MATCH_ID"] + h, [{"MATCH_ID": self.match_id, **x} for x in r]))
        datasets.append(make_dataset("Possessions", POSSESSION_HEADERS, comps["possessions"]))
        return datasets


class _LeagueDash(Endpoint):
    """Shared loading for the season dashboards: report + livescore log of every match in scope."""

    MEASURES: tuple[str, ...] = ()

    def __init__(
        self,
        season: int | str | None = None,
        league: str | None = None,
        gender: str | None = None,
        competition_id: int | None = None,
        stage_id: int | None = None,
        match_ids: list[str | int] | None = None,
        team_id: int | None = None,
        measure_type: str = "Base",
        per_mode: str = "Totals",
        clutch: bool = False,
        **kwargs,
    ):
        self.match_ids, self.scope = scope_args(None, match_ids, season, league, gender, competition_id, stage_id)
        if measure_type not in self.MEASURES:
            raise ValueError(f"measure_type must be one of {self.MEASURES}, not {measure_type!r}")
        if per_mode not in adv.PER_MODES:
            raise ValueError(f"per_mode must be one of {adv.PER_MODES}, not {per_mode!r}")
        self.team_id = int(team_id) if team_id is not None else None
        self.measure_type = measure_type
        self.per_mode = per_mode
        self.clutch = clutch
        super().__init__(**kwargs)

    def parameters(self) -> dict:
        params = {"match_ids": self.match_ids or None, **self.scope, "team_id": self.team_id,
                  "measure_type": self.measure_type, "per_mode": self.per_mode, "clutch": self.clutch}
        return {k: v for k, v in params.items() if v is not None}

    def _load(self) -> dict[str, Any]:
        common = {"transport": self.transport, "cache": self.cache, "refresh": self.refresh}
        teams = {self.team_id} if self.team_id is not None else None
        matches = matches_in_scope(self.match_ids, self.scope, teams, **common)
        reports, events, missing = {}, {}, []
        for m in matches:
            mid = m["MATCH_ID"]
            try:
                reports[mid] = BoxScore(mid, **common).raw["report"]
            except NoDataError as e:
                missing.append({"MATCH_ID": mid, "REASON": str(e)})
                continue
            except TransportError as e:
                missing.append({"MATCH_ID": mid, "REASON": f"report page load failed: {e}"})
                continue
            try:
                events[mid] = PlayByPlay(mid, **common).raw["events"]
            except NoDataError:
                events[mid] = None
                missing.append({"MATCH_ID": mid, "REASON": "no livescore log: report-based columns only"})
            except TransportError as e:
                events[mid] = None
                missing.append({"MATCH_ID": mid, "REASON": f"livescore page load failed: {e}"})
        if not reports:
            reasons = "; ".join(x["REASON"] for x in missing) or "no matches in scope"
            raise NoDataError(f"no match reports for {self.parameters()}: {reasons}")
        return {"matches": matches, "reports": reports, "events": events, "missing": missing}

    def _summed(self, raw: dict[str, Any]) -> dict[str, list[dict]]:
        comps = []
        for mid, report in raw["reports"].items():
            payload = raw["events"].get(mid)
            rows = parse_events(payload, mid) if payload else None
            comps.append(adv.match_components(_report_records(report, mid), rows, clutch=self.clutch))
        return adv.aggregate(comps)

    def _missing(self, raw) -> DataSet:
        return make_dataset("MissingMatches", MISSING_HEADERS, raw.get("missing") or [])


class LeagueDashTeamStats(_LeagueDash):
    """Team stats over a season (or any set of matches), like nba_api's LeagueDashTeamStats.

    measure_type: Base, Advanced, FourFactors, ShotProfile, Situations, GameFlow.
    per_mode: Totals, PerGame, Per60, Per50Possessions (scales counting columns; rates stay as they are).
    Datasets: LeagueDashTeamStats, MissingMatches."""

    endpoint = "leaguedashteamstats"
    MEASURES = (*adv.TEAM_MEASURES, "Situations")

    def _parse(self, raw: dict[str, Any]) -> list[DataSet]:
        summed = self._summed(raw)
        if self.measure_type == "Situations":
            headers, rows = adv.situation_table(summed["situations"], self.per_mode)
        else:
            headers, rows = adv.team_table(summed["teams"], self.measure_type, self.per_mode)
        if self.team_id is not None:
            rows = [r for r in rows if r["TEAM_ID"] == self.team_id]
        return [make_dataset("LeagueDashTeamStats", headers, rows), self._missing(raw)]


class LeagueDashPlayerStats(_LeagueDash):
    """Player stats over a season (or any set of matches), like nba_api's LeagueDashPlayerStats.

    measure_type: Base, Advanced, Goalkeeping. per_mode: Totals, PerGame, Per60, Per50Possessions.
    min_minutes drops players below that many minutes in total. One row per player and team.
    Datasets: LeagueDashPlayerStats, MissingMatches."""

    endpoint = "leaguedashplayerstats"
    MEASURES = tuple(adv.PLAYER_MEASURES)

    def __init__(self, *args, player_id: int | None = None, min_minutes: float = 0, **kwargs):
        self.player_id = int(player_id) if player_id is not None else None
        self.min_minutes = min_minutes
        super().__init__(*args, **kwargs)

    def parameters(self) -> dict:
        params = super().parameters()
        if self.player_id is not None:
            params["player_id"] = self.player_id
        if self.min_minutes:
            params["min_minutes"] = self.min_minutes
        return params

    def _parse(self, raw: dict[str, Any]) -> list[DataSet]:
        summed = self._summed(raw)
        source = summed["goalkeepers"] if self.measure_type == "Goalkeeping" else summed["players"]
        headers, rows = adv.player_table(source, self.measure_type, self.per_mode, self.min_minutes)
        if self.team_id is not None:
            rows = [r for r in rows if r["TEAM_ID"] == self.team_id]
        if self.player_id is not None:
            rows = [r for r in rows if r["PLAYER_ID"] == self.player_id]
        return [make_dataset("LeagueDashPlayerStats", headers, rows), self._missing(raw)]
