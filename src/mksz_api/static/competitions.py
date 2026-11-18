"""Competition lookups, in the spirit of nba_api.static.teams.

Unlike NBA teams, competitions change every season, so these read CompetitionFinder (cached for a day,
forever for past seasons) instead of a bundled list.

    from mksz_api.static import competitions
    competitions.find_competition_by_name("NB I", gender="F", season="2026/27")
"""

from __future__ import annotations

import re
import unicodedata

from mksz_api.endpoints.discovery import CompetitionFinder


def _norm(text: str | None) -> str:
    text = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def get_competitions(season=None, gender=None, league=None, **kwargs) -> list[dict]:
    """One dict per competition with its stages: {"id", "name", "league", "gender", "season", "stages": [...]}."""
    rows = CompetitionFinder(season=season, gender=gender, league=league, **kwargs).competitions.get_records()
    comps: dict[int, dict] = {}
    for r in rows:
        c = comps.setdefault(r["COMPETITION_ID"], {
            "id": r["COMPETITION_ID"], "name": r["COMPETITION_NAME"], "league": r["LEAGUE_ID"],
            "gender": r["GENDER"], "season": r["SEASON"], "season_id": r["SEASON_ID"],
            "event": r["EVENT_NAME"], "stages": [],
        })
        if r["STAGE_ID"] is not None:
            c["stages"].append({"id": r["STAGE_ID"], "name": r["STAGE_NAME"], "order": r["STAGE_ORDER"]})
    return list(comps.values())


def find_competitions_by_name(pattern: str, season=None, gender=None, **kwargs) -> list[dict]:
    """Competitions whose name contains `pattern` (accent- and case-insensitive)."""
    needle = _norm(pattern)
    return [c for c in get_competitions(season, gender, **kwargs) if needle in _norm(c["name"])]


def find_competition_by_name(name: str, season=None, gender=None, **kwargs) -> dict | None:
    """The competition named exactly `name` (accent/case-insensitive, gender suffix optional, so
    "NB I" with gender="F" finds "NB I Férfi"), else the single partial match, else None."""
    comps = get_competitions(season, gender, **kwargs)
    needle = _norm(name)
    for c in comps:
        base = re.sub(r"\s+(ferfi|noi)$", "", _norm(c["name"]))
        if needle in (_norm(c["name"]), base):
            return c
    partial = [c for c in comps if needle in _norm(c["name"])]
    return partial[0] if len(partial) == 1 else None


def find_competition_by_id(competition_id: int, season=None, **kwargs) -> dict | None:
    return next((c for c in get_competitions(season, **kwargs) if c["id"] == int(competition_id)), None)
