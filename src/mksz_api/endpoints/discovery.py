"""Discovery endpoints: seasons → competitions/stages → matches, plus standings.

The site's hierarchy: season (evad) → competition (főverseny, e.g. "NB I Férfi") → stage (alverseny,
e.g. "K&H Férfi Liga", or one cup round / one NB II group) → matches (mérkőzés). The league id
(LEAGUE_ID) is the site's globalisVersenyKod: NBI, NBIB, NBII, MAGYAR_KUPA, SZUPERKUPA, ...
Only adult competitions are listed (that is what the site's competition overview shows).
"""

from __future__ import annotations

from typing import Any

from mksz_api.endpoints._base import DataSet, Endpoint, NoDataError, make_dataset
from mksz_api.library import pages
from mksz_api.library.parameters import (
    current_season_year,
    gender_code,
    league_code,
    season_label,
    season_year,
)

DAY = 24 * 3600
SIX_HOURS = 6 * 3600


def _past(year: int) -> bool:
    return year < current_season_year()


class Seasons(Endpoint):
    """All seasons, newest first. sport="SPAG_1" is indoor handball, "SPAG_2" beach handball."""

    endpoint = "seasons"
    HEADERS = ["SEASON_ID", "SEASON", "START_DATE", "END_DATE", "IS_CURRENT"]

    def __init__(self, sport: str = "SPAG_1", **kwargs):
        self.sport = sport
        super().__init__(**kwargs)

    def parameters(self) -> dict:
        return {"sport": self.sport}

    def _load(self) -> dict[str, Any]:
        return {"evad": self._fetch("seasons", "evad", pages.seasons(), "evad", max_age=DAY)}

    def _parse(self, raw: dict[str, Any]) -> list[DataSet]:
        rows = [
            {
                "SEASON_ID": s["id"]["ev"],
                "SEASON": s["nev"],
                "START_DATE": (s.get("kezdete") or "")[:10] or None,
                "END_DATE": (s.get("vege") or "")[:10] or None,
                "IS_CURRENT": bool(s.get("aktualis")),
            }
            for s in raw["evad"]
            if s["id"].get("sportagKod") == self.sport
        ]
        rows.sort(key=lambda r: -r["SEASON_ID"])
        return [make_dataset("Seasons", self.HEADERS, rows)]


class CompetitionFinder(Endpoint):
    """Adult competitions and their stages in a season, one row per stage.

    CompetitionFinder(season="2026/27", gender="F", league="NBI")"""

    endpoint = "competitionfinder"
    HEADERS = [
        "SEASON_ID", "SEASON", "GENDER", "LEAGUE_ID", "LEAGUE_NAME", "EVENT_NAME", "COMPETITION_ID",
        "COMPETITION_NAME", "STAGE_ID", "STAGE_NAME", "STAGE_ORDER", "STAGE_TYPE", "AGE_GROUP", "OFFICIAL",
    ]

    def __init__(self, season: int | str | None = None, gender: str | None = None, league: str | None = None, **kwargs):
        self.year = season_year(season)
        self.gender = gender_code(gender)
        self.league = league_code(league)
        super().__init__(**kwargs)

    def parameters(self) -> dict:
        return {"season": season_label(self.year), "gender": self.gender, "league": self.league}

    def _load(self) -> dict[str, Any]:
        return {"competitions": self._fetch_both()}

    def _fetch_both(self) -> dict[str, list]:
        """One page load returns both genders' lists; cache them together as one payload."""
        if not self.refresh:
            cached = self.cache.get("competitions", self.year, max_age=DAY, is_final=lambda _: _past(self.year))
            # An all-empty entry is a bad capture (older versions could store one), never a real season.
            if cached is not None and any(cached.values()):
                return cached
        from mksz_api.library.transport import get_transport

        got = (self.transport or get_transport()).fetch(pages.competitions(self.year))
        data = {"F": got["F"], "N": got["N"]}
        if any(data.values()):
            self.cache.put("competitions", self.year, data)
        return data

    def _parse(self, raw: dict[str, Any]) -> list[DataSet]:
        rows = []
        for gender, comps in raw["competitions"].items():
            if self.gender and gender != self.gender:
                continue
            for c in comps:
                if self.league and league_code(c.get("globalisVersenyKod")) != self.league:
                    continue
                base = {
                    "SEASON_ID": c.get("evadEv", self.year),
                    "SEASON": c.get("evadNev") or season_label(self.year),
                    "GENDER": c.get("nem", gender),
                    "LEAGUE_ID": c.get("globalisVersenyKod"),
                    "LEAGUE_NAME": c.get("globalisVersenyNev"),
                    "EVENT_NAME": c.get("sportesemenyNev"),
                    "COMPETITION_ID": c["id"],
                    "COMPETITION_NAME": c.get("rovidNev") or c.get("nev"),
                    "AGE_GROUP": c.get("korosztalyNev"),
                    "OFFICIAL": c.get("sportesemenyTipusKod") == "SPETIP_HIV",
                }
                for s in c.get("alversenyList") or [{}]:
                    rows.append({
                        **base,
                        "STAGE_ID": s.get("id"),
                        "STAGE_NAME": s.get("rovidNev") or s.get("nev"),
                        "STAGE_ORDER": s.get("alversenySorrend"),
                        "STAGE_TYPE": s.get("tipusKod"),
                    })
        return [make_dataset("Competitions", self.HEADERS, rows)]


def find_stages(
    season: int | str | None = None,
    league: str | None = None,
    gender: str | None = None,
    competition_id: int | None = None,
    stage_id: int | None = None,
    **kwargs,
) -> list[dict]:
    """Stages (rows of CompetitionFinder) matching the filters."""
    rows = CompetitionFinder(season=season, gender=gender, league=league, **kwargs).competitions.get_records()
    if competition_id is not None:
        rows = [r for r in rows if r["COMPETITION_ID"] == int(competition_id)]
    if stage_id is not None:
        rows = [r for r in rows if r["STAGE_ID"] == int(stage_id)]
    return [r for r in rows if r["STAGE_ID"] is not None]


class MatchFinder(Endpoint):
    """All matches of the selected stages in a season, like nba_api's LeagueGameFinder.

    MatchFinder(season="2026/27", league="NBI", gender="F")
    MatchFinder(season=2026, competition_id=32027115)        # every stage of that competition
    MatchFinder(season=2026, stage_id=320228793)"""

    endpoint = "matchfinder"
    HEADERS = [
        "MATCH_ID", "SEASON_ID", "SEASON", "GENDER", "LEAGUE_ID", "COMPETITION_ID", "COMPETITION_NAME",
        "STAGE_ID", "STAGE_NAME", "ROUND", "MATCH_DATE", "VENUE", "VENUE_CITY", "HOME_TEAM_ID", "HOME_TEAM",
        "AWAY_TEAM_ID", "AWAY_TEAM", "HOME_SCORE", "AWAY_SCORE", "HOME_HT_SCORE", "AWAY_HT_SCORE", "STATUS",
        "ATTENDANCE",
    ]

    def __init__(
        self,
        season: int | str | None = None,
        league: str | None = None,
        gender: str | None = None,
        competition_id: int | None = None,
        stage_id: int | None = None,
        **kwargs,
    ):
        if league is None and competition_id is None and stage_id is None:
            raise ValueError("give league=, competition_id= or stage_id= (listing every county league is too broad)")
        self.year = season_year(season)
        self.league = league_code(league)
        self.gender = gender_code(gender)
        self.competition_id = competition_id
        self.stage_id = stage_id
        super().__init__(**kwargs)

    def parameters(self) -> dict:
        return {
            "season": season_label(self.year), "league": self.league, "gender": self.gender,
            "competition_id": self.competition_id, "stage_id": self.stage_id,
        }

    def _load(self) -> dict[str, Any]:
        stages = find_stages(
            self.year, self.league, self.gender, self.competition_id, self.stage_id,
            transport=self.transport, cache=self.cache,
        )
        if not stages:
            raise NoDataError(f"no stage matches {self.parameters()}")
        program = {}
        for s in stages:
            program[s["STAGE_ID"]] = self._fetch(
                "program", f"{s['STAGE_ID']}_{self.year}",
                pages.program(s["GENDER"], s["COMPETITION_ID"], s["STAGE_ID"], self.year), "program",
                max_age=SIX_HOURS, is_final=lambda _: _past(self.year),
            )
        return {"stages": stages, "program": program}

    def _parse(self, raw: dict[str, Any]) -> list[DataSet]:
        stages = {s["STAGE_ID"]: s for s in raw["stages"]}
        rows = []
        for stage_id, matches in raw["program"].items():
            stage = stages.get(int(stage_id), {})
            for m in matches:
                rows.append(program_row(m, stage))
        rows.sort(key=lambda r: (r["MATCH_DATE"] or "", r["MATCH_ID"]))
        return [make_dataset("Matches", self.HEADERS, rows)]


def program_row(m: dict, stage: dict | None = None) -> dict:
    stage = stage or {}
    return {
        "MATCH_ID": str(m["merkozesId"]),
        "SEASON_ID": m.get("evadEv"),
        "SEASON": m.get("evadNev"),
        "GENDER": m.get("nem"),
        "LEAGUE_ID": m.get("globalisVersenyKod") or stage.get("LEAGUE_ID"),
        "COMPETITION_ID": m.get("foversenyId"),
        "COMPETITION_NAME": m.get("foversenyRovidNev"),
        "STAGE_ID": m.get("alversenyId"),
        "STAGE_NAME": m.get("alversenyRovidNev"),
        "ROUND": m.get("fordulo"),
        "MATCH_DATE": (m.get("merkozesDatum") or "")[:16] or None,
        "VENUE": m.get("letesitmenyNev"),
        "VENUE_CITY": m.get("letesitmenyCim"),
        "HOME_TEAM_ID": m.get("hazaiCsapatId"),
        "HOME_TEAM": m.get("hazaiCsapatNev"),
        "AWAY_TEAM_ID": m.get("vendegCsapatId"),
        "AWAY_TEAM": m.get("vendegCsapatNev"),
        "HOME_SCORE": m.get("hazaiCsapatGol"),
        "AWAY_SCORE": m.get("vendegCsapatGol"),
        "HOME_HT_SCORE": m.get("hazaiCsapatFelidoGol"),
        "AWAY_HT_SCORE": m.get("vendegCsapatFelidoGol"),
        "STATUS": m.get("merkozesStatusz"),
        "ATTENDANCE": m.get("nezoszam"),
    }


class Standings(Endpoint):
    """League table of a stage. Standings(stage_id=320228793, season="2026/27")."""

    endpoint = "standings"
    HEADERS = [
        "SEASON_ID", "STAGE_ID", "STAGE_NAME", "RANK", "TEAM_ID", "TEAM_NAME", "GP", "W", "D", "L",
        "GOALS_FOR", "GOALS_AGAINST", "GOAL_DIFF", "PTS", "PENALTY_PTS", "BONUS_PTS",
    ]

    def __init__(self, stage_id: int, season: int | str | None = None, competition_id: int | None = None,
                 gender: str | None = None, **kwargs):
        self.stage_id = int(stage_id)
        self.year = season_year(season)
        self.competition_id = competition_id
        self.gender = gender_code(gender)
        super().__init__(**kwargs)

    def parameters(self) -> dict:
        return {"stage_id": self.stage_id, "season": season_label(self.year)}

    def _load(self) -> dict[str, Any]:
        competition_id, gender = self.competition_id, self.gender
        if competition_id is None or gender is None:
            stages = find_stages(self.year, stage_id=self.stage_id, transport=self.transport, cache=self.cache)
            if not stages:
                raise NoDataError(f"stage {self.stage_id} not found in season {season_label(self.year)}")
            competition_id, gender = stages[0]["COMPETITION_ID"], stages[0]["GENDER"]
        tabella = self._fetch(
            "standings", self.stage_id, pages.standings(gender, competition_id, self.stage_id), "tabella",
            max_age=SIX_HOURS, is_final=lambda d: bool(d.get("versenyLezarva")),
        )
        return {"tabella": tabella}

    def _parse(self, raw: dict[str, Any]) -> list[DataSet]:
        t = raw["tabella"]
        rows = []
        for i, r in enumerate(t.get("tabella") or [], start=1):
            gf, ga = r.get("lottGol"), r.get("kapottGol")
            rows.append({
                "SEASON_ID": t.get("evId"),
                "STAGE_ID": t.get("id"),
                "STAGE_NAME": t.get("rovidNev"),
                "RANK": i,
                "TEAM_ID": r.get("csapatId"),
                "TEAM_NAME": r.get("csapatNev"),
                "GP": r.get("lejatszottMerkozes"),
                "W": r.get("gyozelem"),
                "D": r.get("dontetlen"),
                "L": r.get("vereseg"),
                "GOALS_FOR": gf,
                "GOALS_AGAINST": ga,
                "GOAL_DIFF": gf - ga if gf is not None and ga is not None else None,
                "PTS": r.get("pont"),
                "PENALTY_PTS": r.get("buntetoPont"),
                "BONUS_PTS": r.get("bonuszPont"),
            })
        return [make_dataset("Standings", self.HEADERS, rows)]
