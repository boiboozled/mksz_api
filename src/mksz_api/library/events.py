"""Play-by-play event categories shared by the shot chart, possession and advanced-stats code.

Keys are PlayByPlay EVENT_TYPE values (see mksz_api.endpoints.playbyplay.EVENT_TYPES).
"""

from __future__ import annotations

# shot EVENT_TYPE -> (outcome, is 7 m throw)
SHOT_EVENTS: dict[str, tuple[str, bool]] = {
    "GOAL": ("GOAL", False),
    "GOAL_7M": ("GOAL", True),
    "SHOT_SAVED": ("SAVED", False),
    "SHOT_7M_SAVED": ("SAVED", True),
    "SHOT_MISSED": ("MISSED", False),
    "SHOT_7M_MISSED": ("MISSED", True),
    "SHOT_POST": ("POST", False),
    "SHOT_7M_POST": ("POST", True),
    "SHOT_BLOCKED": ("BLOCKED", False),
}
SHOT_OUTCOMES = ["GOAL", "SAVED", "MISSED", "POST", "BLOCKED"]

# events that lose the ball without a shot
TURNOVER_EVENTS: dict[str, str] = {"TURNOVER": "TURNOVER", "TECHNICAL_FAULT": "TECHNICAL_FAULT"}

# events that can end a possession: every shot outcome plus turnovers
TERMINAL_EVENTS: set[str] = set(SHOT_EVENTS) | set(TURNOVER_EVENTS)

# outcomes after which the attacking team can keep the ball (goal always hands it over)
LIVE_BALL_OUTCOMES = {"SAVED", "MISSED", "POST", "BLOCKED"}

# EVENT_TYPE -> report columns it counts towards, so the play-by-play can rebuild the official box score
BOX_COLUMNS: dict[str, tuple[str, ...]] = {
    "GOAL": ("GOALS", "SHOTS"),
    "GOAL_7M": ("GOALS", "SHOTS", "GOALS_7M", "SHOTS_7M"),
    "SHOT_SAVED": ("SHOTS", "SHOTS_SAVED"),
    "SHOT_7M_SAVED": ("SHOTS", "SHOTS_7M", "SHOTS_SAVED"),
    "SHOT_MISSED": ("SHOTS", "SHOTS_MISSED"),
    "SHOT_7M_MISSED": ("SHOTS", "SHOTS_7M", "SHOTS_MISSED"),
    "SHOT_POST": ("SHOTS", "SHOTS_POST"),
    "SHOT_7M_POST": ("SHOTS", "SHOTS_7M", "SHOTS_POST"),
    "SHOT_BLOCKED": ("BLOCKED",),  # the report leaves blocked shots out of SHOTS
    "TURNOVER": ("TURNOVERS",),
    "TECHNICAL_FAULT": ("TECHNICAL_FAULTS",),
    "TWO_MINUTES": ("TWO_MINUTES",),
    "YELLOW_CARD": ("YELLOW_CARDS",),
    "RED_CARD": ("RED_CARDS",),
    "BLUE_CARD": ("BLUE_CARDS",),
}

OTHER_SIDE = {"HOME": "AWAY", "AWAY": "HOME"}
