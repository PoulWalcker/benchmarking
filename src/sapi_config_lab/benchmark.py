"""Versioned benchmark declarations; metadata discovery never imports benchmark code."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import json
import keyword
import math
from pathlib import Path, PurePosixPath
import re
from types import MappingProxyType
from typing import Any, Literal

MANIFEST_VERSION = "sapi-lab-benchmark/v1"
Visibility = Literal["public", "trusted", "reference"]


def _freeze(value: Any) -> Any:
    """Detach JSON options/configuration and make every nested container immutable."""
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    return value


def read_manifest(path: Path) -> dict[str, Any]:
    """Reject ambiguous object keys and non-JSON numeric constants."""

    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            if key in result:
                raise ValueError(f"Duplicate manifest key: {key}")
            result[key] = value
        return result

    def constant(value: str) -> Any:
        raise ValueError(f"Invalid JSON constant: {value}")

    result = json.loads(path.read_text(), object_pairs_hook=pairs, parse_constant=constant)
    if not isinstance(result, dict):
        raise ValueError(f"Manifest must be an object: {path}")
    json.dumps(result, allow_nan=False)
    return result


def contained_file(root: Path, relative: str) -> Path:
    """Require a canonical relative regular file, with no symlink components."""
    if (
        not isinstance(relative, str)
        or not relative
        or "\\" in relative
        or PurePosixPath(relative).is_absolute()
        or any(part in {"", ".", ".."} for part in relative.split("/"))
    ):
        raise ValueError(f"Invalid relative path: {relative!r}")
    path = root
    for part in relative.split("/"):
        path /= part
        if path.is_symlink():
            raise ValueError(f"Symlink path is not allowed: {path}")
    if not path.is_file() or not path.resolve().is_relative_to(root.resolve()):
        raise ValueError(f"Missing or escaping file: {path}")
    return path


@dataclass(frozen=True)
class BenchmarkFile:
    source: Path
    destination: str
    visibility: Visibility


@dataclass(frozen=True)
class Entrypoint:
    path: str
    symbol: str


@dataclass(frozen=True)
class Budgets:
    authoring_attempts: int | None
    runtime_model_calls: int | None
    judge_calls: int


@dataclass(frozen=True)
class Controls:
    oracle_acceptance: bool
    reference_reward: float | None


@dataclass(frozen=True)
class Benchmark:
    version: str
    name: str
    directory: Path
    public: tuple[BenchmarkFile, ...]
    trusted: tuple[BenchmarkFile, ...]
    reference: BenchmarkFile
    bindings: str
    operations: str
    harbor_task: str
    entrypoints: Mapping[str, Entrypoint]
    dependencies: Mapping[str, Path]
    budgets: Budgets
    controls: Controls
    default: bool
    config: Mapping[str, Any]

    @property
    def files(self) -> tuple[BenchmarkFile, ...]:
        return (*self.public, *self.trusted, self.reference)


def python_module(path: str) -> str:
    """Map a declared Python file to its package-relative import identity."""
    parts = list(PurePosixPath(path).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    if any(not part.isidentifier() or keyword.iskeyword(part) for part in parts):
        raise ValueError(f"Invalid Python module identity: {path}")
    return ".".join(parts)


def load_benchmark(root: Path, directory: Path) -> Benchmark:
    """Validate one manifest under an explicit benchmark search root."""
    root = root.resolve()
    if directory.is_symlink() or directory.parent.resolve() != root:
        raise ValueError(f"Benchmark must be a direct directory under {root}: {directory}")
    directory = directory.resolve()
    meta = read_manifest(contained_file(directory, "scenario.json"))
    fields = {
        "version",
        "id",
        "default",
        "public",
        "trusted",
        "reference",
        "bindings",
        "operations",
        "harbor_task",
        "entrypoints",
        "dependencies",
        "controls",
        "budgets",
        "config",
    }
    if set(meta) != fields:
        raise ValueError(f"Manifest fields differ: {sorted(set(meta) ^ fields)}")
    if meta["version"] != MANIFEST_VERSION:
        raise ValueError(f"Unsupported benchmark version: {meta['version']!r}")
    name = meta["id"]
    if not isinstance(name, str) or not re.fullmatch(r"[a-z][a-z0-9-]*", name):
        raise ValueError("Benchmark id must be a lowercase CLI name")
    if type(meta["default"]) is not bool:
        raise ValueError("default must be an explicit boolean")
    files: dict[str, BenchmarkFile] = {}
    sources: set[Path] = set()

    def add(base: Path, path: str, destination: str, visibility: Visibility) -> BenchmarkFile:
        source = contained_file(base, path)
        if destination in files or source in sources:
            raise ValueError(f"Conflicting file classification/destination: {destination}")
        if any(destination.startswith(old + "/") or old.startswith(destination + "/") for old in files):
            raise ValueError(f"Conflicting file destination: {destination}")
        item = BenchmarkFile(source, destination, visibility)
        files[destination] = item
        sources.add(source)
        return item

    def declared(base: Path, section: Mapping[str, Any], prefix: str = "") -> None:
        for visibility in ("public", "trusted"):
            paths = section[visibility]
            if not isinstance(paths, list) or any(not isinstance(path, str) for path in paths):
                raise ValueError(f"{visibility} must be an explicit list of files")
            for path in paths:
                if not prefix and path.split("/")[0] in {"dependencies", "scenario.json"}:
                    raise ValueError(f"Reserved destination: {path}")
                add(base, path, prefix + path, visibility)

    declared(directory, meta)
    reference_path = meta["reference"]
    if isinstance(reference_path, str) and reference_path.split("/")[0] in {"dependencies", "scenario.json"}:
        raise ValueError(f"Reserved reference destination: {reference_path}")
    reference = add(directory, reference_path, reference_path, "reference")
    dependencies: dict[str, Path] = {}
    if not isinstance(meta["dependencies"], dict):
        raise ValueError("dependencies must be an object")
    for alias, declaration in meta["dependencies"].items():
        if not alias.isidentifier() or keyword.iskeyword(alias):
            raise ValueError(f"Invalid dependency identity: {alias}")
        if not isinstance(declaration, dict) or set(declaration) != {"path", "public", "trusted"}:
            raise ValueError(f"Invalid dependency declaration: {alias}")
        path = declaration["path"]
        if not isinstance(path, str) or not re.fullmatch(r"_shared/[a-z][a-z0-9_-]*", path):
            raise ValueError(f"Dependency must name a benchmark-owned _shared directory: {alias}")
        base = root / path
        if (root / "_shared").is_symlink() or base.is_symlink() or not base.is_dir():
            raise ValueError(f"Missing or symlink dependency: {path}")
        dependencies[alias] = base
        if not declaration["public"] and not declaration["trusted"]:
            raise ValueError(f"Dependency needs explicit files: {alias}")
        declared(base, declaration, f"dependencies/{alias}/")
    for key in ("bindings", "operations", "harbor_task"):
        path = meta[key]
        if not isinstance(path, str) or path not in files or files[path].visibility == "reference":
            raise ValueError(f"{key} must identify a declared public/trusted file")
    if not meta["harbor_task"].endswith(".toml"):
        raise ValueError("harbor_task must identify standard Harbor TOML configuration")
    entrypoints: dict[str, Entrypoint] = {}
    entries = meta["entrypoints"]
    if (
        not isinstance(entries, dict)
        or not {"plan", "evaluate"} <= entries.keys()
        or entries.keys() - {"plan", "evaluate", "prepare", "snapshot"}
    ):
        raise ValueError("entrypoints requires plan/evaluate and only optional prepare/snapshot")
    for role, entry in entries.items():
        if not isinstance(entry, dict) or set(entry) != {"path", "symbol"}:
            raise ValueError(f"Invalid entrypoint: {role}")
        path, symbol = entry["path"], entry["symbol"]
        if (
            not isinstance(path, str)
            or path not in files
            or files[path].visibility != "trusted"
            or not path.endswith(".py")
            or not isinstance(symbol, str)
            or not symbol.isidentifier()
            or keyword.iskeyword(symbol)
        ):
            raise ValueError(f"Entrypoint must name a trusted Python file and callable symbol: {role}")
        entrypoints[role] = Entrypoint(path, symbol)
    module_paths: dict[str, str] = {}
    for item in files.values():
        if item.destination.endswith(".py") and item.visibility == "trusted":
            module = python_module(item.destination)
            if module in module_paths:
                raise ValueError(f"Conflicting module identity: {item.destination}, {module_paths[module]}")
            module_paths[module] = item.destination
    for module, path in module_paths.items():
        if (
            not path.endswith("/__init__.py")
            and path != "__init__.py"
            and any(other.startswith(module + ".") for other in module_paths)
        ):
            raise ValueError(f"Conflicting module/package identity: {path}")
    budgets = meta["budgets"]
    if not isinstance(budgets, dict) or set(budgets) != {"authoring_attempts", "runtime_model_calls", "judge_calls"}:
        raise ValueError("budgets requires authoring_attempts, runtime_model_calls and judge_calls")
    for key, value in budgets.items():
        if value is None and key != "judge_calls":
            continue
        if type(value) is not int or value < 0:
            raise ValueError(
                f"budgets.{key} must be a nonnegative integer" + (" or null" if key != "judge_calls" else "")
            )
    controls = meta["controls"]
    if not isinstance(controls, dict) or set(controls) != {"oracle_acceptance", "reference_reward"}:
        raise ValueError("controls requires oracle_acceptance and reference_reward")
    reward = controls["reference_reward"]
    if controls["oracle_acceptance"] is not True or (
        reward is not None and (type(reward) not in (int, float) or not math.isfinite(reward) or not 0 <= reward <= 1)
    ):
        raise ValueError("Invalid oracle acceptance or reference reward")
    if not isinstance(meta["config"], dict):
        raise ValueError("config must be an opaque JSON object")
    return Benchmark(
        MANIFEST_VERSION,
        name,
        directory,
        tuple(item for item in files.values() if item.visibility == "public"),
        tuple(item for item in files.values() if item.visibility == "trusted"),
        reference,
        meta["bindings"],
        meta["operations"],
        meta["harbor_task"],
        MappingProxyType(entrypoints),
        MappingProxyType(dependencies),
        Budgets(**budgets),
        Controls(**controls),
        meta["default"],
        _freeze(meta["config"]),
    )


def discover_benchmarks(root: Path) -> tuple[Benchmark, ...]:
    """List versioned descriptors; legacy manifests belong to the compatibility bridge."""
    if not root.is_dir():
        raise ValueError(f"Benchmark search root is not a directory: {root}")
    found: dict[str, Benchmark] = {}
    for path in sorted(root.glob("*/scenario.json")):
        meta = read_manifest(path)
        if "version" not in meta:
            continue
        benchmark = load_benchmark(root, path.parent)
        if benchmark.name in found:
            raise ValueError(f"Duplicate benchmark id: {benchmark.name}")
        found[benchmark.name] = benchmark
    return tuple(found.values())
