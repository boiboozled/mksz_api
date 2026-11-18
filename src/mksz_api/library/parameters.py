"""Parameter parsing shared by the endpoints (seasons, genders, leagues)."""

from __future__ import annotations

import re
from datetime import date

# globalisVersenyKod values seen on mksz.hu, the closest thing to nba_api's league_id.
LEAGUES = {
    "NBI": "Nemzeti Bajnokság I.",
    "NBIB": "Nemzeti Bajnokság I/B",
    "NBII": "Nemzeti Bajnokság II.",
    "MAGYAR_KUPA": "Magyar Kupa",
    "SZUPERKUPA": "Szuperkupa",
    "EDZOMERKOZES": "Edzőmérkőzések",
    "SZERV_EGYEB_VERSENY": "Egyéb (megyei/budapesti) versenyek",
}

_GENDERS = {
    "f": "F", "m": "F", "men": "F", "male": "F", "ferfi": "F", "férfi": "F",
    "n": "N", "w": "N", "women": "N", "female": "N", "noi": "N", "női": "N",
}


def current_season_year(today: date | None = None) -> int:
    """MKSZ seasons run 1 July - 30 June and are identified by their starting year."""
    today = today or date.today()
    return today.year if today.month >= 7 else today.year - 1


def season_year(season: int | str | None) -> int:
    """2026, "2026", "2026/27", "2026-27", "2026/2027" -> 2026. None -> the current season."""
    if season is None:
        return current_season_year()
    if isinstance(season, int):
        return season
    m = re.fullmatch(r"\s*(\d{4})(?:\s*[/-]\s*(\d{2}|\d{4}))?\s*", str(season))
    if not m:
        raise ValueError(f"unrecognised season {season!r}; use e.g. 2026 or '2026/27'")
    start = int(m.group(1))
    if m.group(2) and int(m.group(2)[-2:]) != (start + 1) % 100:
        raise ValueError(f"season {season!r} does not span consecutive years")
    return start


def season_label(year: int) -> str:
    """2026 -> "2026/2027", the label the site uses."""
    return f"{year}/{year + 1}"


def gender_code(gender: str | None) -> str | None:
    """"F" (férfi, men) or "N" (női, women); accepts a few English/Hungarian spellings. None stays None."""
    if gender is None:
        return None
    code = _GENDERS.get(str(gender).strip().lower())
    if not code:
        raise ValueError(f"unrecognised gender {gender!r}; use 'F' (men) or 'N' (women)")
    return code


def league_code(league: str | None) -> str | None:
    """Normalise a league id: "NB I" / "nbi" -> "NBI", "NB I/B" -> "NBIB". Unknown codes pass through upper-cased."""
    if league is None:
        return None
    return re.sub(r"[\s/.]", "", str(league)).upper()
