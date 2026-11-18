from pathlib import Path

from mksz_roster.parse import parse_active_team, parse_header, to_lineup_json

HTML = (Path(__file__).parent / "fixtures" / "pler_tab.html").read_text(encoding="utf-8")


def test_header():
    header = parse_header(HTML)
    assert header["teams"] == ["PLER-Budapest", "Szigetszentmiklósi KSK"]
    assert header["score"] == ["22", "20"]
    assert header["half_time"] == ["8", "12"]


def test_active_team_roster():
    team = parse_active_team(HTML)
    assert team.name == "PLER-Budapest"
    assert len(team.players) == 16
    keepers = [p for p in team.players if p.gk]
    assert [(p.number, p.name) for p in keepers] == [("1", "BŐSZ DÁNIEL"), ("12", "KRÁNITZ ZOLTÁN")]
    by_number = {p.number: p for p in team.players}
    assert by_number["77"].name == "MARKEZ MITAR" and not by_number["77"].gk
    assert by_number["18"].time_on_court == "57:05"
    assert by_number["33"].time_on_court is None  # did not play


def test_lineup_json_puts_goalkeepers_first():
    team = parse_active_team(HTML)
    lineup = to_lineup_json("32021352123", parse_header(HTML), [team])
    players = lineup["teams"][0]["players"]
    assert players[0] == {"number": "1", "name": "BŐSZ DÁNIEL", "gk": True, "time_on_court": "43:27"}
    assert "gk" not in players[2]
    assert lineup["score"] == {"PLER-Budapest": "22", "Szigetszentmiklósi KSK": "20"}


def test_second_team_tab():
    """The Szigetszentmiklósi KSK tab of the same match (the second team tab, active after clicking it)."""
    html = (Path(__file__).parent / "fixtures" / "szksk_tab.html").read_text(encoding="utf-8")
    team = parse_active_team(html)
    assert team.name == "Szigetszentmiklósi KSK"
    assert len(team.players) == 16
    by_number = {p.number: p for p in team.players}
    assert by_number["12"].name == "HOLLÓ BALÁZS PÉTER" and by_number["12"].gk
    assert by_number["12"].time_on_court == "49:02"
    assert [p.number for p in team.players if p.gk] == ["12", "65", "99"]
    assert by_number["99"].time_on_court is None  # did not play
