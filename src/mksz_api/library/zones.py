"""Shooting zones and goal cells, as defined by the livescore site's shot statistics.

Court zones (the scorer's `selectedArea`) are 0-17. Positions are where the site draws each zone's
bubble on its half-court picture: the goal is at the top, and the picture is seen from behind the
attackers, so "right" is the attacker's right. COURT_X runs 0 (left) to 1 (right) and COURT_Y 0 (goal
line) to 1 (half-way line). They are taken from the site's layout (378 x 334 px), for plotting.

Goal cells (`selectedGateArea`) are 1-9 in a 3x3 grid, numbered row by row from the top-left,
as drawn on the site: the goal seen from the shooter's side.
"""

from __future__ import annotations

from dataclasses import dataclass

_FIELD_HEIGHT_PX = 334


@dataclass(frozen=True)
class Zone:
    id: int
    name: str  # English
    name_hu: str  # the site's label
    x: float
    y: float


def _z(zid: int, name: str, name_hu: str, left_pct: float, top_px: float) -> Zone:
    return Zone(zid, name, name_hu, round(left_pct / 100, 3), round(top_px / _FIELD_HEIGHT_PX, 3))


COURT_ZONES: dict[int, Zone] = {z.id: z for z in [
    _z(0, "Right wing", "Jobbszélső", 90, 20),
    _z(1, "Right back", "Jobb közép", 80, 125),
    _z(2, "Centre back", "Közép", 50, 150),
    _z(3, "Left back", "Bal közép", 20, 125),
    _z(4, "Left wing", "Balszélső", 10, 20),
    _z(5, "Right back, long range", "Jobb távol", 85, 250),
    _z(6, "Centre, long range", "Közép távol", 50, 220),
    _z(7, "Left back, long range", "Bal távol", 15, 250),
    _z(8, "Right pivot", "Jobb beálló", 81, 40),
    _z(9, "Centre pivot", "Közép beálló", 40, 95),
    _z(10, "Left pivot", "Bal beálló", 19, 40),
    _z(11, "Right breakthrough", "Jobb betörés", 75, 65),
    _z(12, "Centre breakthrough", "Közép betörés", 60, 95),
    _z(13, "Left breakthrough", "Bal betörés", 25, 65),
    _z(14, "Kínai (trick shot)", "Kínai", 40, 30),
    _z(15, "Fast break", "Gyorsindítás", 60, 30),
    _z(16, "Own half", "Saját térfél", 50, 290),
    _z(17, "7 m throw", "7 méteres", 50, 110),
]}
SEVEN_METRE_ZONE = 17

GOAL_ROWS = {1: "TOP", 2: "TOP", 3: "TOP", 4: "MIDDLE", 5: "MIDDLE", 6: "MIDDLE", 7: "BOTTOM", 8: "BOTTOM", 9: "BOTTOM"}
GOAL_SIDES = {1: "LEFT", 4: "LEFT", 7: "LEFT", 2: "CENTRE", 5: "CENTRE", 8: "CENTRE", 3: "RIGHT", 6: "RIGHT", 9: "RIGHT"}


def court_zone(value) -> int | None:
    """The scorer's selectedArea ('0'..'17', int or None) as an int zone id, or None when unrecorded."""
    if value is None or value == "":
        return None
    try:
        zone = int(value)
    except (TypeError, ValueError):
        return None
    return zone if zone in COURT_ZONES else None


def goal_cell(value) -> int | None:
    """The scorer's selectedGateArea (1..9; 0/None = not recorded) as an int, or None."""
    try:
        cell = int(value)
    except (TypeError, ValueError):
        return None
    return cell if cell in GOAL_ROWS else None
