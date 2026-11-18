"""Which page to open for which data, and which of its JSON responses to keep.

Everything site-specific about page URLs, response URL patterns and page-control scripts lives here.
"""

from __future__ import annotations

import json
import re

from mksz_api.library.parameters import season_label
from mksz_api.library.transport import Capture, PageRequest

MKSZ = "https://mksz.hu"
LIVESCORE = "https://livescore.mksz.hu"
API = r"/public/api/v1/"

# Finds the component holding the program's date range on a /versenyek/... page.
_PROGRAM_BLOCK = """(() => { let el = document.querySelector('input.datepicker');
  while (el && !el.__vue__) el = el.parentElement;
  for (let v = el && el.__vue__; v; v = v.$parent) if (v.$options.name === 'Program_eredmenyek_blokk') return v;
  return null; })()"""


def seasons() -> PageRequest:
    return PageRequest(f"{MKSZ}/minden-kezilabda", [Capture("evad", API + r"evad$", "evad")])


def competitions(year: int) -> PageRequest:
    """The 'minden kézilabda' page lists adult competitions of both genders for the selected season."""
    label = json.dumps(season_label(year))
    script = f"""() => {{ const s = document.querySelector('select'); if (!s) return false;
      const o = [...s.options].find(o => o.text.trim() === {label}); if (!o) return false;
      if (s.value !== o.value) {{ s.value = o.value; s.dispatchEvent(new Event('change', {{bubbles: true}})); }}
      return true; }}"""
    captures = [
        Capture(g, API + rf"versenyek/foversenyek\?(?=.*evadEv\.equals={year}\b)(?=.*nem\.in={g}\b)", "versenyek/foversenyek")
        for g in ("F", "N")
    ]
    return PageRequest(f"{MKSZ}/minden-kezilabda", captures, script=script)


def _versenyek_url(gender: str, competition_id: int, stage_id: int) -> str:
    category = "felnottNoiVersenyek" if gender == "N" else "felnottFerfiVersenyek"
    return f"{MKSZ}/versenyek/{category}/{competition_id}/{stage_id}"


def program(gender: str, competition_id: int, stage_id: int, year: int) -> PageRequest:
    """All matches of a stage in a season: set the page's program date range to the whole season
    (1 June - 31 July next year, generous on both ends) so the page requests the full program."""
    start, end = f"{year}.06.01.", f"{year + 1}.07.31."
    script = f"""() => {{ const v = {_PROGRAM_BLOCK}; if (!v) return false;
      if (v.startDate !== '{start}' || v.endDate !== '{end}') {{ v.startDate = '{start}'; v.endDate = '{end}'; }}
      return true; }}"""
    # The page subtracts 3 h from the start date, so the query starts on the evening of 31 May.
    pattern = API + rf"versenyek/program\?(?=.*merkozesDatum\.greaterThanOrEqual={year}-05-31T)(?=.*alversenyId\.equals={stage_id}\b)"
    return PageRequest(
        _versenyek_url(gender, competition_id, stage_id),
        [Capture("program", pattern, "versenyek/program")],
        script=script,
        timeout=60,
    )


def standings(gender: str, competition_id: int, stage_id: int) -> PageRequest:
    return PageRequest(
        _versenyek_url(gender, competition_id, stage_id),
        [Capture("tabella", API + rf"versenyek/tabella/{stage_id}$", f"versenyek/tabella/{stage_id}")],
    )


def match_report(match_id: str) -> PageRequest:
    """The official match report (jegyzőkönyv): rosters with full per-player stats, staff, officials."""
    return PageRequest(
        f"{MKSZ}/jegyzokonyv/{match_id}",
        [Capture("report", API + rf"versenyek/{match_id}/nyomtatasi-adatok$", f"versenyek/{match_id}/nyomtatasi-adatok")],
    )


def match_events(match_id: str) -> PageRequest:
    """The livescore event log (every shot, save, turnover, card, timeout and substitution)."""
    return PageRequest(
        f"{LIVESCORE}/{match_id}/esemenyek",
        [Capture("events", API + rf"versenyek/digital-report/esemeny/{match_id}$", f"versenyek/digital-report/esemeny/{match_id}")],
    )


def is_match_id(value: str | int) -> bool:
    return re.fullmatch(r"\d{6,}", str(value)) is not None
