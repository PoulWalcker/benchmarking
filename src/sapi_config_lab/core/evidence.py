"""How recorded evidence is encoded and identified.

Every artifact this lab writes is hashed, pinned and compared later, so its exact
bytes are part of the contract. Keeping the encoding here means a change to it is
a change in one place rather than a drift between twenty.

`runtime.lifecycle.durable_json` is deliberately not part of this family: it
records lineage as canonical compact JSON and must keep producing the digests
already recorded. Do not route it through here.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
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
    """Record an execution artifact, whose records carry values JSON cannot encode
    (paths, deadlines). Those are recorded as str() rather than failing the run
    that produced them; no other artifact gets that latitude."""
    Path(path).write_text(json_text(value, default=str))


def sha256(path: Path | str) -> str:
    """The identity recorded for a file, wherever one is recorded."""
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()
