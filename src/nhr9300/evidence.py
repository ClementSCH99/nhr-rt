"""Small, dependency-free helpers for durable local evidence files."""

from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path
from typing import Any, Callable

from .errors import NHREvidencePersistenceError
from .types import to_jsonable

EvidencePersistenceError = NHREvidencePersistenceError


def atomic_write_json(
    path: Path,
    value: Any,
    *,
    attempts: int = 5,
    backoff_s: float = 0.02,
    replace: Callable[[str | bytes | os.PathLike[str] | os.PathLike[bytes], str | bytes | os.PathLike[str] | os.PathLike[bytes]], None] = os.replace,
) -> None:
    """Write JSON through a unique same-directory file and atomic replace.

    Windows sharing violations are retried with a short bounded backoff. The
    temporary file is flushed and fsynced before replacement and is removed on
    failure. Persistent failure is reported as
    :class:`~nhr9300.errors.NHREvidencePersistenceError`.
    """
    if attempts <= 0 or backoff_s < 0:
        raise ValueError("attempts must be positive and backoff_s non-negative")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    payload = json.dumps(to_jsonable(value), indent=2, ensure_ascii=False)
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        for attempt in range(attempts):
            try:
                replace(temporary, path)
                return
            except PermissionError as exc:
                if attempt + 1 == attempts:
                    raise NHREvidencePersistenceError(
                        f"Could not atomically persist evidence to {path} after "
                        f"{attempts} attempts; use a writable local non-synchronized path"
                    ) from exc
                time.sleep(backoff_s * (attempt + 1))
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def check_output_directory(path: Path) -> dict[str, Any]:
    """Test create/write/fsync/replace/delete without connecting hardware."""
    diagnostic_dir = path / f".nhr9300-diagnostic-{uuid.uuid4().hex}"
    target = diagnostic_dir / "replace-check.json"
    atomic_write_json(target, {"check": "output_dir_atomic_replace"})
    target.unlink()
    diagnostic_dir.rmdir()
    return {
        "path": str(path.resolve()),
        "writable": True,
        "atomic_replace": True,
    }
