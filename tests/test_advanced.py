"""Offline tests for possessions, lineups and advanced statistics (fixtures: two PLER - SZKSK matches)."""

import json

import pytest
from test_mksz_api import RAW, FakeTransport, load  # noqa: F401  (env fixture below reuses them)

from mksz_api.endpoints import BoxScoreAdvanced, LeagueDashPlayerStats, LeagueDashTeamStats
from mksz_api.endpoints.boxscore import parse_report
from mksz_api.endpoints.playbyplay import parse_events
from mksz_api.library import advanced as adv
from mksz_api.library.cache import Cache
from mksz_api.library.oncourt import build_lineups, situation
from mksz_api.library.possessions import clutch_windows, possession_log
from mksz_api.library.zones import ZONE_GROUPS, zone_group

MATCH_A, MATCH_B = "32021352123", "32021422857"  # 22-20 (2025/26) and 26-25 (2026/27)
PLER_A = 3810367232


@pytest.fixture
def env(tmp_path):
    t = FakeTransport()
    return t, {"transport": t, "cache": Cache(tmp_path / "raw")}


def match(mid):
    rows = parse_events(load(f"events_{mid}.json"), mid)
    report = {d.name: d.get_records() for d in parse_report(load(f"report_{mid}.json"))}
    gks = {p["PLAYER_ID"] for p in report["PlayerStats"] if p["GK"]}
    return rows, report, build_lineups(rows, gks)


# -- lineups -------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("mid", [MATCH_A, MATCH_B])
def test_lineups_reproduce_report_minutes(mid):
    rows, report, lineups = match(mid)
    assert lineups.has_lineups and lineups.strength_source == "lineups"
    seconds = lineups.seconds_by_player()
    for p in report["PlayerStats"]:
        assert seconds.get(p["PLAYER_ID"], 0) == p["SECONDS_PLAYED"], p["PLAYER_NAME"]
    assert sum(b - a for a, b, _ in lineups.intervals) == 3600


def test_suspension_and_empty_net_states():
    rows, _, lineups = match(MATCH_A)
    # Vuksic (HOME) is suspended 07:26-09:26: HOME is a player down
    states = [st for a, b, st in lineups.intervals if a >= 446 and b <= 566]
    assert states and all(st.HOME.suspended == 1 and situation(st, "HOME") == "DISADVANTAGE" for st in states)
    assert all(situation(st, "AWAY") == "ADVANTAGE" for st in states)
    # 24:45-25:04: PLER has swapped its goalkeeper for a 7th field player (Fekete in for Kránitz). The
    # scorer's "goalkeeper: none" event says 24:39; the lineup feed is the one that matches reality.
    st = next(st for a, b, st in lineups.intervals if a <= 1495 < b)
    assert not st.HOME.goalkeeper_on and st.HOME.field_players == 7


def test_suspensions_mode_without_lineup_feed():
    rows, report, _ = match(MATCH_A)
    no_feed = [r for r in rows if r["EVENT_TYPE"] not in ("STARTER", "SUBSTITUTION")]
    lineups = build_lineups(no_feed, set())
    assert not lineups.has_lineups and lineups.strength_source == "suspensions"
    st = next(st for a, b, st in lineups.intervals if a >= 446 and b <= 566)
    assert st.HOME.suspended == 1 and st.HOME.field_players == 5 and st.AWAY.field_players == 6


def test_corrupted_lineup_feed_is_rejected():
    rows, report, lineups = match(MATCH_A)
    assert lineups.lineup_error == 0.0 and not lineups.lineups_rejected
    # drop the substitutions of one team: its on-court set goes wrong, as in some real feeds
    broken = [r for r in rows if not (r["EVENT_TYPE"] == "SUBSTITUTION" and r["HOME_AWAY"] == "AWAY"
                                      and r["ELAPSED_SEC"] < 1800 and r["PLAYER_OUT_ID"] % 2)]
    gks = {p["PLAYER_ID"] for p in report["PlayerStats"] if p["GK"]}
    rejected = build_lineups(broken, gks)
    assert rejected.lineups_rejected and rejected.lineup_error > 0.05
    assert not rejected.has_lineups and rejected.strength_source == "suspensions" and not rejected.stints
    comp = adv.match_components(report, broken)
    assert not comp["HAS_LINEUPS"] and comp["LINEUPS_REJECTED"]
    assert all("PLUS_MINUS" not in p for p in comp["players"])


# -- possessions ---------------------------------------------------------------------------------------------

@pytest.mark.parametrize("mid,poss,goals,retained", [
    (MATCH_A, {"HOME": 48, "AWAY": 49}, {"HOME": 22, "AWAY": 20}, 11),
    (MATCH_B, {"HOME": 55, "AWAY": 55}, {"HOME": 26, "AWAY": 25}, 6),
])
def test_possession_log(mid, poss, goals, retained):
    rows, _, lineups = match(mid)
    log = possession_log(rows, lineups)
    assert {s: sum(p["OFFENSE"] == s for p in log) for s in poss} == poss
    assert {s: sum(p["GOALS"] for p in log if p["OFFENSE"] == s) for s in goals} == goals
    assert sum(p["RETAINED"] for p in log) == retained
    assert all(p["DURATION"] >= 0 and p["START_SEC"] <= p["END_SEC"] for p in log)
    # possessions alternate, apart from period starts
    for a, b in zip(log, log[1:]):
        if a["PERIOD"] == b["PERIOD"]:
            assert a["OFFENSE"] != b["OFFENSE"] or a["GOALS"], (a, b)
    assert all(p["SITUATION"] in ("EVEN", "ADVANTAGE", "DISADVANTAGE") for p in log)


def test_possessions_after_timeouts_and_clutch():
    rows, _, lineups = match(MATCH_A)
    log = possession_log(rows, lineups)
    timeouts = [r for r in rows if r["EVENT_TYPE"] == "TIMEOUT"]
    followed = [t for t in timeouts if any(p["OFFENSE"] == t["HOME_AWAY"] and p["PERIOD"] == t["PERIOD"]
                                           and p["END_SEC"] >= t["ELAPSED_SEC"] for p in log)]
    assert len(timeouts) == 6 and len(followed) == 5  # the last one (59:44) has no possession after it
    assert sum(p["AFTER_TIMEOUT"] for p in log) == len(followed)
    assert clutch_windows(rows, 3600) == [(3300, 3600)]  # margin within 2 goals all through the last 5 minutes
    assert all(p["IS_CLUTCH"] == (p["END_SEC"] >= 3300) for p in log)


@pytest.mark.parametrize("mid", [MATCH_A, MATCH_B])
def test_play_by_play_box_equals_report(mid):
    rows, report, _ = match(mid)
    _, players = adv.pbp_box(rows)
    for p in report["PlayerStats"]:
        for col in adv.BOX:
            assert players[p["PLAYER_ID"]][col] == p[col], (p["PLAYER_NAME"], col)


# -- components, rates, tables -------------------------------------------------------------------------------

def components(mid, **kw):
    rows, report, _ = match(mid)
    return adv.match_components(report, rows, **kw)


def test_team_ratings_and_four_factors():
    teams = {t["TEAM_ID"]: t for t in adv.team_table(adv.aggregate([components(MATCH_A)])["teams"], "Advanced")[1]}
    pler = teams[PLER_A]
    assert (pler["POSS"], pler["OPP_POSS"], pler["PACE"]) == (48, 49, 48.5)
    assert pler["ORTG"] == round(50 * 22 / 48, 1) and pler["DRTG"] == round(50 * 20 / 49, 1)
    assert pler["NET_RTG"] == round(pler["ORTG"] - pler["DRTG"], 1)
    assert pler["ATTEMPTS"] == 42 and pler["TOV_PCT"] == round(100 * 13 / 48, 1)  # 5 lost balls + 8 faults
    ff = adv.team_table(adv.aggregate([components(MATCH_A)])["teams"], "FourFactors")[1]
    a, b = ff
    assert a["ATT_GOAL_PCT"] == b["OPP_ATT_GOAL_PCT"] and a["TOV_PCT"] == b["OPP_TOV_PCT"]


def test_situations_add_up():
    comp = components(MATCH_A)
    sits = [s for s in comp["situations"] if s["TEAM_ID"] == PLER_A]
    by = {s["SITUATION"]: s for s in sits}
    assert sum(by[s]["SECONDS"] for s in ("EVEN", "ADVANTAGE", "DISADVANTAGE")) == 3600
    assert sum(by[s]["POSS"] for s in ("EVEN", "ADVANTAGE", "DISADVANTAGE")) == 48
    assert by["DISADVANTAGE"]["SECONDS"] == 240 and by["ADVANTAGE"]["SECONDS"] == 120  # 2 vs 1 suspensions
    # empty-net goals of the players add up to the team's empty-net goals
    players = [p for p in comp["players"] if p["TEAM_ID"] == PLER_A]
    assert sum(p["EMPTY_NET_GOALS"] for p in players) == by["EMPTY_NET"]["GOALS"] == 5


def test_game_flow_matches_score_sequence():
    rows, _, _ = match(MATCH_A)
    flow = adv.game_flow(rows, 3600)
    margins = [r["HOME_SCORE"] - r["AWAY_SCORE"] for r in rows if r["EVENT_TYPE"] in ("GOAL", "GOAL_7M")]
    leaders = [m > 0 for m in margins if m != 0]
    assert flow["HOME"]["LEAD_CHANGES"] == sum(a != b for a, b in zip(leaders, leaders[1:]))
    assert flow["HOME"]["TIMES_TIED"] == margins.count(0)
    assert flow["HOME"]["LARGEST_LEAD"] == max(margins) and flow["AWAY"]["LARGEST_LEAD"] == -min(margins)
    assert sum(flow["HOME"][k] for k in ("TIME_LEADING_SEC", "TIME_TRAILING_SEC", "TIME_TIED_SEC")) == 3600


def test_plus_minus_and_on_off():
    rows, _, lineups = match(MATCH_A)
    comp = components(MATCH_A)
    expected = 0
    for r in rows:
        if r["EVENT_TYPE"] in ("GOAL", "GOAL_7M"):
            n_on = len(lineups.at(r["EVENT_NUM"]).HOME.on)
            expected += n_on if r["HOME_AWAY"] == "HOME" else -n_on
    pler = [p for p in comp["players"] if p["TEAM_ID"] == PLER_A]
    assert sum(p["PLUS_MINUS"] for p in pler) == expected
    rows_adv = {r["PLAYER_ID"]: r for r in adv.player_table(pler, "Advanced")[1]}
    sunajko = rows_adv[57071]  # on court ~57 minutes
    assert sunajko["OFF_POSS_ON"] + sunajko["DEF_POSS_ON"] == sunajko["POSS_ON"] <= 48 + 49
    # usage over everyone on court sums to ~100% per possession, so 6-7 players average ~1/6
    assert 0 < sunajko["USG_PCT"] < 50


def test_aggregate_sums_counts_not_rates():
    one = adv.aggregate([components(MATCH_A)])
    two = adv.aggregate([components(MATCH_A), components(MATCH_A)])
    t1 = {r["TEAM_ID"]: r for r in adv.team_table(one["teams"], "Advanced")[1]}
    t2 = {r["TEAM_ID"]: r for r in adv.team_table(two["teams"], "Advanced")[1]}
    assert t2[PLER_A]["POSS"] == 2 * t1[PLER_A]["POSS"] and t2[PLER_A]["ORTG"] == t1[PLER_A]["ORTG"]
    flow = {r["TEAM_ID"]: r for r in adv.team_table(two["teams"], "GameFlow")[1]}
    assert flow[PLER_A]["LARGEST_LEAD"] == 2  # max over matches, not a sum


def test_per_modes():
    two = adv.aggregate([components(MATCH_A), components(MATCH_A)])
    get = lambda mode: {r["TEAM_ID"]: r for r in adv.team_table(two["teams"], "Base", mode)[1]}[PLER_A]  # noqa: E731
    assert get("Totals")["GOALS"] == 44 and get("PerGame")["GOALS"] == 22 and get("PerGame")["MIN"] == 60
    assert get("Per60")["GOALS"] == 22
    assert get("Per50Possessions")["GOALS"] == round(50 * 44 / 97, 2)  # basis: (POSS + OPP_POSS) / 2
    assert get("Per60")["SHOT_PCT"] == get("Totals")["SHOT_PCT"]  # rates are never scaled
    with pytest.raises(ValueError):
        adv.team_table(two["teams"], "Base", "Per100")


def test_goalkeeping():
    comp = components(MATCH_A)
    gks = {g["PLAYER_ID"]: g for g in adv.player_table(comp["goalkeepers"], "Goalkeeping")[1]}
    hollo = gks[2640]
    assert (hollo["SAVES"], hollo["SHOTS_FACED"], hollo["SAVE_PCT"]) == (10, 27, 37.0)  # = report
    assert sum(hollo[f"{g}_FACED"] for g in adv.FIELD_GROUPS) + hollo["SHOTS_FACED_7M"] == 27


def test_zone_groups_cover_all_zones():
    zones = [z for g in ZONE_GROUPS.values() for z in g]
    assert sorted(zones) == list(range(18)) and zone_group(None) == "UNKNOWN" and zone_group(17) == "SEVEN_M"


# -- endpoints -----------------------------------------------------------------------------------------------

def test_boxscore_advanced(env):
    _, kw = env
    b = BoxScoreAdvanced(MATCH_A, **kw)
    assert [d.name for d in b.data_sets] == [
        "TeamAdvanced", "ShotProfile", "GameFlow", "Situations", "PlayerAdvanced", "Goalkeeping", "Possessions"]
    team = {r["TEAM_ID"]: r for r in b.team_advanced.get_records()}
    assert team[PLER_A]["POSS"] == 48 and team[PLER_A]["HAS_LINEUPS"] is True
    assert len(b.possessions) == 97 and len(b.situations) == 8


def test_boxscore_advanced_clutch(env):
    _, kw = env
    b = BoxScoreAdvanced(MATCH_A, clutch=True, **kw)
    team = {r["TEAM_ID"]: r for r in b.team_advanced.get_records()}
    assert team[PLER_A]["MIN"] == 5.0
    clutch_poss = sum(p["IS_CLUTCH"] for p in possession_log(*match(MATCH_A)[::2]) if p["OFFENSE"] == "HOME")
    assert team[PLER_A]["POSS"] == clutch_poss
    players = b.player_advanced.get_records()
    assert all(p["MIN"] <= 5.0 for p in players)
    assert sum(p["CLUTCH_GOALS"] or 0 for p in players if p["TEAM_ID"] == PLER_A) == team[PLER_A]["POSS"] * team[PLER_A]["ATTACK_EFF_PCT"] / 100


def test_league_dash_without_livescore_log(env):
    t, kw = env
    t.pages = {k: v for k, v in t.pages.items()}
    t.pages["livescore.mksz.hu/32021422857/"] = {"events": []}
    dash = LeagueDashTeamStats(match_ids=[MATCH_A, MATCH_B], measure_type="Advanced", **kw)
    rows = dash.league_dash_team_stats.get_records()
    assert len(rows) == 4  # two seasons: teams get new ids every season
    no_log = [r for r in rows if r["PBP_GAMES"] == 0]
    assert len(no_log) == 2 and all(r["POSS"] is None and r["ORTG"] is None for r in no_log)
    assert [m["MATCH_ID"] for m in dash.missing_matches.get_records()] == [MATCH_B]


def test_league_dash_measures_and_filters(env):
    _, kw = env
    sit = LeagueDashTeamStats(match_ids=[MATCH_A], measure_type="Situations", team_id=PLER_A, **kw)
    assert [r["SITUATION"] for r in sit.league_dash_team_stats.get_records()] == list(adv.SITUATIONS)
    players = LeagueDashPlayerStats(match_ids=[MATCH_A], measure_type="Base", per_mode="Per60", min_minutes=30, **kw)
    recs = players.league_dash_player_stats.get_records()
    assert recs and all(r["MIN"] >= 30 for r in recs)
    ocvirk = LeagueDashPlayerStats(match_ids=[MATCH_A], player_id=313252, **kw).league_dash_player_stats.get_records()
    assert len(ocvirk) == 1 and ocvirk[0]["GOALS"] == 6
    with pytest.raises(ValueError):
        LeagueDashPlayerStats(match_ids=[MATCH_A], measure_type="FourFactors", **kw)
    with pytest.raises(ValueError):
        LeagueDashTeamStats(season=2025, **kw)  # no league / competition / stage
