"""Verify the vendored spec offline or explicitly fetch a candidate for review.

This command never updates the pinned manifest or vendored snapshot.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import difflib
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
from urllib.parse import quote
from urllib.request import Request, urlopen

MANIFEST = Path("provenance/spec-source.json")


def _hash(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _path(root: Path, value: str) -> Path:
    relative = PurePosixPath(value)
    if relative.is_absolute() or ".." in relative.parts or not value:
        raise ValueError("Source paths must be relative without parent traversal")
    target = root / relative
    if not target.resolve().is_relative_to(root.resolve()):
        raise ValueError("Snapshot path escapes the repository")
    return target


def verify(root: Path) -> dict:
    """Check the manifest and snapshot hashes without any network access."""
    manifest = json.loads((root / MANIFEST).read_text())
    if manifest.get("schema") != "sapi-lab-spec-source/v1":
        raise ValueError("Unknown spec manifest schema")
    if not re.fullmatch(r"https://github.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", manifest["repository"]):
        raise ValueError("Expected an explicit public GitHub repository URL")
    if not re.fullmatch(r"[0-9a-f]{40}", manifest["revision"]):
        raise ValueError("The pinned revision must be a complete commit hash")
    if not isinstance(manifest["files"], list) or not manifest["files"]:
        raise ValueError("The manifest requires snapshot files")
    for entry in manifest["files"]:
        _path(root, entry["upstream_path"])
        snapshot = _path(root, entry["snapshot"])
        if _hash(snapshot.read_bytes()) != entry["sha256"]:
            raise ValueError(f"Snapshot hash mismatch: {entry['snapshot']}")
    return {"verified": True, "mode": "offline", **manifest}


def _fetch(url: str) -> bytes:
    with urlopen(Request(url, headers={"User-Agent": "sapi-config-lab-spec-compare"}), timeout=30) as response:
        data = response.read(4_194_305)
    if len(data) > 4_194_304:
        raise ValueError("Upstream response exceeds the source comparison limit")
    return data


def compare(root: Path, revision: str, output: Path, *, fetch=_fetch) -> dict:
    """Resolve a requested ref once; preserve its bytes and diff in a new directory."""
    pinned = verify(root)
    output.mkdir(parents=True, exist_ok=False)
    repository = pinned["repository"].removeprefix("https://github.com/")
    commit = json.loads(fetch(f"https://api.github.com/repos/{repository}/commits/{quote(revision, safe='')}"))["sha"]
    if not isinstance(commit, str) or not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ValueError("Upstream did not resolve to a complete commit hash")
    rows, diff = [], []
    for entry in pinned["files"]:
        path = entry["upstream_path"]
        candidate = fetch(f"https://raw.githubusercontent.com/{repository}/{commit}/{quote(path, safe='/')}")
        saved = _path(output / "candidate", path)
        saved.parent.mkdir(parents=True, exist_ok=True)
        saved.write_bytes(candidate)
        original = _path(root, entry["snapshot"]).read_bytes()
        diff.extend(
            difflib.unified_diff(
                original.decode().splitlines(keepends=True),
                candidate.decode().splitlines(keepends=True),
                fromfile=f"pinned/{path}",
                tofile=f"{commit}/{path}",
            )
        )
        rows.append({**entry, "candidate_sha256": _hash(candidate), "identical": candidate == original})
    report = {
        "schema": "sapi-lab-spec-comparison/v1",
        "repository": pinned["repository"],
        "pinned_revision": pinned["revision"],
        "requested_revision": revision,
        "resolved_revision": commit,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "files": rows,
        "changed_files": [row["upstream_path"] for row in rows if not row["identical"]],
        "pin_updated": False,
    }
    (output / "changes.diff").write_text("".join(diff))
    (output / "comparison.json").write_text(json.dumps(report, indent=2) + "\n")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("verify", "compare"))
    parser.add_argument("--revision", help="Explicit upstream commit or ref to compare; never changes the pin")
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    if args.command == "verify":
        if args.revision or args.output_dir:
            parser.error("Offline verification does not accept comparison arguments")
        result = verify(root)
    else:
        if not args.revision or not args.output_dir:
            parser.error("Comparison requires --revision and a new --output-dir")
        result = compare(root, args.revision, args.output_dir)
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
