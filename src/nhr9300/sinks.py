"""Persistence extension points for acquisition rows.

Sinks receive already-normalized rows and must not call the instrument. This
keeps hardware timing and safety ownership inside :mod:`nhr9300.acquisition`
while allowing future database, Parquet, or remote logging adapters.
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Mapping, Protocol, Sequence


class MeasurementSink(Protocol):
    """Lifecycle contract for one acquisition persistence destination."""

    def open(self, fields: Sequence[str]) -> None: ...

    def write(self, row: Mapping[str, object]) -> None: ...

    def close(self) -> None: ...


class CsvMeasurementSink:
    """Durable CSV sink that flushes every sample for interruption recovery."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._handle = None
        self._writer: csv.DictWriter | None = None

    def open(self, fields: Sequence[str]) -> None:
        if self._handle is not None:
            raise RuntimeError("CSV sink is already open")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._handle = self.path.open("w", newline="", encoding="utf-8")
        self._writer = csv.DictWriter(self._handle, fieldnames=list(fields))
        self._writer.writeheader()
        self._handle.flush()

    def write(self, row: Mapping[str, object]) -> None:
        if self._writer is None or self._handle is None:
            raise RuntimeError("CSV sink is not open")
        self._writer.writerow(dict(row))
        self._handle.flush()

    def close(self) -> None:
        if self._handle is not None:
            self._handle.close()
        self._handle = None
        self._writer = None
