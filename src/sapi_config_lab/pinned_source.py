"""External source checkouts pinned by a manifest under provenance/, verified offline before use."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import tempfile
from typing import Any
from urllib.parse import quote, urlparse
from urllib.request import Request

from sapi_config_lab.evidence import sha256
from sapi_config_lab.net import urlopen
from sapi_config_lab.paths import benchmark_root, workspace_root

Document = dict[str, Any]
SCHEMA = "sapi-lab-upstream-source/v1"
MAX_BODY = 2_000_000


def _get(url: str) -> bytes:
    with urlopen(Request(url, headers={"User-Agent": "sapi-config-lab"}), timeout=30) as response:
        body = response.read(MAX_BODY + 1)
    if len(body) > MAX_BODY:
        raise ValueError("Upstream source exceeds download limit")
    return body


def read_manifest(path: Path) -> Document:
    manifest = json.loads(path.read_text())
    repository = urlparse(manifest.get("repository") or "")
    if (
        manifest.get("schema") != SCHEMA
        or repository.scheme != "https"
        or repository.netloc != "github.com"
        or len(PurePosixPath(repository.path).parts) != 3
        or not re.fullmatch(r"[0-9a-f]{40}", manifest.get("revision") or "")
        or not isinstance(manifest.get("files"), dict)
        or not manifest["files"]
    ):
        raise ValueError(f"Unexpected pinned source manifest: {path.name}")
    for name, digest in manifest["files"].items():
        relative = PurePosixPath(name)
        if relative.is_absolute() or ".." in relative.parts or not name or len(digest) != 64:
            raise ValueError("Invalid pinned source entry")
    return manifest


@dataclass(frozen=True)
class PinnedSource:
    """A manifest and the local checkout it must describe exactly."""

    manifest: Path
    root: Path

    def verify(self) -> Document:
        """Every pinned file present and unchanged, and nothing else beside them."""
        manifest = read_manifest(self.manifest)
        root = self.root.resolve()
        for name, expected in manifest["files"].items():
            path = root / name
            if not path.resolve().is_relative_to(root) or not path.is_file() or path.is_symlink():
                raise ValueError(f"Missing or unsafe pinned source: {name}")
            if sha256(path) != expected:
                raise ValueError(f"Pinned source hash mismatch: {name}")
        # Extra Python could shadow the stdlib or add import hooks.
        present = {str(p.relative_to(root)) for p in root.rglob("*") if p.is_file()}
        if present != set(manifest["files"]):
            raise ValueError("Unexpected files in pinned source cache")
        return {
            "verified": True,
            "repository": manifest["repository"],
            "revision": manifest["revision"],
            "files": dict(manifest["files"]),
            "manifest_sha256": sha256(self.manifest),
        }


def pinned_source(name: str, *, cache: Path | None = None) -> PinnedSource:
    """Resolve a declared trusted source pin and its host-owned cache."""
    root = workspace_root()
    if re.fullmatch(r"[a-z][a-z0-9-]*", name) is None:
        raise ValueError("Invalid pinned source name")
    matches = {
        task.parent / "provenance" / (name + "-source.json")
        for task in benchmark_root().glob("*/task.toml")
        if (task.parent / "provenance" / (name + "-source.json")).is_file()
    }
    if len(matches) != 1:
        raise ValueError("Source pin must be owned by exactly one native task: " + name)
    manifest = matches.pop()
    if any(path.is_symlink() for path in (manifest, manifest.parent, manifest.parent.parent)):
        raise ValueError("Native source pin must not use symlinks")
    revision = read_manifest(manifest)["revision"]
    return PinnedSource(manifest, (cache or root / ".cache" / name) / revision)


def fetch_source(source: PinnedSource, *, fetch: Callable[[str], bytes] = _get) -> Path:
    """Fetch missing pinned bytes atomically; an existing checkout is verified, never repaired."""
    manifest = read_manifest(source.manifest)
    destination = source.root.resolve()
    if destination.exists():
        source.verify()
        return destination
    destination.parent.mkdir(parents=True, exist_ok=True)
    raw = "https://raw.githubusercontent.com" + urlparse(manifest["repository"]).path + "/" + manifest["revision"]
    temporary = Path(tempfile.mkdtemp(prefix=".fetch-", dir=destination.parent))
    try:
        for name, expected in manifest["files"].items():
            body = fetch(f"{raw}/{quote(name, safe='/')}")
            target = temporary / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(body)
            if len(body) > MAX_BODY or sha256(target) != expected:
                raise ValueError(f"Pinned source hash mismatch: {name}")
        PinnedSource(source.manifest, temporary).verify()
        temporary.rename(destination)
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)
    return destination
