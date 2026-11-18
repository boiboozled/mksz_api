"""Raw-payload cache: one JSON file per payload under <root>/<kind>/<key>.json.

A cached payload is reused when it is *final* (a finished match, a past season) or younger than the
endpoint's max age. The files are the untouched site payloads, so they can be re-parsed later without
loading any page again.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Callable

DEFAULT_ROOT = "data/raw"


class Cache:
    def __init__(self, root: str | Path | None = None):
        self.root = Path(root or os.environ.get("MKSZ_CACHE_DIR", DEFAULT_ROOT))

    def path(self, kind: str, key: str | int) -> Path:
        return self.root / kind / f"{key}.json"

    def get(
        self,
        kind: str,
        key: str | int,
        max_age: float | None = None,
        is_final: Callable[[Any], bool] | None = None,
    ) -> Any | None:
        """The cached payload, or None when missing or stale.

        Stale means: older than `max_age` seconds and not `is_final(payload)`. max_age=None never expires."""
        path = self.path(kind, key)
        if not path.exists():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        if max_age is None:
            return data
        if time.time() - path.stat().st_mtime <= max_age:
            return data
        if is_final is not None and is_final(data):
            return data
        return None

    def put(self, kind: str, key: str | int, data: Any) -> None:
        path = self.path(kind, key)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)


class NoCache(Cache):
    """Cache that never stores anything (cache=False on an endpoint)."""

    def __init__(self):
        super().__init__(root=os.devnull)

    def get(self, *args, **kwargs):
        return None

    def put(self, *args, **kwargs):
        return None
