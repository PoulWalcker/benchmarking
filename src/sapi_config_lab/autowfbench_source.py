"""The pinned AutoWFBench source: offline verification and an explicit fetch.

Both the simulator (execute) and the upstream judge and scorer (evaluate) load
code from this checkout, so both verify it here first.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path, PurePosixPath
import shutil
import tempfile
from typing import Any, Callable
from urllib.parse import quote
from urllib.request import Request

from sapi_config_lab.net import urlopen
from sapi_config_lab.paths import workspace_root

Document = dict[str, Any]
PINNED_REVISION = "970bbc8645c4d503d35cb5df05363fb9de132519"
MANIFEST = Path(__file__).with_name("autowfbench-source.json")
MAX_BODY = 2_000_000


def default_source() -> Path:
    """The private cache an experiment reads the pinned checkout from."""
    return workspace_root() / ".cache/autowfbench" / PINNED_REVISION


def _get(url: str) -> bytes:
    with urlopen(Request(url, headers={"User-Agent": "sapi-config-lab"}), timeout=30) as response:
        body = response.read(MAX_BODY + 1)
    if len(body) > MAX_BODY:
        raise ValueError("Upstream source exceeds download limit")
    return body


def _manifest() -> Document:
    manifest = json.loads(MANIFEST.read_text())
    if (
        manifest.get("schema") != "sapi-lab-upstream-source/v1"
        or manifest.get("repository") != "https://github.com/aleski-green/AutoWFBench"
        or manifest.get("revision") != PINNED_REVISION
        or not isinstance(manifest.get("files"), dict)
        or not manifest["files"]
    ):
        raise ValueError("Unexpected AutoWFBench source manifest")
    for name, digest in manifest["files"].items():
        path = PurePosixPath(name)
        if path.is_absolute() or ".." in path.parts or not name or len(digest) != 64:
            raise ValueError("Invalid pinned source entry")
    return manifest


def verify_source(source_root: Path) -> Document:
    """Verify every pinned source offline before importing or launching upstream."""
    source_root = Path(source_root).resolve()
    manifest = _manifest()
    for name, expected in manifest["files"].items():
        path = source_root / name
        if not path.resolve().is_relative_to(source_root) or not path.is_file() or path.is_symlink():
            raise ValueError(f"Missing or unsafe pinned source: {name}")
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError(f"Pinned source hash mismatch: {name}")
    # Extra Python can shadow stdlib or add import hooks. Use a closed checkout.
    present = {str(p.relative_to(source_root)) for p in source_root.rglob("*") if p.is_file()}
    if present != set(manifest["files"]):
        raise ValueError("Unexpected files in pinned source cache")
    return {
        "verified": True,
        "repository": manifest["repository"],
        "revision": manifest["revision"],
        "files": dict(manifest["files"]),
        "manifest_sha256": hashlib.sha256(MANIFEST.read_bytes()).hexdigest(),
    }


def fetch_source(cache_root: Path, *, fetch: Callable[[str], bytes] = _get) -> Path:
    """Explicitly fetch missing pinned bytes atomically; never resolve main or repin.

    Existing caches are verified, not repaired in place. Compilation and ordinary
    source verification never call this function or access the network.
    """
    manifest = _manifest()
    cache_root = Path(cache_root).resolve()
    destination = cache_root / PINNED_REVISION
    if destination.exists():
        verify_source(destination)
        return destination
    cache_root.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=".fetch-", dir=cache_root))
    try:
        for name, expected in manifest["files"].items():
            url = (
                f"https://raw.githubusercontent.com/aleski-green/AutoWFBench/{PINNED_REVISION}/{quote(name, safe='/')}"
            )
            body = fetch(url)
            if len(body) > MAX_BODY or hashlib.sha256(body).hexdigest() != expected:
                raise ValueError(f"Pinned source hash mismatch: {name}")
            target = temporary / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(body)
        verify_source(temporary)
        temporary.rename(destination)
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)
    return destination
