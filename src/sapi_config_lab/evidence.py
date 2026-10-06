"""How recorded evidence is encoded and identified; its exact bytes are pinned by later hashes.

Two encodings must stay separate: indented JSON for documents people read, and the
canonical form (`canonical`, `digest`, `durable_json`) whose bytes recorded digests pin.
"""

from __future__ import annotations

from collections.abc import Callable
import hashlib
import json
import os
from pathlib import Path
from typing import Any


def json_text(value: Any, *, ensure_ascii: bool = False, default: Callable[[Any], Any] | None = None) -> str:
    """Two-space indent, one trailing newline; Unicode literal unless asked otherwise."""
    return json.dumps(value, indent=2, ensure_ascii=ensure_ascii, default=default) + "\n"


def write_json(path: Path | str, value: Any, *, ensure_ascii: bool = False) -> None:
    """Record a value as evidence. Pass ensure_ascii only where the escaped bytes
    are already pinned by a hash or a reader that cannot take UTF-8."""
    Path(path).write_text(json_text(value, ensure_ascii=ensure_ascii))


def write_record_json(path: Path | str, value: Any) -> None:
    """An execution record; values JSON cannot encode (paths, deadlines) become str() rather than failing the run."""
    Path(path).write_text(json_text(value, default=str))


def sha256(path: Path | str) -> str:
    """The identity recorded for a file, wherever one is recorded."""
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def canonical(value: Any) -> str:
    """Sorted keys, compact separators, Unicode literal, finite numbers only."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def digest(value: Any) -> str:
    """SHA-256 of the canonical encoding."""
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def durable_json(path: Path, value: Any) -> None:
    """Write canonical JSON atomically and fsync it and its directory."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w") as handle:
        handle.write(canonical(value) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(path)
    descriptor = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
