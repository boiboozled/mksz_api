"""BoxScore: the official match report (jegyzőkönyv) of one match, like nba_api's BoxScoreTraditionalV2.

Source: mksz.hu/jegyzokonyv/<match_id>. Datasets: MatchInfo, PlayerStats, TeamStats, StaffStats, Officials.

Shot counting follows the report: SHOTS = GOALS + SHOTS_SAVED + SHOTS_POST + SHOTS_MISSED (7 m included).
Blocked shots are not in the report; the livescore page adds them to its shot totals, and they are in
PlayByPlay as SHOT_BLOCKED events.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any

from mksz_api.endpoints._base import DataSet, Endpoint, NoDataError, make_dataset, mmss, pct
from mksz_api.library import pages

TEN_MINUTES = 600

PLAYER_FIELDS = {  # report key -> column; counting stats, None in the report means 0
    "gol": "GOALS",
    "loves": "SHOTS",
    "gol7m": "GOALS_7M",
    "loves7m": "SHOTS_7M",
    "vedett": "SHOTS_SAVED",
    "kapufa": "SHOTS_POST",
    "rontott": "SHOTS_MISSED",
    "vedettLoves": "SAVES",
    "kapottLoves": "SHOTS_FACED",
    "eladottLabda": "TURNOVERS",
    "technikaiHiba": "TECHNICAL_FAULTS",
    "sarga": "YELLOW_CARDS",
    "perc2": "TWO_MINUTES",
    "kizar": "RED_CARDS",
    "keklap": "BLUE_CARDS",
}
CARD_FIELDS = {"sarga": "YELLOW_CARDS", "perc2": "TWO_MINUTES", "kizar": "RED_CARDS", "keklap": "BLUE_CARDS"}
SUMMED = list(PLAYER_FIELDS.values())

OFFICIAL_ROLES = {
    "jatekvezeto1": "REFEREE",
    "jatekvezeto2": "REFEREE",
    "szkVb": "TABLE_OFFICIAL",
    "szkVb2": "TABLE_OFFICIAL",
    "jatekvezetoEllenor": "REFEREE_INSPECTOR",
    "szovetsegiEllenor": "FEDERATION_DELEGATE",
}


def _int(v: Any) -> int | None:
    if v is None or v == "":
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def parse_report_date(text: str | None) -> str | None:
    """'2025. 12. 11. - 18:00' -> '2025-12-11T18:00'."""
    if not text:
        return None
    m = re.match(r"\s*(\d{4})\.\s*(\d{1,2})\.\s*(\d{1,2})\.?(?:\s*-\s*(\d{1,2}):(\d{2}))?", text)
    if not m:
        return None
    y, mo, d, hh, mm = m.groups()
    return f"{int(y):04d}-{int(mo):02d}-{int(d):02d}T{int(hh or 0):02d}:{mm or '00'}"


def report_is_final(report: dict) -> bool:
    """Final = has a result and the match is more than a day in the past."""
    if _int(report.get("golHazai")) is None:
        return False
    when = parse_report_date(report.get("tenylegesDatum"))
    return bool(when) and datetime.fromisoformat(when) < datetime.now() - timedelta(days=1)


class BoxScore(Endpoint):
    endpoint = "boxscore"

    INFO_HEADERS = [
        "MATCH_ID", "SEASON_ID", "SEASON", "GENDER", "COMPETITION_ID", "COMPETITION_NAME", "STAGE_ID",
        "STAGE_NAME", "ROUND", "MATCH_DATE", "VENUE", "HOME_TEAM_ID", "HOME_TEAM", "AWAY_TEAM_ID", "AWAY_TEAM",
        "HOME_SCORE", "AWAY_SCORE", "HOME_HT_SCORE", "AWAY_HT_SCORE", "HOME_POINTS", "AWAY_POINTS",
        "ATTENDANCE", "VIDEO_URL",
    ]
    PLAYER_HEADERS = [
        "MATCH_ID", "TEAM_ID", "TEAM_NAME", "HOME_AWAY", "PLAYER_ID", "PLAYER_NAME", "JERSEY", "GK",
        "GOALS", "SHOTS", "SHOT_PCT", "GOALS_7M", "SHOTS_7M", "SHOTS_SAVED", "SHOTS_POST", "SHOTS_MISSED",
        "SAVES", "SHOTS_FACED", "SAVE_PCT", "TURNOVERS", "TECHNICAL_FAULTS", "YELLOW_CARDS", "TWO_MINUTES",
        "RED_CARDS", "BLUE_CARDS", "SECONDS_PLAYED", "MIN",
    ]
    TEAM_HEADERS = [
        "MATCH_ID", "TEAM_ID", "TEAM_NAME", "HOME_AWAY", "SCORE", "HT_SCORE", "SECOND_HALF_SCORE", "POINTS",
        "GOALS", "SHOTS", "SHOT_PCT", "GOALS_7M", "SHOTS_7M", "SHOTS_SAVED", "SHOTS_POST", "SHOTS_MISSED",
        "SAVES", "SHOTS_FACED", "SAVE_PCT", "TURNOVERS", "TECHNICAL_FAULTS", "YELLOW_CARDS", "TWO_MINUTES",
        "RED_CARDS", "BLUE_CARDS", "TIMEOUTS",
    ]
    STAFF_HEADERS = [
        "MATCH_ID", "TEAM_ID", "TEAM_NAME", "HOME_AWAY", "PERSON_ID", "NAME", "LETTER", "ROLE",
        "YELLOW_CARDS", "TWO_MINUTES", "RED_CARDS", "BLUE_CARDS",
    ]
    OFFICIAL_HEADERS = ["MATCH_ID", "ROLE", "SOURCE_FIELD", "PERSON_ID", "NAME"]

    def __init__(self, match_id: str | int, **kwargs):
        self.match_id = str(match_id)
        super().__init__(**kwargs)

    def parameters(self) -> dict:
        return {"match_id": self.match_id}

    def _load(self) -> dict[str, Any]:
        report = self._fetch(
            "report", self.match_id, pages.match_report(self.match_id), "report",
            max_age=TEN_MINUTES, is_final=report_is_final,
        )
        if not report or not report.get("merkozesId"):
            raise NoDataError(f"no match report for {self.match_id}")
        if _int(report.get("golHazai")) is None:
            raise NoDataError(f"match {self.match_id} has no result yet (not played or report not filed)")
        return {"report": report}

    def _parse(self, raw: dict[str, Any]) -> list[DataSet]:
        return parse_report(raw["report"], self.match_id)


def parse_report(r: dict, match_id: str | None = None) -> list[DataSet]:
    match_id = str(r.get("merkozesId") or match_id)
    sides = {
        "HOME": ("hazai", _int(r.get("hazaiCsapatId")), r.get("hazaiCsapatNev")),
        "AWAY": ("vendeg", _int(r.get("vendegCsapatId")), r.get("vendegCsapatNev")),
    }
    info = {
        "MATCH_ID": match_id,
        "SEASON_ID": _int(r.get("ev")),
        "SEASON": r.get("evadNev"),
        "GENDER": r.get("nem"),
        "COMPETITION_ID": _int(r.get("foversenyId")),
        "COMPETITION_NAME": r.get("foversenyRovidNev"),
        "STAGE_ID": _int(r.get("alversenyId")),
        "STAGE_NAME": r.get("alversenyRovidNev"),
        "ROUND": _int(r.get("alversenyFordulo")),
        "MATCH_DATE": parse_report_date(r.get("tenylegesDatum")),
        "VENUE": r.get("letesitmenyNev"),
        "HOME_TEAM_ID": sides["HOME"][1],
        "HOME_TEAM": sides["HOME"][2],
        "AWAY_TEAM_ID": sides["AWAY"][1],
        "AWAY_TEAM": sides["AWAY"][2],
        "HOME_SCORE": _int(r.get("golHazai")),
        "AWAY_SCORE": _int(r.get("golVendeg")),
        "HOME_HT_SCORE": _int(r.get("golHazaiElsoFelido")),
        "AWAY_HT_SCORE": _int(r.get("golVendegElsoFelido")),
        "HOME_POINTS": _int(r.get("pontHazai")),
        "AWAY_POINTS": _int(r.get("pontVendeg")),
        "ATTENDANCE": _int(r.get("nezoszam")),
        "VIDEO_URL": r.get("videoUrl"),
    }

    players, staff, teams = [], [], []
    for side, (prefix, team_id, team_name) in sides.items():
        team_cols = {"MATCH_ID": match_id, "TEAM_ID": team_id, "TEAM_NAME": team_name, "HOME_AWAY": side}
        team_players = []
        for p in r.get(f"{prefix}JatekosDS") or []:
            row = {**team_cols, "PLAYER_ID": _int(p.get("szemelyId")), "PLAYER_NAME": p.get("szemelyNev"),
                   "JERSEY": _int(p.get("mezszam")), "GK": bool(p.get("kapus"))}
            for key, col in PLAYER_FIELDS.items():
                row[col] = _int(p.get(key)) or 0
            row["SHOT_PCT"] = pct(row["GOALS"], row["SHOTS"])
            row["SAVE_PCT"] = pct(row["SAVES"], row["SHOTS_FACED"])
            row["SECONDS_PLAYED"] = _int(p.get("palyanToltottIdo")) or 0
            row["MIN"] = mmss(row["SECONDS_PLAYED"])
            team_players.append(row)
        team_players.sort(key=lambda x: (x["JERSEY"] is None, x["JERSEY"] or 0))
        players += team_players

        team_staff = []
        for s in r.get(f"{prefix}StabDS") or []:
            row = {**team_cols, "PERSON_ID": _int(s.get("szemelyId")), "NAME": s.get("szemelyNev"),
                   "LETTER": s.get("jel"), "ROLE": s.get("pozicio")}
            for key, col in CARD_FIELDS.items():
                row[col] = _int(s.get(key)) or 0
            team_staff.append(row)
        staff += team_staff

        totals = {col: sum(p[col] for p in team_players) for col in SUMMED}
        for col in CARD_FIELDS.values():  # team sanctions include the bench officials'
            totals[col] += sum(s[col] for s in team_staff)
        score = _int(r.get(f"gol{prefix.capitalize()}"))
        ht = _int(r.get(f"gol{prefix.capitalize()}ElsoFelido"))
        second = _int(r.get(f"gol{prefix.capitalize()}MasodikFelido"))
        timeouts = sum(1 for i in (1, 2, 3) if r.get(f"{prefix}Idokeres{i}"))
        teams.append({
            **team_cols, **totals,
            "SCORE": score, "HT_SCORE": ht, "SECOND_HALF_SCORE": second,
            "POINTS": _int(r.get(f"pont{prefix.capitalize()}")),
            "SHOT_PCT": pct(totals["GOALS"], totals["SHOTS"]),
            "TIMEOUTS": timeouts if any(r.get(f"{prefix}Idokeres{i}") is not None for i in (1, 2, 3)) else None,
        })

    # Shots faced by a team = opponent's shots on target (goals + saved), as the livescore page counts them;
    # this also covers shots on an empty goal, which no goalkeeper's SHOTS_FACED includes.
    for team, opp in ((teams[0], teams[1]), (teams[1], teams[0])):
        team["SHOTS_FACED"] = opp["GOALS"] + opp["SHOTS_SAVED"]
        team["SAVE_PCT"] = pct(team["SAVES"], team["SHOTS_FACED"])

    officials = []
    for key, role in OFFICIAL_ROLES.items():
        if r.get(key):
            officials.append({"MATCH_ID": match_id, "ROLE": role, "SOURCE_FIELD": key,
                              "PERSON_ID": _int(r.get(f"{key}Id")), "NAME": r.get(key)})

    return [
        make_dataset("MatchInfo", BoxScore.INFO_HEADERS, [info]),
        make_dataset("PlayerStats", BoxScore.PLAYER_HEADERS, players),
        make_dataset("TeamStats", BoxScore.TEAM_HEADERS, teams),
        make_dataset("StaffStats", BoxScore.STAFF_HEADERS, staff),
        make_dataset("Officials", BoxScore.OFFICIAL_HEADERS, officials),
    ]
