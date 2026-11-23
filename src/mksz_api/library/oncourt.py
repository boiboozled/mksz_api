"""Who is on court, rebuilt from the play-by-play.

The livescore log has a lineup feed next to the scorer's events: STARTER (7 per team at the start of each
half), SUBSTITUTION (player in / player out), SUSPENSION_LEAVE / SUSPENSION_RETURN. Replaying it gives each
player's stints on court; for NB I matches the rebuilt seconds equal the report's playing time exactly.

Active suspensions are counted for 120 s from each SUSPENSION_LEAVE event (or TWO_MINUTES event when the
feed has none), so repeated or mislabelled return events do not matter.

Numerical strength (suspensions) and empty net (no goalkeeper on court, i.e. a 7th field player) come from:
- lineups mode: the on-court sets, with goalkeepers taken from the report's GK flag;
- suspensions mode (no lineup feed, e.g. NB II): 6 field players minus active suspensions, and
  empty net from the scorer's goalkeeper changes (goalkeeper set to none).

All times are cumulative game seconds (ELAPSED_SEC): 0-1800 first half, 1800-3600 second half, overtime after.
"""

from __future__ import annotations

from dataclasses import dataclass, field

SUSPENSION_SECONDS = 120
FIELD_PLAYERS = 6
# A lineup feed is used only if a team has the wrong number of players on court (7 minus suspensions)
# for at most this share of the time; some feeds are corrupted (repeated starters, substitutions of
# players who are not on court), and then the match falls back to suspensions mode.
MAX_LINEUP_ERROR = 0.05
SIDES = ("HOME", "AWAY")


@dataclass(frozen=True)
class TeamState:
    on: frozenset  # player ids on court (empty in suspensions mode)
    goalkeeper_on: bool  # False = empty net (a 7th field player instead of the goalkeeper)
    field_players: int  # players on court other than a goalkeeper
    suspended: int  # active suspensions


@dataclass(frozen=True)
class State:
    HOME: TeamState
    AWAY: TeamState

    def team(self, side: str) -> TeamState:
        return self.HOME if side == "HOME" else self.AWAY


@dataclass
class Lineups:
    has_lineups: bool
    strength_source: str  # "lineups" or "suspensions"
    game_end: int
    lineup_error: float | None = None  # share of team-time with an impossible number of players on court
    lineups_rejected: bool = False  # the feed existed but failed the MAX_LINEUP_ERROR check
    stints: list[dict] = field(default_factory=list)  # {"HOME_AWAY", "PLAYER_ID", "START", "END"}
    intervals: list[tuple[int, int, State]] = field(default_factory=list)  # (start, end, state), covering the game
    states: dict[int, State] = field(default_factory=dict)  # EVENT_NUM -> state when the event happened

    def at(self, event_num: int) -> State | None:
        return self.states.get(event_num)

    def seconds_on(self, player_id) -> int:
        return sum(s["END"] - s["START"] for s in self.stints if s["PLAYER_ID"] == player_id)

    def seconds_by_player(self) -> dict:
        out: dict = {}
        for s in self.stints:
            out[s["PLAYER_ID"]] = out.get(s["PLAYER_ID"], 0) + s["END"] - s["START"]
        return out

    def overlap_seconds(self, player_id, windows: list[tuple[int, int]]) -> int:
        """Seconds the player spent on court inside the given (start, end) windows."""
        total = 0
        for s in self.stints:
            if s["PLAYER_ID"] != player_id:
                continue
            for a, b in windows:
                total += max(0, min(b, s["END"]) - max(a, s["START"]))
        return total


def game_end_seconds(rows: list[dict]) -> int:
    """Regulation is 3600 s; overtime periods are 5 minutes, so round the last event up to a 300 s mark."""
    last = max((r["ELAPSED_SEC"] or 0 for r in rows), default=0)
    if last <= 3600:
        return 3600
    return -(-last // 300) * 300


def build_lineups(rows: list[dict], goalkeepers: set, game_end: int | None = None,
                  use_lineups: bool = True) -> Lineups:
    """Replay the lineup feed of one match's PlayByPlay rows (in EVENT_NUM order).

    Falls back to suspensions mode when there is no lineup feed, or when the feed is inconsistent
    (see MAX_LINEUP_ERROR; `lineup_error` and `lineups_rejected` say what happened)."""
    game_end = game_end or game_end_seconds(rows)
    starters = {side: sum(1 for r in rows if r["EVENT_TYPE"] == "STARTER" and r["HOME_AWAY"] == side) for side in SIDES}
    has_lineups = use_lineups and all(n >= FIELD_PLAYERS for n in starters.values())
    has_leave_events = any(r["EVENT_TYPE"] == "SUSPENSION_LEAVE" for r in rows)
    out = Lineups(has_lineups, "lineups" if has_lineups else "suspensions", game_end)

    on: dict[str, set] = {s: set() for s in SIDES}
    since: dict[tuple[str, object], int] = {}
    starter_period: dict[str, str | None] = {s: None for s in SIDES}
    # (player, start, end) of each suspension. A suspension always lasts 120 s, so it is counted from the
    # leave event (or the TWO_MINUTES event when the feed has no leave events) for 120 s. Return events only
    # put a player back on court: the log sometimes repeats a leave or has the return under another player.
    suspensions: dict[str, list[tuple[object, int, int]]] = {s: [] for s in SIDES}
    gk_empty: dict[str, bool] = {s: False for s in SIDES}  # fallback mode

    def leave(side: str, pid, t: int) -> None:
        if pid in on[side]:
            on[side].discard(pid)
            out.stints.append({"HOME_AWAY": side, "PLAYER_ID": pid, "START": since.pop((side, pid)), "END": t})

    def enter(side: str, pid, t: int) -> None:
        if pid is not None and pid not in on[side]:
            on[side].add(pid)
            since[(side, pid)] = t

    def snapshot(t: int) -> State:
        teams = {}
        for side in SIDES:
            if has_lineups:
                gk_on = bool(on[side] & goalkeepers)
                field_players = len(on[side]) - (1 if gk_on else 0)
            else:
                gk_on = not gk_empty[side]
                field_players = FIELD_PLAYERS - _active(side, t) + (0 if gk_on else 1)
            teams[side] = TeamState(frozenset(on[side]), gk_on, field_players, _active(side, t))
        return State(**teams)

    def _active(side: str, t: int) -> int:
        return sum(1 for _, start, end in suspensions[side] if start <= t < end)

    def suspend(side: str, pid, t: int) -> None:
        if any(p == pid and t - start < 10 for p, start, _ in suspensions[side]):
            return  # the same suspension logged twice
        suspensions[side].append((pid, t, t + SUSPENSION_SECONDS))

    last_t = 0
    for r in rows:
        t = r["ELAPSED_SEC"]
        if t is None:  # untimed event (rare): it happened "now", so give it the current state
            out.states[r["EVENT_NUM"]] = snapshot(last_t)
            continue
        if t > last_t:
            out.intervals.append((last_t, t, snapshot(last_t)))
            last_t = t
        out.states[r["EVENT_NUM"]] = snapshot(t)
        et, side = r["EVENT_TYPE"], r["HOME_AWAY"]
        if side not in SIDES:
            continue
        if not has_lineups and et in ("STARTER", "SUBSTITUTION", "SUSPENSION_RETURN"):
            continue  # suspensions mode ignores the lineup feed (absent, or rejected as inconsistent)
        if not has_lineups and et == "SUSPENSION_LEAVE":
            suspend(side, r["PLAYER_OUT_ID"], t)
        elif et == "STARTER":
            if starter_period[side] != r["PERIOD"]:  # first starter of a half: the previous lineup leaves
                for pid in list(on[side]):
                    leave(side, pid, t)
                starter_period[side] = r["PERIOD"]
            enter(side, r["PLAYER_IN_ID"], t)
        elif et == "SUBSTITUTION":
            leave(side, r["PLAYER_OUT_ID"], t)
            enter(side, r["PLAYER_IN_ID"], t)
        elif et == "SUSPENSION_LEAVE":
            leave(side, r["PLAYER_OUT_ID"], t)
            suspend(side, r["PLAYER_OUT_ID"], t)
        elif et == "SUSPENSION_RETURN":
            enter(side, r["PLAYER_IN_ID"], t)
        elif et == "TWO_MINUTES" and not has_leave_events:
            suspend(side, r["PLAYER_ID"], t)
        elif et == "GOALKEEPER_CHANGE":
            gk_empty[side] = not r["PLAYER_ID"]
    if game_end > last_t:
        out.intervals.append((last_t, game_end, snapshot(last_t)))
    for side in SIDES:
        for pid in list(on[side]):
            leave(side, pid, max(game_end, last_t))
    out.stints.sort(key=lambda s: (s["HOME_AWAY"], s["START"], str(s["PLAYER_ID"])))
    if has_lineups:
        wrong = sum(b - a for a, b, st in out.intervals for side in SIDES
                    if len(st.team(side).on) != FIELD_PLAYERS + 1 - st.team(side).suspended)
        total = 2 * sum(b - a for a, b, _ in out.intervals)
        out.lineup_error = round(wrong / total, 4) if total else 0.0
        if out.lineup_error > MAX_LINEUP_ERROR:
            fallback = build_lineups(rows, goalkeepers, game_end, use_lineups=False)
            fallback.lineup_error, fallback.lineups_rejected = out.lineup_error, True
            return fallback
    return out


def situation(state: State, side: str) -> str:
    """EVEN / ADVANTAGE / DISADVANTAGE for `side`, from active suspensions (not from an empty net)."""
    own, opp = state.team(side).suspended, state.team("AWAY" if side == "HOME" else "HOME").suspended
    if own == opp:
        return "EVEN"
    return "ADVANTAGE" if own < opp else "DISADVANTAGE"
