"""Shared scope handling for endpoints that work on many matches (ShotChart, LeagueDash*).

A scope is either explicit match ids, or a season plus league / competition_id / stage_id (resolved with
MatchFinder, played matches only, optionally only those of some teams)."""

from __future__ import annotations

from typing import Any

from mksz_api.endpoints._base import NoDataError
from mksz_api.endpoints.discovery import MatchFinder

SCOPE_HELP = "give match_id / match_ids, or season with league / competition_id / stage_id"


def scope_args(match_id=None, match_ids=None, season=None, league=None, gender=None, competition_id=None,
               stage_id=None) -> tuple[list[str], dict[str, Any]]:
    ids = [str(m) for m in ([match_id] if match_id is not None else []) + list(match_ids or [])]
    scope = {"season": season, "league": league, "gender": gender, "competition_id": competition_id,
             "stage_id": stage_id}
    if not ids and league is None and competition_id is None and stage_id is None:
        raise ValueError(SCOPE_HELP)
    return list(dict.fromkeys(ids)), scope


def matches_in_scope(match_ids: list[str], scope: dict, team_ids: set | None = None, **endpoint_kwargs) -> list[dict]:
    """[{"MATCH_ID", "MATCH_DATE"}] of the scope: the given ids, or the season's played matches."""
    if match_ids:
        return [{"MATCH_ID": m, "MATCH_DATE": None} for m in match_ids]
    finder = MatchFinder(**scope, **endpoint_kwargs)
    rows = [r for r in finder.matches.get_records() if r["HOME_SCORE"] is not None]  # played only
    if team_ids:
        rows = [r for r in rows if {r["HOME_TEAM_ID"], r["AWAY_TEAM_ID"]} & team_ids]
    if not rows:
        raise NoDataError(f"no played matches for {scope}")
    return [{"MATCH_ID": r["MATCH_ID"], "MATCH_DATE": r["MATCH_DATE"]} for r in rows]
