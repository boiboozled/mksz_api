"""Endpoint and DataSet base classes, shaped like nba_api's.

    ep = BoxScore("32021352123")
    ep.get_data_frames()                 # list of DataFrames, one per dataset
    ep.player_stats.get_data_frame()     # one dataset by name
    ep.get_dict() / ep.get_json()        # {"resource", "parameters", "resultSets": [{"name", "headers", "rowSet"}]}
    ep.get_normalized_dict()             # {"PlayerStats": [{col: value, ...}, ...], ...}
    ep.get_raw()                         # the untouched site payloads
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from mksz_api.library.cache import Cache, NoCache
from mksz_api.library.transport import PageRequest, Transport, get_transport


class NoDataError(LookupError):
    """The site has no data of this kind for the request (e.g. no livescore log for a lower-league match)."""


class DataSet:
    def __init__(self, name: str, headers: list[str], rows: list[list[Any]]):
        self.name = name
        self.headers = headers
        self.rows = rows

    def get_dict(self) -> dict:
        return {"headers": self.headers, "data": self.rows}

    def get_data_frame(self):
        import pandas as pd

        return pd.DataFrame(self.rows, columns=self.headers)

    def get_records(self) -> list[dict]:
        return [dict(zip(self.headers, row)) for row in self.rows]

    def __len__(self) -> int:
        return len(self.rows)

    def __repr__(self) -> str:
        return f"<DataSet {self.name}: {len(self.rows)} rows x {len(self.headers)} cols>"


def make_dataset(name: str, headers: list[str], records: list[dict]) -> DataSet:
    return DataSet(name, headers, [[r.get(h) for h in headers] for r in records])


class Endpoint:
    """Subclasses set `endpoint` and implement `_load()` (fetch payloads via self._fetch) and
    `_parse(raw) -> list[DataSet]`. Datasets are also exposed as snake_case attributes."""

    endpoint = "endpoint"

    def __init__(
        self,
        *,
        transport: Transport | None = None,
        cache: Cache | bool | None = None,
        refresh: bool = False,
        get_request: bool = True,
    ):
        self.transport = transport
        self.cache = NoCache() if cache is False else (cache if isinstance(cache, Cache) else Cache())
        self.refresh = refresh
        self.raw: dict[str, Any] = {}
        self.data_sets: list[DataSet] = []
        if get_request:
            self.get_request()

    # -- to implement -------------------------------------------------------------------------------------
    def parameters(self) -> dict:
        return {}

    def _load(self) -> dict[str, Any]:
        raise NotImplementedError

    def _parse(self, raw: dict[str, Any]) -> list[DataSet]:
        raise NotImplementedError

    # -- machinery ----------------------------------------------------------------------------------------
    def get_request(self) -> None:
        self.raw = self._load()
        self.data_sets = self._parse(self.raw)
        for ds in self.data_sets:
            setattr(self, _snake(ds.name), ds)

    def _fetch(
        self,
        kind: str,
        key: str | int,
        request: PageRequest,
        capture: str,
        max_age: float | None = None,
        is_final: Callable[[Any], bool] | None = None,
    ) -> Any:
        if not self.refresh:
            cached = self.cache.get(kind, key, max_age=max_age, is_final=is_final)
            if cached is not None:
                return cached
        data = (self.transport or get_transport()).fetch(request)[capture]
        self.cache.put(kind, key, data)
        return data

    def get_dict(self) -> dict:
        return {
            "resource": self.endpoint,
            "parameters": self.parameters(),
            "resultSets": [{"name": d.name, "headers": d.headers, "rowSet": d.rows} for d in self.data_sets],
        }

    def get_json(self) -> str:
        return json.dumps(self.get_dict(), ensure_ascii=False)

    def get_data_frames(self) -> list:
        return [d.get_data_frame() for d in self.data_sets]

    def get_normalized_dict(self) -> dict[str, list[dict]]:
        return {d.name: d.get_records() for d in self.data_sets}

    def get_raw(self) -> dict[str, Any]:
        return self.raw

    def to_csv(self, directory: str | Path, prefix: str = "") -> list[Path]:
        """Write one CSV per dataset into `directory`; returns the paths."""
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        paths = []
        for d in self.data_sets:
            path = directory / f"{prefix}{_snake(d.name)}.csv"
            d.get_data_frame().to_csv(path, index=False)
            paths.append(path)
        return paths

    def __repr__(self) -> str:
        sets = ", ".join(f"{d.name}({len(d)})" for d in self.data_sets)
        return f"<{type(self).__name__} {self.parameters()} [{sets}]>"


def _snake(name: str) -> str:
    out = ""
    for i, ch in enumerate(name):
        if ch.isupper() and i and not name[i - 1].isupper():
            out += "_"
        out += ch.lower()
    return out


def pct(made: int | None, attempts: int | None) -> float | None:
    if not attempts:
        return None
    return round(100.0 * (made or 0) / attempts, 1)


def mmss(seconds: int | float | None) -> str | None:
    if seconds is None:
        return None
    seconds = int(seconds)
    return f"{seconds // 60:02d}:{seconds % 60:02d}"
