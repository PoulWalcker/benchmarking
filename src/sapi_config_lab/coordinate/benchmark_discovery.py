"""Metadata-only discovery, with a bounded header bridge for unmigrated manifests."""

from dataclasses import dataclass
from pathlib import Path
import re

from sapi_config_lab.benchmark import Benchmark, contained_file, discover_benchmarks, read_manifest


@dataclass(frozen=True)
class LegacyBenchmark:
    """A listing header, never an executable replacement for a versioned descriptor."""

    name: str
    directory: Path
    default: bool
    version: None = None


def list_benchmarks(root: Path) -> tuple[Benchmark | LegacyBenchmark, ...]:
    """List both manifest generations without importing the old execution registry."""
    found: dict[str, Benchmark | LegacyBenchmark] = {item.name: item for item in discover_benchmarks(root)}
    for path in sorted(root.glob("*/scenario.json")):
        if path.parent.is_symlink():
            raise ValueError(f"Symlink benchmark directory: {path.parent}")
        meta = read_manifest(contained_file(path.parent, "scenario.json"))
        if "version" in meta:
            continue
        if not re.fullmatch(r"[0-9]{2}-[a-z][a-z0-9-]*", path.parent.name) or type(meta.get("default")) is not bool:
            raise ValueError(f"Invalid legacy benchmark header: {path.parent.name}")
        name = path.parent.name.split("-", 1)[1]
        if name in found:
            raise ValueError(f"Duplicate benchmark id: {name}")
        found[name] = LegacyBenchmark(name, path.parent.resolve(), meta["default"])
    return tuple(sorted(found.values(), key=lambda item: item.directory.name))
