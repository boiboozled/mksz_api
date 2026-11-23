"""`mksz` command line: thin wrapper around mksz_api.endpoints.

    mksz seasons
    mksz competitions --season 2026/27 --gender F
    mksz matches --season 2026/27 --league NBI --gender F -o data/nb1_men_2026.csv
    mksz boxscore 32021352123 -o data/csv/
    mksz pbp 32021352123 -o data/csv/
    mksz shotchart 32021352123 --player Ocvirk -o data/shots/   # shots + zone/goal-cell summaries
    mksz shotchart --season 2025/26 --league NBI --gender F --goalkeeper-id 2640 -o data/gk/
    mksz scrape --from data/nb1_men_2026.csv -o data/csv/      # boxscores + play-by-play for many matches
    mksz advanced 32021352123 [--clutch] -o data/adv/          # possessions, ratings, lineups, situations
    mksz leaguedash teams --season 2025/26 --league NBI --gender F --measure Advanced
    mksz leaguedash players --season 2025/26 --league NBI --gender F --measure Base --per-mode Per60 --min-minutes 120

Raw payloads are cached under data/raw (or $MKSZ_CACHE_DIR), so re-running only loads pages for matches
that are new or not finished yet.
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

from mksz_api.endpoints import (
    BoxScore, BoxScoreAdvanced, CompetitionFinder, LeagueDashPlayerStats, LeagueDashTeamStats, MatchFinder,
    NoDataError, PlayByPlay, Seasons, ShotChart, Standings,
)
from mksz_api.library.advanced import PER_MODES
from mksz_api.library.cache import Cache
from mksz_api.library.transport import BrowserTransport, TransportError, set_transport


def _emit(dataset, output: str | None) -> None:
    df = dataset.get_data_frame()
    if output:
        Path(output).parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(output, index=False)
        print(f"wrote {output} ({len(df)} rows)", file=sys.stderr)
    else:
        import pandas as pd

        with pd.option_context("display.max_rows", 500, "display.max_columns", 50, "display.width", 250):
            print(df.to_string(index=False))


def _write_endpoint(ep, out_dir: str | None, prefix: str) -> None:
    if out_dir:
        for path in ep.to_csv(out_dir, prefix=prefix):
            print(f"wrote {path}", file=sys.stderr)
    else:
        for ds in ep.data_sets:
            print(f"\n== {ds.name}")
            _emit(ds, None)


def _match_ids(args) -> list[str]:
    ids = list(args.match_ids or [])
    if args.from_csv:
        with open(args.from_csv, newline="", encoding="utf-8") as f:
            ids += [row["MATCH_ID"] for row in csv.DictReader(f)]
    return list(dict.fromkeys(ids))


def _append(path: Path, rows: list[dict], headers: list[str], fresh: set[Path]) -> None:
    """Append rows to a CSV, writing the header the first time the file is touched in this run."""
    mode = "w" if path not in fresh else "a"
    with open(path, mode, newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=headers)
        if mode == "w":
            w.writeheader()
        w.writerows(rows)
    fresh.add(path)


def cmd_scrape(args, common) -> int:
    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=True)
    ids = _match_ids(args)
    if not ids:
        print("no match ids (give ids or --from matches.csv)", file=sys.stderr)
        return 2
    fresh: set[Path] = set()
    failures = 0
    for i, mid in enumerate(ids, 1):
        status = []
        try:
            box = BoxScore(mid, **common)
            for ds in box.data_sets:
                _append(out / f"{_file(ds.name)}.csv", ds.get_records(), ds.headers, fresh)
            status.append("boxscore")
        except NoDataError as e:
            status.append(f"skipped: {e}")
            print(f"[{i}/{len(ids)}] {mid}: {', '.join(status)}", file=sys.stderr)
            continue  # unplayed match: no play-by-play either
        except TransportError as e:
            status.append(f"boxscore FAILED ({e})")
            failures += 1
        if not args.no_pbp:
            try:
                pbp = PlayByPlay(mid, **common)
                ds = pbp.play_by_play
                _append(out / "play_by_play.csv", ds.get_records(), ds.headers, fresh)
                status.append(f"pbp {len(ds)} events")
            except NoDataError:
                status.append("no pbp")
            except TransportError as e:
                status.append(f"pbp FAILED ({e})")
                failures += 1
        print(f"[{i}/{len(ids)}] {mid}: {', '.join(status)}", file=sys.stderr)
    print(f"done: {len(ids)} matches into {out}/ ({failures} failures)", file=sys.stderr)
    return 1 if failures else 0


def _file(name: str) -> str:
    from mksz_api.endpoints._base import _snake

    return _snake(name)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="mksz", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cache-dir", help="raw payload cache (default: $MKSZ_CACHE_DIR or data/raw)")
    ap.add_argument("--refresh", action="store_true", help="ignore the cache and reload pages")
    ap.add_argument("--delay", type=float, default=2.0, help="seconds between page loads (default 2)")
    ap.add_argument("--headed", action="store_true", help="show the browser window")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("seasons", help="list seasons")
    p.add_argument("-o", "--output")

    p = sub.add_parser("competitions", help="list competitions and stages of a season")
    p.add_argument("--season")
    p.add_argument("--gender", help="F (men) or N (women)")
    p.add_argument("--league", help="e.g. NBI, NBIB, NBII, MAGYAR_KUPA")
    p.add_argument("-o", "--output")

    p = sub.add_parser("matches", help="list matches (match ids) of a season")
    p.add_argument("--season")
    p.add_argument("--league")
    p.add_argument("--gender")
    p.add_argument("--competition", type=int, help="competition (főverseny) id")
    p.add_argument("--stage", type=int, help="stage (alverseny) id")
    p.add_argument("-o", "--output")

    p = sub.add_parser("standings", help="league table of a stage")
    p.add_argument("stage", type=int)
    p.add_argument("--season")
    p.add_argument("-o", "--output")

    p = sub.add_parser("boxscore", help="official boxscore of a match")
    p.add_argument("match_id")
    p.add_argument("-o", "--output", help="directory for the CSVs (default: print)")

    p = sub.add_parser("pbp", help="play-by-play of a match")
    p.add_argument("match_id")
    p.add_argument("-o", "--output", help="CSV path (default: print)")

    p = sub.add_parser("shotchart", help="shot locations/outcomes of a player, team or goalkeeper")
    p.add_argument("match_ids", nargs="*", help="match id(s); or use --season with --league/--competition/--stage")
    p.add_argument("--season")
    p.add_argument("--league")
    p.add_argument("--gender")
    p.add_argument("--competition", type=int)
    p.add_argument("--stage", type=int)
    p.add_argument("--player", help="player name (part of it; accents/case ignored)")
    p.add_argument("--player-id", type=int)
    p.add_argument("--team-id", type=int, help="shots taken by this team")
    p.add_argument("--opponent-team-id", type=int, help="shots conceded by this team")
    p.add_argument("--goalkeeper-id", type=int, help="shots faced by this goalkeeper")
    p.add_argument("-o", "--output", help="directory for the CSVs (default: print the summaries)")

    p = sub.add_parser("advanced", help="advanced box score of a match (possessions, ratings, lineups, ...)")
    p.add_argument("match_id")
    p.add_argument("--clutch", action="store_true", help="clutch time only (last 5 min / OT, margin <= 2)")
    p.add_argument("-o", "--output", help="directory for the CSVs (default: print)")

    p = sub.add_parser("leaguedash", help="season team or player stats (Base, Advanced, Per60, ...)")
    p.add_argument("kind", choices=["teams", "players"])
    p.add_argument("match_ids", nargs="*", help="match id(s); or use --season with --league/--competition/--stage")
    p.add_argument("--season")
    p.add_argument("--league")
    p.add_argument("--gender")
    p.add_argument("--competition", type=int)
    p.add_argument("--stage", type=int)
    p.add_argument("--team-id", type=int)
    p.add_argument("--player-id", type=int, help="players only")
    p.add_argument("--measure", default="Base",
                   help="teams: Base, Advanced, FourFactors, ShotProfile, Situations, GameFlow; "
                        "players: Base, Advanced, Goalkeeping (default Base)")
    p.add_argument("--per-mode", default="Totals", choices=PER_MODES)
    p.add_argument("--clutch", action="store_true")
    p.add_argument("--min-minutes", type=float, default=0, help="players only")
    p.add_argument("-o", "--output", help="CSV path (default: print)")

    p = sub.add_parser("scrape", help="boxscores + play-by-play of many matches into CSV tables")
    p.add_argument("match_ids", nargs="*")
    p.add_argument("--from", dest="from_csv", help="CSV with a MATCH_ID column (e.g. from `mksz matches`)")
    p.add_argument("--no-pbp", action="store_true", help="skip play-by-play")
    p.add_argument("-o", "--output", default="data/csv")

    args = ap.parse_args(argv)
    transport = BrowserTransport(delay=args.delay, headless=not args.headed)
    set_transport(transport)
    common = {"cache": Cache(args.cache_dir), "refresh": args.refresh}
    try:
        if args.cmd == "seasons":
            _emit(Seasons(**common).seasons, args.output)
        elif args.cmd == "competitions":
            _emit(CompetitionFinder(args.season, args.gender, args.league, **common).competitions, args.output)
        elif args.cmd == "matches":
            mf = MatchFinder(args.season, args.league, args.gender, args.competition, args.stage, **common)
            _emit(mf.matches, args.output)
        elif args.cmd == "standings":
            _emit(Standings(args.stage, args.season, **common).standings, args.output)
        elif args.cmd == "boxscore":
            _write_endpoint(BoxScore(args.match_id, **common), args.output, f"{args.match_id}_")
        elif args.cmd == "pbp":
            _emit(PlayByPlay(args.match_id, **common).play_by_play, args.output)
        elif args.cmd == "shotchart":
            sc = ShotChart(
                match_ids=args.match_ids or None, season=args.season, league=args.league, gender=args.gender,
                competition_id=args.competition, stage_id=args.stage, player_id=args.player_id,
                player_name=args.player, team_id=args.team_id, opponent_team_id=args.opponent_team_id,
                goalkeeper_id=args.goalkeeper_id, **common,
            )
            if args.output:
                _write_endpoint(sc, args.output, "")
            else:
                for ds in (sc.summary, sc.zone_summary, sc.goal_summary, sc.missing_matches):
                    print(f"\n== {ds.name}")
                    _emit(ds, None)
        elif args.cmd == "advanced":
            _write_endpoint(BoxScoreAdvanced(args.match_id, clutch=args.clutch, **common), args.output,
                            f"{args.match_id}_")
        elif args.cmd == "leaguedash":
            scope = dict(season=args.season, league=args.league, gender=args.gender, competition_id=args.competition,
                         stage_id=args.stage, match_ids=args.match_ids or None, team_id=args.team_id,
                         measure_type=args.measure, per_mode=args.per_mode, clutch=args.clutch)
            if args.kind == "teams":
                dash = LeagueDashTeamStats(**scope, **common)
            else:
                dash = LeagueDashPlayerStats(**scope, player_id=args.player_id, min_minutes=args.min_minutes, **common)
            _emit(dash.data_sets[0], args.output)
            if len(dash.missing_matches):
                print(f"{len(dash.missing_matches)} match(es) without full data (see MissingMatches):", file=sys.stderr)
                for m in dash.missing_matches.get_records():
                    print(f"  {m['MATCH_ID']}: {m['REASON']}", file=sys.stderr)
        elif args.cmd == "scrape":
            return cmd_scrape(args, common)
    except (NoDataError, TransportError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    finally:
        transport.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
