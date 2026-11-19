"""Offline tests for mksz_api: endpoints run against captured site payloads via a fake transport.

Expected values come from the livescore page of match 32021352123 (PLER-Budapest - Szigetszentmiklósi KSK,
22-20, 2025-12-11) as shown in the browser.
"""

import json
from pathlib import Path

import pytest

from mksz_api.endpoints import BoxScore, CompetitionFinder, MatchFinder, NoDataError, PlayByPlay, Seasons, Standings
from mksz_api.endpoints.boxscore import parse_report_date
from mksz_api.library import pages
from mksz_api.library.cache import Cache
from mksz_api.library.parameters import gender_code, league_code, season_label, season_year
from mksz_api.static import competitions

RAW = Path(__file__).parent / "fixtures" / "raw"


def load(name):
    return json.loads((RAW / name).read_text(encoding="utf-8"))


class FakeTransport:
    """Serves fixture payloads by page URL; records which pages were 'loaded'."""

    def __init__(self):
        self.calls = []
        self.pages = {
            "/minden-kezilabda": {"evad": load("evad.json"), "F": load("foversenyek_2026_F.json"), "N": load("foversenyek_2026_N.json")},
            "/versenyek/felnottFerfiVersenyek/32027115/320228793": {
                "program": load("program_320228793_window.json"), "tabella": load("tabella_320228793.json")},
            "/jegyzokonyv/32021352123": {"report": load("report_32021352123.json")},
            "/jegyzokonyv/32021422857": {"report": load("report_32021422857.json")},
            "livescore.mksz.hu/32021352123/": {"events": load("events_32021352123.json")},
            "livescore.mksz.hu/32021422857/": {"events": load("events_32021422857.json")},
            "livescore.mksz.hu/1/": {"events": []},
        }

    def fetch(self, request):
        self.calls.append(request.url)
        for key, payloads in self.pages.items():
            if key in request.url:
                return {c.name: payloads[c.name] for c in request.captures}
        raise AssertionError(f"unexpected page {request.url}")


@pytest.fixture
def env(tmp_path):
    t = FakeTransport()
    return t, {"transport": t, "cache": Cache(tmp_path / "raw")}


# -- parameters ---------------------------------------------------------------------------------------------

@pytest.mark.parametrize("given", [2026, "2026", "2026/27", "2026-27", "2026/2027"])
def test_season_year(given):
    assert season_year(given) == 2026
    assert season_label(2026) == "2026/2027"


def test_bad_season():
    with pytest.raises(ValueError):
        season_year("2026/29")


def test_gender_and_league_codes():
    assert gender_code("men") == "F" and gender_code("N") == "N" and gender_code("női") == "N"
    assert league_code("NB I") == "NBI" and league_code("nb i/b") == "NBIB"


def test_report_date():
    assert parse_report_date("2025. 12. 11. - 18:00") == "2025-12-11T18:00"


# -- discovery ----------------------------------------------------------------------------------------------

def test_seasons(env):
    _, kw = env
    df = Seasons(**kw).seasons.get_data_frame()
    assert df.iloc[0]["SEASON_ID"] >= 2026
    assert "2025/2026" in set(df["SEASON"])


def test_competition_finder_filters(env):
    _, kw = env
    rows = CompetitionFinder(season="2026/27", gender="F", league="NBI", **kw).competitions.get_records()
    assert [(r["COMPETITION_ID"], r["STAGE_ID"], r["STAGE_NAME"]) for r in rows] == [(32027115, 320228793, "K&H Férfi Liga")]
    nb2 = CompetitionFinder(season=2026, gender="F", league="NBII", **kw).competitions.get_records()
    assert len(nb2) == 4 and all(r["COMPETITION_NAME"] == "NB II Férfi" for r in nb2)  # four groups


def test_static_competitions(env):
    _, kw = env
    c = competitions.find_competition_by_name("NB I", gender="F", season="2026/27", **kw)
    assert c["id"] == 32027115 and c["league"] == "NBI" and c["stages"][0]["id"] == 320228793
    women = competitions.find_competition_by_name("nb i noi", season=2026, **kw)
    assert women["id"] == 32027118


def test_empty_cached_competitions_are_refetched(env):
    # Older versions could cache {"F": [], "N": []} for a past season and then never reload it.
    t, kw = env
    kw["cache"].put("competitions", 2026, {"F": [], "N": []})
    c = competitions.find_competition_by_name("NB I", gender="F", season="2026/27", **kw)
    assert c["id"] == 32027115 and len(t.calls) == 1
    assert any(kw["cache"].get("competitions", 2026).values())  # the bad entry was replaced


def test_competitions_capture_skips_stale_empty_response(monkeypatch):
    """While switching season the page first requests the new season with the old age-group id, which
    returns []. The transport must wait for the real response instead of keeping the empty one."""
    from mksz_api.library.transport import BrowserTransport

    req = pages.competitions(2025)
    api = "https://public.mksz.hu/public/api/v1/versenyek/foversenyek?evadEv.equals=2025&nem.in={g}&korosztalyId.equals={k}"
    responses = [  # (url, body) in the order the page receives them
        (api.format(g="N", k=12020648), []), (api.format(g="F", k=12020645), []),  # stale: old season's ids
        (api.format(g="F", k=12020581), [{"id": 1}]), (api.format(g="N", k=12020584), [{"id": 2}]),
    ]

    class Resp:
        def __init__(self, url, body):
            self.url, self._body = url, body

        def json(self):
            return self._body

    class Page:
        def on(self, event, cb):
            self.cb = cb

        def goto(self, url, **_):
            for url_, body in responses:
                self.cb(Resp(url_, body))

        def evaluate(self, script):
            return True

        def wait_for_timeout(self, ms):
            pass

        def close(self):
            pass

    t = BrowserTransport(delay=0, timeout=1)
    monkeypatch.setattr(t, "_ensure", lambda: type("Ctx", (), {"new_page": lambda self: Page()})())
    try:
        assert t.fetch(req) == {"F": [{"id": 1}], "N": [{"id": 2}]}
    finally:
        t.close()


def test_match_finder(env):
    t, kw = env
    mf = MatchFinder(season="2026/27", league="NBI", gender="F", **kw)
    df = mf.matches.get_data_frame()
    assert len(df) == 12
    first = df.iloc[0]
    assert first["COMPETITION_ID"] == 32027115 and first["STAGE_ID"] == 320228793
    assert {"MATCH_ID", "HOME_TEAM", "AWAY_TEAM", "HOME_SCORE", "ROUND", "MATCH_DATE"} <= set(df.columns)
    assert all(isinstance(m, str) for m in df["MATCH_ID"])
    # second call is served from the cache: no page loads
    n = len(t.calls)
    MatchFinder(season="2026/27", league="NBI", gender="F", **kw)
    assert len(t.calls) == n


def test_match_finder_needs_a_filter():
    with pytest.raises(ValueError):
        MatchFinder(season=2026, get_request=False)


def test_standings(env):
    _, kw = env
    rows = Standings(320228793, season=2026, **kw).standings.get_records()
    assert len(rows) == 14
    top = rows[0]
    assert top["RANK"] == 1 and top["TEAM_NAME"] == "OTP Bank-PICK Szeged"
    assert top["GOAL_DIFF"] == top["GOALS_FOR"] - top["GOALS_AGAINST"]


# -- boxscore -----------------------------------------------------------------------------------------------

def test_boxscore_matches_livescore_page(env):
    _, kw = env
    box = BoxScore("32021352123", **kw)
    info = box.match_info.get_records()[0]
    assert (info["HOME_TEAM"], info["AWAY_TEAM"]) == ("PLER-Budapest", "Szigetszentmiklósi KSK")
    assert (info["HOME_SCORE"], info["AWAY_SCORE"], info["HOME_HT_SCORE"], info["AWAY_HT_SCORE"]) == (22, 20, 8, 12)
    assert info["SEASON"] == "2025/2026" and info["ROUND"] == 13

    players = {(p["TEAM_NAME"], p["JERSEY"]): p for p in box.player_stats.get_records()}
    hollo = players[("Szigetszentmiklósi KSK", 12)]
    assert hollo["PLAYER_NAME"] == "HOLLÓ BALÁZS PÉTER" and hollo["GK"]
    assert (hollo["SAVES"], hollo["SHOTS_FACED"], hollo["MIN"]) == (10, 27, "49:02")
    schmid = players[("Szigetszentmiklósi KSK", 10)]
    assert (schmid["GOALS"], schmid["GOALS_7M"], schmid["SHOTS_7M"]) == (5, 2, 2)
    assert schmid["SHOTS"] == 9  # the page shows 10: it also counts 1 blocked shot
    assert players[("Szigetszentmiklósi KSK", 8)]["TWO_MINUTES"] == 1  # Gábori

    teams = {t["TEAM_NAME"]: t for t in box.team_stats.get_records()}
    sz = teams["Szigetszentmiklósi KSK"]
    assert (sz["SCORE"], sz["GOALS"], sz["GOALS_7M"], sz["SHOTS_7M"]) == (20, 20, 2, 2)
    assert (sz["SAVES"], sz["SHOTS_FACED"]) == (11, 33)  # page: 11/33
    assert (sz["YELLOW_CARDS"], sz["TWO_MINUTES"]) == (1, 1)  # the yellow went to the head coach
    pler = teams["PLER-Budapest"]
    assert (pler["GOALS_7M"], pler["SHOTS_7M"], pler["TWO_MINUTES"]) == (1, 2, 2)

    staff = box.staff_stats.get_records()
    assert any(s["NAME"] == "IMRE VILMOS" and s["YELLOW_CARDS"] == 1 for s in staff)
    assert {o["ROLE"] for o in box.officials.get_records()} >= {"REFEREE", "TABLE_OFFICIAL"}


def test_boxscore_nba_api_shapes(env):
    _, kw = env
    box = BoxScore("32021422857", **kw)
    d = box.get_dict()
    assert d["resource"] == "boxscore" and d["parameters"] == {"match_id": "32021422857"}
    assert [r["name"] for r in d["resultSets"]] == ["MatchInfo", "PlayerStats", "TeamStats", "StaffStats", "Officials"]
    assert len(box.get_data_frames()) == 5
    assert box.get_normalized_dict()["MatchInfo"][0]["HOME_SCORE"] == 26
    assert json.loads(box.get_json())["resultSets"][1]["headers"][0] == "MATCH_ID"


def test_boxscore_to_csv(env, tmp_path):
    _, kw = env
    paths = BoxScore("32021352123", **kw).to_csv(tmp_path / "csv", prefix="m_")
    assert sorted(p.name for p in paths) == [
        "m_match_info.csv", "m_officials.csv", "m_player_stats.csv", "m_staff_stats.csv", "m_team_stats.csv"]


# -- play-by-play -------------------------------------------------------------------------------------------

def test_play_by_play_matches_livescore_page(env):
    _, kw = env
    events = PlayByPlay("32021352123", **kw).play_by_play.get_records()
    goals = [e for e in events if e["EVENT_TYPE"] in ("GOAL", "GOAL_7M")]
    assert sum(e["HOME_AWAY"] == "HOME" for e in goals) == 22
    assert sum(e["HOME_AWAY"] == "AWAY" for e in goals) == 20
    assert (events[-1]["HOME_SCORE"], events[-1]["AWAY_SCORE"]) == (22, 20)
    second_half = next(e for e in events if e["EVENT_TYPE"] == "PERIOD_START" and e["PERIOD"] == "3")
    assert (second_half["HOME_SCORE"], second_half["AWAY_SCORE"]) == (8, 12)  # half-time score
    timeouts = [(e["HOME_AWAY"], e["CLOCK"]) for e in events if e["EVENT_TYPE"] == "TIMEOUT"]
    assert timeouts == [("HOME", "24:15"), ("AWAY", "29:06"), ("AWAY", "40:51"),
                        ("HOME", "48:35"), ("AWAY", "58:33"), ("HOME", "59:44")]
    # chronological, and scores never go down
    clocks = [e["ELAPSED_SEC"] for e in events if e["ELAPSED_SEC"] is not None]
    assert clocks == sorted(clocks)
    assert all(a["HOME_SCORE"] <= b["HOME_SCORE"] for a, b in zip(events, events[1:]))


def test_play_by_play_agrees_with_boxscore(env):
    """Report shots + blocked shots in the PBP = the shot totals shown on the livescore page (22/42, 20/42)."""
    _, kw = env
    events = PlayByPlay("32021352123", **kw).play_by_play.get_records()
    teams = {t["TEAM_NAME"]: t for t in BoxScore("32021352123", **kw).team_stats.get_records()}
    for name, page_shots in (("PLER-Budapest", 42), ("Szigetszentmiklósi KSK", 42)):
        blocked = sum(1 for e in events if e["EVENT_TYPE"] == "SHOT_BLOCKED" and e["TEAM_NAME"] == name)
        assert teams[name]["SHOTS"] + blocked == page_shots


def test_play_by_play_event_fields(env):
    _, kw = env
    events = PlayByPlay("32021422857", **kw).play_by_play.get_records()
    goal = next(e for e in events if e["EVENT_TYPE"] == "GOAL")
    assert goal["PLAYER_NAME"] and goal["JERSEY"] is not None and goal["TEAM_ID"]
    assert goal["SHOT_AREA"] is not None and goal["OPP_GOALKEEPER_ID"]
    sub = next(e for e in events if e["EVENT_TYPE"] == "SUBSTITUTION")
    assert sub["PLAYER_IN_NAME"] and sub["PLAYER_OUT_NAME"]
    assert (events[-1]["HOME_SCORE"], events[-1]["AWAY_SCORE"]) == (26, 25)


def test_play_by_play_without_log(env):
    _, kw = env
    with pytest.raises(NoDataError):
        PlayByPlay("1", **kw)


# -- page requests ------------------------------------------------------------------------------------------

def test_program_pattern_ignores_default_window():
    import re

    rx = re.compile(pages.program("F", 32027115, 320228793, 2026).captures[0].pattern)
    base = ("https://public.mksz.hu/public/api/v1/versenyek/program?merkozesDatum.greaterThanOrEqual={start}"
            "&merkozesDatum.lessThanOrEqual=2027-07-31T23:59:59.000Z&foversenyId.equals=32027115&alversenyId.equals=320228793")
    assert rx.search(base.format(start="2026-05-31T21:59:59.000Z"))
    assert not rx.search(base.format(start="2026-11-11T21:59:59.000Z"))  # the page's default 2-week window


def test_boxscore_unplayed_match(env):
    t, kw = env
    report = load("report_32021422857.json")
    report.update(golHazai=None, golVendeg=None)
    t.pages["/jegyzokonyv/999999999"] = {"report": report}
    with pytest.raises(NoDataError):
        BoxScore("999999999", **kw)
