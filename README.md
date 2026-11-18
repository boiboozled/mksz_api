# mksz-api

**Hungarian handball data in Python, in the style of [nba_api](https://github.com/swar/nba_api).**

[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Status: alpha](https://img.shields.io/badge/status-alpha-orange.svg)](#project-status)

`mksz-api` gives programmatic access to the public competition data of the Hungarian Handball Federation
(Magyar Kézilabda Szövetség, [mksz.hu](https://mksz.hu)). That covers seasons, competitions, fixtures and
results, official match boxscores, standings and play-by-play event logs, returned as pandas DataFrames,
plain dicts/JSON or CSV files.

> **Unofficial project.** `mksz-api` is not affiliated with, endorsed by or supported by MKSZ. All data
> belongs to its respective owners. Please read [Responsible use](#responsible-use).

---

## Contents

- [Features](#features)
- [Installation](#installation)
- [Quickstart](#quickstart)
- [Concepts](#concepts)
- [Endpoints](#endpoints)
- [Command-line interface](#command-line-interface)
- [Caching and configuration](#caching-and-configuration)
- [How it works](#how-it-works)
- [Data notes and limitations](#data-notes-and-limitations)
- [Lineup export (`mksz-roster`)](#lineup-export-mksz-roster)
- [Development](#development)
- [Contributing](#contributing)
- [Responsible use](#responsible-use)
- [License](#license)

## Features

- **Discovery**: list seasons, then competitions and their stages, then every match ID of a season,
  filtered by league (`NBI`, `NBIB`, `NBII`, `MAGYAR_KUPA`, …) and gender.
- **Official boxscores** from the match report: per-player goals, shots, 7 m, saves, turnovers,
  technical faults, sanctions and time on court, plus team totals, bench officials and referees.
- **Play-by-play** from the livescore log: every shot (with court and goal zone), save, turnover,
  sanction, timeout and substitution, in chronological order with a running score.
- **Standings** for any league stage.
- **nba_api-like interface**: endpoint classes with `get_data_frames()`, `get_dict()`, `get_json()`
  and named datasets.
- **Raw-payload cache**: finished matches are fetched once and never reloaded.
- **CLI** (`mksz`) for exporting tidy CSV tables without writing code.

## Installation

`mksz-api` is not on PyPI yet. Install it from GitHub:

```bash
pip install "git+https://github.com/boiboozled/mksz_api"
```

or from a clone:

```bash
git clone https://github.com/boiboozled/mksz_api
cd mksz_api
pip install -e .            # or: uv venv && uv pip install -e .
```

The site is a JavaScript application, so data is loaded through a headless Chromium browser
([Playwright](https://playwright.dev/python/)). Download the browser once:

```bash
python -m playwright install chromium
```

On Linux, headless Chromium also needs some system libraries. Install them once, as root:

```bash
sudo "$(which python)" -m playwright install-deps chromium
```

**Requirements:** Python ≥ 3.10, pandas ≥ 2, Playwright ≥ 1.47.

## Quickstart

```python
from mksz_api.static import competitions
from mksz_api.endpoints import MatchFinder, BoxScore, PlayByPlay, Standings

# Find a competition (gender: "F" = men / férfi, "N" = women / női)
nb1 = competitions.find_competition_by_name("NB I", gender="F", season="2025/26")
print(nb1["id"], nb1["name"], [s["name"] for s in nb1["stages"]])

# Every match of the season, like nba_api's LeagueGameFinder
games = MatchFinder(season="2025/26", league="NBI", gender="F").get_data_frames()[0]
print(games[["MATCH_ID", "ROUND", "HOME_TEAM", "AWAY_TEAM", "HOME_SCORE", "AWAY_SCORE"]].head())

# Official boxscore of one match
box = BoxScore(match_id="32021352123")
players = box.player_stats.get_data_frame()
teams = box.team_stats.get_data_frame()

# Play-by-play
pbp = PlayByPlay("32021352123").play_by_play.get_data_frame()
goals = pbp[pbp.EVENT_TYPE.isin(["GOAL", "GOAL_7M"])]

# League table
table = Standings(stage_id=320228793, season="2026/27").standings.get_data_frame()
```

## Concepts

MKSZ organises its data in four levels:

| Level | Hungarian | ID column | Example |
|---|---|---|---|
| Season | *évad* | `SEASON_ID` | `2025` = 2025/2026 |
| Competition | *főverseny* | `COMPETITION_ID` | `32025870` = NB I Férfi 2025/26 |
| Stage | *alverseny* | `STAGE_ID` | `320225642` = regular season, `320228091` = championship final |
| Match | *mérkőzés* | `MATCH_ID` | `32021352123` |

- **Seasons** run from 1 July to 30 June and are identified by their starting year. Every parameter
  accepts `2025`, `"2025"`, `"2025/26"`, `"2025-26"` or `"2025/2026"`. `None` means the current season.
- **Leagues**: `LEAGUE_ID` is the site's competition code: `NBI`, `NBIB`, `NBII`, `MAGYAR_KUPA`,
  `SZUPERKUPA`, `EDZOMERKOZES` (friendlies), `SZERV_EGYEB_VERSENY` (regional leagues). `"NB I"` and
  `"nb i/b"` are normalised to `NBI` and `NBIB`.
- **Gender**: `"F"` (férfi, men) or `"N"` (női, women). `"men"` and `"women"` are also accepted.
- **Match IDs** are the numbers in `https://mksz.hu/jegyzokonyv/<MATCH_ID>` and
  `https://livescore.mksz.hu/<MATCH_ID>`.

## Endpoints

| Endpoint | Parameters | Datasets |
|---|---|---|
| `Seasons` | `sport="SPAG_1"` (indoor; `"SPAG_2"` = beach) | `Seasons` |
| `CompetitionFinder` | `season`, `gender`, `league` | `Competitions` (one row per stage) |
| `MatchFinder` | `season` and one of `league` / `competition_id` / `stage_id`; optional `gender` | `Matches` |
| `Standings` | `stage_id`, `season` | `Standings` |
| `BoxScore` | `match_id` | `MatchInfo`, `PlayerStats`, `TeamStats`, `StaffStats`, `Officials` |
| `PlayByPlay` | `match_id` | `PlayByPlay` |

All endpoints are importable from `mksz_api.endpoints` and also accept these keyword arguments:

| Argument | Default | Meaning |
|---|---|---|
| `transport` | shared `BrowserTransport` | how pages are loaded (see [How it works](#how-it-works)) |
| `cache` | `Cache()` | a `mksz_api.library.cache.Cache`, or `False` to disable caching |
| `refresh` | `False` | ignore cached payloads and reload |
| `get_request` | `True` | fetch on construction (set `False` to build the object only) |

### Output

```python
ep = BoxScore("32021352123")
ep.get_data_frames()             # [DataFrame, ...], one per dataset
ep.player_stats.get_data_frame() # a single dataset, as a snake_case attribute
ep.get_dict()                    # {"resource", "parameters", "resultSets": [{"name", "headers", "rowSet"}]}
ep.get_json()                    # the same, as a JSON string
ep.get_normalized_dict()         # {"PlayerStats": [{column: value, ...}, ...], ...}
ep.get_raw()                     # the untouched source payloads
ep.to_csv("out/", prefix="m_")   # one CSV per dataset
```

### Static lookups

`mksz_api.static.competitions` provides helpers in the spirit of `nba_api.stats.static.teams`:
`get_competitions(season, gender, league)`, `find_competitions_by_name(pattern, ...)`,
`find_competition_by_name(name, ...)` and `find_competition_by_id(competition_id, ...)`. Name matching
ignores case and accents. Competitions change every season, so these read (and cache) `CompetitionFinder`
instead of a bundled list.

### Key columns

**`PlayerStats`**: `PLAYER_ID`, `PLAYER_NAME`, `JERSEY`, `GK`, `GOALS`, `SHOTS`, `SHOT_PCT`, `GOALS_7M`,
`SHOTS_7M`, `SHOTS_SAVED`, `SHOTS_POST`, `SHOTS_MISSED`, `SAVES`, `SHOTS_FACED`, `SAVE_PCT`, `TURNOVERS`,
`TECHNICAL_FAULTS`, `YELLOW_CARDS`, `TWO_MINUTES`, `RED_CARDS`, `BLUE_CARDS`, `SECONDS_PLAYED`, `MIN`.

**`PlayByPlay`**: `EVENT_NUM`, `PERIOD`, `CLOCK`, `ELAPSED_SEC`, `HOME_AWAY`, `TEAM_NAME`, `EVENT_TYPE`,
`PLAYER_NAME`, `JERSEY`, `PLAYER_IN_NAME`, `PLAYER_OUT_NAME`, `OPP_GOALKEEPER_ID`, `SHOT_AREA`,
`GOAL_AREA`, `HOME_SCORE`, `AWAY_SCORE`. `EVENT_TYPE` is one of `GOAL`, `GOAL_7M`, `SHOT_SAVED`,
`SHOT_7M_SAVED`, `SHOT_MISSED`, `SHOT_7M_MISSED`, `SHOT_POST`, `SHOT_7M_POST`, `SHOT_BLOCKED`,
`TURNOVER`, `TECHNICAL_FAULT`, `TWO_MINUTES`, `YELLOW_CARD`, `RED_CARD`, `BLUE_CARD`, `TIMEOUT`,
`STARTER`, `SUBSTITUTION`, `SUSPENSION_LEAVE`, `SUSPENSION_RETURN`, `PERIOD_START` or
`GOALKEEPER_CHANGE`.

## Command-line interface

Installing the package provides the `mksz` command:

```bash
mksz seasons
mksz competitions --season 2026/27 --gender F [--league NBI]
mksz matches --season 2025/26 --league NBI --gender F -o data/nb1_men_2025.csv
mksz matches --season 2026 --stage 320228996 -o data/nb2_group_a.csv
mksz standings 320228793 --season 2026/27
mksz boxscore 32021352123 -o data/csv/          # one CSV per dataset (omit -o to print)
mksz pbp 32021352123 -o data/pbp.csv
mksz scrape --from data/nb1_men_2025.csv -o data/csv/
```

`mksz scrape` collects boxscores and play-by-play for many matches (from IDs given as arguments or a
CSV with a `MATCH_ID` column). It writes `match_info.csv`, `player_stats.csv`, `team_stats.csv`,
`staff_stats.csv`, `officials.csv` and `play_by_play.csv`. Unplayed matches are skipped; `--no-pbp`
skips play-by-play.

| Global option | Default | Meaning |
|---|---|---|
| `--delay SECONDS` | `2.0` | minimum pause between page loads |
| `--refresh` | off | ignore the cache |
| `--cache-dir DIR` | `$MKSZ_CACHE_DIR` or `data/raw` | raw payload cache |
| `--headed` | off | show the browser window (debugging) |

## Caching and configuration

Every source payload is stored untouched in `<cache>/<kind>/<id>.json`. The default cache directory is
`data/raw`, relative to the working directory; override it with the `MKSZ_CACHE_DIR` environment
variable or `cache=Cache("path")`.

| Data | Reused without reloading |
|---|---|
| finished matches (reports, event logs) | forever |
| past seasons (competitions, fixtures) | forever |
| current fixtures and standings | 6 hours |
| season and competition lists | 1 day |
| unfinished matches | 10 minutes |

To configure the browser, create a transport and make it the default:

```python
from mksz_api.library.transport import BrowserTransport, set_transport

set_transport(BrowserTransport(delay=3.0, timeout=60.0, headless=True))
```

## How it works

mksz.hu and livescore.mksz.hu are single-page applications that load their data as JSON after the page
opens. `BrowserTransport` opens a page in headless Chromium, as an ordinary visitor would, and keeps the
JSON responses the page itself receives. For full-season fixtures and past seasons, it sets the page's
own controls (the programme date range and the season selector), so the page requests that data itself.

Endpoints only describe *which page* holds *which payload* (`mksz_api/library/pages.py`). The transport
layer is pluggable: any object with a `fetch(PageRequest) -> dict` method can replace the browser, for
example in tests. `HttpTransport` is deliberately left unimplemented. The underlying JSON services are
not a documented public API, and calling them outside the website would require credentials embedded in
the site's code.

## Data notes and limitations

- **Boxscores are the official match report.** `SHOTS` = goals + saved + post + missed, 7 m shots
  included. The report does not record blocked shots. The livescore page counts them in its shot totals
  (e.g. *20/42* there vs. `SHOTS = 37` here plus 5 `SHOT_BLOCKED` events in the play-by-play).
- Team `SHOTS_FACED` is the opponent's goals plus saved shots (shots on an empty goal included), as on
  the livescore page. Team sanction totals include the bench officials'.
- **Play-by-play** is the livescore log. The raw log is out of order and its score lags behind, so events
  are re-sorted chronologically and the score is recomputed from the goal events. `CLOCK` is cumulative
  game time (`mm:ss`).
- **Coverage varies by league.** Lower leagues (e.g. NB II) log shots, goals and sanctions but not
  substitutions, so time on court is missing there.
- Unplayed matches raise `mksz_api.endpoints.NoDataError`, as do matches without a livescore log
  (`PlayByPlay`).
- Discovery covers **adult** competitions; youth and beach competitions are not listed yet.
- In-progress matches are fetched as page snapshots; there is no live (websocket) feed.

## Lineup export (`mksz-roster`)

The package also ships `mksz-roster`. It exports both teams' rosters (shirt number, name, goalkeeper
flag, time on court) of a match as the lineup JSON used by the handball-3d-analysis pipeline:

```bash
mksz-roster 32021352123 -o lineup.json
```

## Development

```bash
git clone https://github.com/boiboozled/mksz_api
cd mksz_api
uv venv && uv pip install -e ".[test]"
.venv/bin/python -m playwright install chromium
.venv/bin/pytest
```

The test suite runs **offline**. Endpoints are exercised end to end against captured site payloads in
`tests/fixtures/raw/` through a fake transport, so no browser or network is needed.

To see which JSON a page receives (e.g. when the site changes), use the reconnaissance script:

```bash
.venv/bin/python scripts/recon.py https://mksz.hu/jegyzokonyv/32021422857 -o /tmp/recon
```

Project layout:

```
src/mksz_api/
├── endpoints/        # Seasons, CompetitionFinder, MatchFinder, Standings, BoxScore, PlayByPlay
├── library/
│   ├── pages.py      # which page carries which payload (site-specific knowledge lives here)
│   ├── transport.py  # BrowserTransport, Transport protocol
│   ├── cache.py      # raw payload cache
│   └── parameters.py # season / gender / league parsing
├── static/           # competition lookups
└── cli.py            # the `mksz` command
src/mksz_roster/      # lineup export (`mksz-roster`)
scripts/recon.py      # dump the JSON a page receives
tests/                # offline tests and fixtures
```

## Contributing

Bug reports and pull requests are welcome at
[github.com/boiboozled/mksz_api/issues](https://github.com/boiboozled/mksz_api/issues).

- Open an issue first for larger changes.
- Add a fixture and a test for new endpoints or parser changes. Tests must not touch the network.
- When the site changes, `scripts/recon.py` shows the new payloads. Most fixes then belong in
  `library/pages.py`.

## Project status

Alpha: the public API may still change before 1.0. The scraper depends on how mksz.hu works internally,
so site updates can break it without notice.

## Responsible use

- Keep the default delay between page loads (2 s) or increase it, and rely on the cache. Do not hammer
  the site.
- The data is published by MKSZ; check their terms before redistributing it, especially commercially.
- This project is for research, analysis and personal use.

## License

Released under the [MIT License](LICENSE).

## Acknowledgements

The interface is modelled on [nba_api](https://github.com/swar/nba_api).
