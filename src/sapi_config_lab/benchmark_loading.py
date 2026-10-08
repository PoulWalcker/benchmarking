"""Load a selected trusted benchmark snapshot without searching undeclared local code."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
import hashlib
import importlib
from importlib.abc import Loader, MetaPathFinder
from importlib.machinery import ModuleSpec
import inspect
import json
from pathlib import Path
import sys
from types import ModuleType
from typing import Any, cast

from sapi_config_lab.benchmark import Benchmark, contained_file, load_benchmark, python_module
from sapi_config_lab.contracts import RunBinding

# Plans and verdicts retain their existing document schemas; this seam adds no workflow IR.
Plan = Callable[[Path, Mapping[str, Any]], Mapping[str, Any]]
Evaluate = Callable[[Path, Mapping[str, Any]], Mapping[str, Any]]
Prepare = Callable[[Mapping[str, Any]], RunBinding]
Snapshot = Callable[[Mapping[str, Any]], Mapping[str, Any]]


@dataclass(frozen=True)
class BenchmarkIdentity:
    version: str
    benchmark_id: str
    files: tuple[tuple[str, str], ...]
    options_json: str
    sha256: str


@dataclass(frozen=True)
class BenchmarkEntrypoints:
    identity: BenchmarkIdentity
    package: str
    plan: Plan
    evaluate: Evaluate
    prepare: Prepare | None
    snapshot: Snapshot | None


def freeze_identity(benchmark: Benchmark, options: Mapping[str, Any]) -> BenchmarkIdentity:
    """Hash the declared closure at staging, including manifest, dependencies and options."""
    current = load_benchmark(benchmark.directory.parent, benchmark.directory)
    if current != benchmark:
        raise ValueError("Benchmark declaration changed since discovery")
    files = [("scenario.json", hashlib.sha256((benchmark.directory / "scenario.json").read_bytes()).hexdigest())]
    for item in benchmark.files:
        # Revalidate at use: discovery may precede staging by an arbitrarily long time.
        base = benchmark.directory
        relative = item.destination
        if relative.startswith("dependencies/"):
            _, alias, relative = relative.split("/", 2)
            base = benchmark.dependencies[alias]
        source = contained_file(base, relative)
        files.append((item.destination, hashlib.sha256(source.read_bytes()).hexdigest()))
    frozen_files = tuple(sorted(files))

    def json_value(value: Any) -> Any:
        if isinstance(value, Mapping):
            if any(not isinstance(key, str) for key in value):
                raise TypeError("Frozen option object keys must be strings")
            return {key: json_value(item) for key, item in value.items()}
        if isinstance(value, (tuple, list)):
            return [json_value(item) for item in value]
        if value is None or type(value) in (str, int, float, bool):
            return value
        raise TypeError(f"Frozen options require JSON values, got {type(value).__name__}")

    options_json = json.dumps(json_value(options), sort_keys=True, separators=(",", ":"), allow_nan=False)
    digest = hashlib.sha256(
        json.dumps([benchmark.version, benchmark.name, frozen_files, options_json]).encode()
    ).hexdigest()
    return BenchmarkIdentity(benchmark.version, benchmark.name, frozen_files, options_json, digest)


class _SnapshotModules(MetaPathFinder, Loader):
    """One selected namespace backed only by verified, declared Python bytes."""

    def __init__(self, package: str, sources: dict[str, tuple[Path, bytes, bool]]) -> None:
        self.package = package
        self.sources = sources
        self.packages = {package}
        for module in sources:
            parts = module.split(".")
            self.packages.update(".".join(parts[:index]) for index in range(1, len(parts)))
        self.loaded: dict[str, ModuleType] = {}

    def find_spec(self, fullname: str, path: Any = None, target: Any = None) -> ModuleSpec | None:
        if fullname != self.package and not fullname.startswith(self.package + "."):
            return None
        if fullname not in self.sources and fullname not in self.packages:
            raise ModuleNotFoundError(f"Undeclared benchmark module: {fullname}")
        source = self.sources.get(fullname)
        return ModuleSpec(fullname, self, is_package=fullname in self.packages or bool(source and source[2]))

    def create_module(self, spec: ModuleSpec) -> ModuleType | None:
        return None

    def exec_module(self, module: ModuleType) -> None:
        source = self.sources.get(module.__name__)
        if source:
            path, content, _ = source
            module.__file__ = str(path)
            exec(compile(content, str(path), "exec"), module.__dict__)
        self.loaded[module.__name__] = module


def load_entrypoints(benchmark: Benchmark, identity: BenchmarkIdentity) -> BenchmarkEntrypoints:
    """Verify frozen identity before importing only the selected benchmark's callables."""
    if freeze_identity(benchmark, json.loads(identity.options_json)) != identity:
        raise ValueError("Benchmark source/options identity mismatch")
    location = hashlib.sha256(str(benchmark.directory).encode()).hexdigest()
    package = f"_sapi_benchmark_{location}_{identity.sha256}"
    sources: dict[str, tuple[Path, bytes, bool]] = {}
    digests = dict(identity.files)
    for item in benchmark.trusted:
        if item.destination.endswith(".py"):
            content = item.source.read_bytes()
            if hashlib.sha256(content).hexdigest() != digests[item.destination]:
                raise ValueError(f"Benchmark source changed during loading: {item.destination}")
            relative = python_module(item.destination)
            module_name = package + ("." + relative if relative else "")
            sources[module_name] = (item.source, content, item.source.name == "__init__.py")
    existing = [
        finder for finder in sys.meta_path if isinstance(finder, _SnapshotModules) and finder.package == package
    ]
    finder = existing[0] if existing else _SnapshotModules(package, sources)
    for name, module in tuple(sys.modules.items()):
        if (name == package or name.startswith(package + ".")) and finder.loaded.get(name) is not module:
            raise ValueError(f"Conflicting loaded module identity: {name}")
    if not existing:
        sys.meta_path.insert(0, finder)
    resolved: dict[str, Any] = {}
    for role, entry in benchmark.entrypoints.items():
        relative = python_module(entry.path)
        module = importlib.import_module(package + ("." + relative if relative else ""))
        function = getattr(module, entry.symbol, None)
        if not callable(function) or inspect.iscoroutinefunction(function):
            raise ValueError(f"Entrypoint must be a synchronous callable: {role}")
        try:
            inspect.signature(function).bind(*([None] * (2 if role in {"plan", "evaluate"} else 1)))
        except (TypeError, ValueError) as error:
            raise ValueError(f"Invalid entrypoint signature: {role}") from error
        resolved[role] = function
    return BenchmarkEntrypoints(
        identity,
        package,
        cast(Plan, resolved["plan"]),
        cast(Evaluate, resolved["evaluate"]),
        cast(Prepare | None, resolved.get("prepare")),
        cast(Snapshot | None, resolved.get("snapshot")),
    )
