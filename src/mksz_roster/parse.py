"""Parse the rendered "Játékos statisztikák" tab of an MKSZ livescore page.

The page shows one team's table at a time (the active `.team-tab`); `parse_active_team`
reads that one, `parse_header` reads the two team names and the scores. Both work on plain
HTML, so they are tested offline against a saved page (tests/fixtures/).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

from bs4 import BeautifulSoup


@dataclass
class Player:
    number: str
    name: str
    gk: bool = False
    time_on_court: str | None = None  # "mm:ss" as the page shows it, None when empty


@dataclass
class Team:
    name: str
    players: list[Player] = field(default_factory=list)


def _text(el) -> str:
    return " ".join(el.get_text(" ", strip=True).split()) if el else ""


def parse_header(html: str) -> dict:
    """{"teams": [left, right], "score": [left, right], "half_time": [left, right]}."""
    soup = BeautifulSoup(html, "html.parser")
    headers = soup.select("#header .team-header")
    names = [_text(h.select_one(".team-name")) for h in headers]
    score = [_text(h.select_one(".sum-score")) for h in headers]
    half = [_text(h.select_one(".half-score")) for h in headers]
    return {"teams": names, "score": score, "half_time": half}


def active_team_name(html: str) -> str | None:
    tab = BeautifulSoup(html, "html.parser").select_one(".team-tab .team-inner.active")
    return _text(tab) or None


def parse_active_team(html: str) -> Team:
    """The roster table currently shown: goalkeepers (marked "K") then field players."""
    soup = BeautifulSoup(html, "html.parser")
    team = Team(name=active_team_name(html) or "")
    for section, is_gk in ((".goalkeepers", True), (".players", False)):
        for row in soup.select(f".main-container {section} .table-row"):
            number = _text(row.select_one(".player-jersey"))
            name = _text(row.select_one(".player-name"))
            if not number or not name:
                continue
            gk = is_gk or _text(row.select_one(".player-goalkeeper")) == "K"
            cells = [_text(c) for c in row.select(".nowrap")]
            time_on_court = cells[-1] if cells and ":" in cells[-1] else None
            team.players.append(Player(number=number, name=name, gk=gk, time_on_court=time_on_court))
    return team


def to_lineup_json(match_id: str, header: dict, teams: list[Team]) -> dict:
    """The lineup format of the handball-3d-analysis pipeline (lineups/<video>.json):
    {"teams": [{"name", "players": [{"number", "name", "gk"?}]}]}, plus match metadata."""
    return {
        "_source": f"https://livescore.mksz.hu/{match_id}/jatekos_statisztikak",
        "match_id": match_id,
        "score": dict(zip(header.get("teams", []), header.get("score", []))),
        "teams": [
            {
                "name": t.name,
                "players": [
                    {k: v for k, v in asdict(p).items() if v not in (None, False) or k == "number"}
                    for p in sorted(t.players, key=lambda p: (not p.gk, int(p.number) if p.number.isdigit() else 999))
                ],
            }
            for t in teams
        ],
    }
