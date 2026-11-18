"""Dev tool: load a page in headless Chromium and dump every JSON payload it receives.

Captures bo.mksz.hu JSON responses (XHR/fetch) and socket.io websocket frames, so we can see what data
each page gets and build the endpoint normalizers against real payloads.

Usage:
    .venv/bin/python scripts/recon.py https://livescore.mksz.hu/32021352123/jatekos_statisztikak -o tests/fixtures/raw/livescore
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import sync_playwright


def slug(url: str) -> str:
    parts = urlsplit(url)
    s = re.sub(r"[^A-Za-z0-9]+", "_", parts.path + ("_" + parts.query if parts.query else "")).strip("_")
    return s[:150] or "root"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("url")
    ap.add_argument("-o", "--out", type=Path, required=True)
    ap.add_argument("--wait", type=float, default=8.0, help="seconds to keep listening after load")
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    log: list[dict] = []
    frames: list[str] = []

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()

        def on_response(resp):
            ctype = resp.headers.get("content-type", "")
            if "json" not in ctype or "mksz" not in resp.url:
                return
            try:
                body = resp.json()
            except Exception as e:  # noqa: BLE001
                log.append({"url": resp.url, "status": resp.status, "error": str(e)})
                return
            name = slug(resp.url)
            (args.out / f"xhr_{name}.json").write_text(json.dumps(body, ensure_ascii=False, indent=1), encoding="utf-8")
            log.append({"url": resp.url, "status": resp.status, "file": f"xhr_{name}.json", "bytes": len(json.dumps(body))})

        def on_ws(ws):
            log.append({"websocket": ws.url})
            ws.on("framereceived", lambda payload: frames.append(payload if isinstance(payload, str) else f"<binary {len(payload)}>"))
            ws.on("framesent", lambda payload: frames.append(">> " + (payload if isinstance(payload, str) else "<binary>")))

        page.on("response", on_response)
        page.on("websocket", on_ws)
        page.goto(args.url, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(int(args.wait * 1000))
        (args.out / "page.html").write_text(page.content(), encoding="utf-8")
        browser.close()

    for i, f in enumerate(frames):
        m = re.match(r"^(\d+)(\[.*)$", f, re.S)
        if m and m.group(1).startswith("42"):
            try:
                event = json.loads(m.group(2))
                (args.out / f"ws_{i:03d}_{event[0]}.json").write_text(
                    json.dumps(event, ensure_ascii=False, indent=1), encoding="utf-8"
                )
            except json.JSONDecodeError:
                pass
    (args.out / "frames.txt").write_text("\n".join(f[:300] for f in frames), encoding="utf-8")
    (args.out / "log.json").write_text(json.dumps(log, ensure_ascii=False, indent=1), encoding="utf-8")
    for entry in log:
        print(entry)
    print(f"{len(frames)} websocket frames")


if __name__ == "__main__":
    main()
