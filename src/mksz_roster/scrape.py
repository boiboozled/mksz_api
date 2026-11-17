"""Download both teams' rosters of an MKSZ match: render the livescore page with
Playwright, click each team tab, and parse each table with mksz_roster.parse.

Usage:
    mksz-roster 32021352123 -o lineup.json
    mksz-roster 32021352123 --save-html pages/   # also keep the rendered HTML per team
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from mksz_roster.parse import parse_active_team, parse_header, to_lineup_json

URL = "https://livescore.mksz.hu/{match_id}/jatekos_statisztikak"


def scrape(match_id: str, timeout_ms: int = 30000, save_html: Path | None = None, headed: bool = False) -> dict:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not headed)
        page = browser.new_page()
        page.goto(URL.format(match_id=match_id), wait_until="domcontentloaded", timeout=timeout_ms)
        page.wait_for_selector(".main-container .team-tab", timeout=timeout_ms)
        page.wait_for_selector(".main-container .table-row", timeout=timeout_ms)
        header = parse_header(page.content())

        teams = []
        tabs = page.locator(".main-container .team-tab")
        for i in range(tabs.count()):
            tab = tabs.nth(i)
            tab.click()
            # The tab is active once its inner element carries the class; then wait for the rows to re-render.
            tab.locator(".team-inner.active").wait_for(timeout=timeout_ms)
            page.wait_for_timeout(300)
            page.wait_for_selector(".main-container .table-row", timeout=timeout_ms)
            html = page.content()
            if save_html:
                save_html.mkdir(parents=True, exist_ok=True)
                (save_html / f"{match_id}_team{i}.html").write_text(html, encoding="utf-8")
            teams.append(parse_active_team(html))
        browser.close()

    if len(teams) != 2 or teams[0].name == teams[1].name:
        raise RuntimeError(f"expected two different teams, got {[t.name for t in teams]}")
    return to_lineup_json(match_id, header, teams)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("match_id", help="the number in https://livescore.mksz.hu/<match_id>/jatekos_statisztikak")
    ap.add_argument("-o", "--output", help="lineup JSON path (default: print)")
    ap.add_argument("--save-html", type=Path, help="directory to keep the rendered page per team")
    ap.add_argument("--headed", action="store_true", help="show the browser window")
    ap.add_argument("--timeout", type=int, default=30000, help="ms")
    args = ap.parse_args()

    lineup = scrape(args.match_id, args.timeout, args.save_html, args.headed)
    text = json.dumps(lineup, ensure_ascii=False, indent=1)
    if args.output:
        Path(args.output).write_text(text + "\n", encoding="utf-8")
        counts = ", ".join(f"{t['name']}: {len(t['players'])}" for t in lineup["teams"])
        print(f"wrote {args.output} ({counts})")
    else:
        print(text)


if __name__ == "__main__":
    main()
