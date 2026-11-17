# mksz-roster

Downloads both teams' rosters (shirt number, name, goalkeeper flag, time on court) of a handball match from
`https://livescore.mksz.hu/<match_id>/jatekos_statisztikak` and writes them in the `lineups/<video>.json`
format of the handball-3d-analysis pipeline (used by `jersey_track_fix.py`).

The page renders with JavaScript and shows one team at a time, so Playwright renders it and clicks each team
tab; BeautifulSoup parses the rendered HTML (tested offline against `tests/fixtures/`).

```bash
uv venv && uv pip install -e '.[test]' && .venv/bin/playwright install chromium
.venv/bin/mksz-roster 32021352123 -o lineup.json
.venv/bin/pytest
```

Headless Chromium needs some system libraries (libatk, libgbm, libxkbcommon, ...). If the launch fails,
install them once with root: `sudo .venv/bin/playwright install-deps chromium`.
