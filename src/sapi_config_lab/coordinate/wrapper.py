"""The inspected model wrapper: live dispatch requires its recorded identity to hold on this machine."""

from __future__ import annotations

from pathlib import Path, PurePath

from sapi_config_lab.coordinate.replay import read_json, require
from sapi_config_lab.evidence import sha256

SCHEMA = "sapi-lab-wrapper-identity/v1"


def parse_wrapper_files(values: list[str] | None) -> dict[str, Path]:
    """`--wrapper-file NAME=PATH` → {NAME: PATH}."""
    locations: dict[str, Path] = {}
    for value in values or []:
        name, _, path = value.partition("=")
        if not name or not path or name in locations:
            raise ValueError("--wrapper-file takes NAME=PATH, once per inspected file")
        locations[name] = Path(path)
    return locations


def wrapper_identity(path: Path, endpoint: str, model: str, locations: dict[str, Path] | None = None) -> dict:
    """Fail closed unless every inspected file is byte-identical here.

    A file's logical name is its recorded `name`, or the basename of its recorded
    `path`. It is read from `locations[name]` when given, else from the recorded path,
    so a run moved to another machine names its local copies instead of weakening the hash.
    """
    evidence = read_json(path)
    require(
        evidence.get("schema") == SCHEMA and evidence.get("endpoint") == endpoint, "Wrapper identity endpoint mismatch"
    )
    require(
        evidence.get("dispatch") == "codex-exec"
        and evidence.get("response_substitution") is False
        and evidence.get("wrapper_retries") == 0
        and evidence.get("model") == model,
        "Wrapper dispatch inspection is missing or incompatible",
    )
    require(
        evidence.get("provider_internal_retries") in ("unknown", "none", "observed"),
        "Missing lower-layer retry limitation",
    )
    files = evidence.get("files")
    require(isinstance(files, list) and len(files) >= 1, "Missing private wrapper source identity")
    names = [row.get("name") or PurePath(row.get("path") or "").name for row in files]
    require(all(names) and len(set(names)) == len(names), "Duplicate or unnamed wrapper source identity")
    locations = locations or {}
    require(set(locations) <= set(names), "--wrapper-file names a file the identity does not record")
    for name, row in zip(names, files, strict=True):
        local = locations.get(name) or (Path(row["path"]) if row.get("path") else None)
        require(local is not None, f"No local path for wrapper file {name}; pass --wrapper-file {name}=PATH")
        assert local is not None
        require(local.is_file() and sha256(local) == row["sha256"], f"Wrapper/config identity changed: {name}")
    return evidence
