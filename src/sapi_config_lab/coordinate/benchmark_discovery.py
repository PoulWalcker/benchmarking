"""Explicit metadata selection at command composition; no import-time discovery or dispatch table."""

from collections.abc import Iterable
from pathlib import Path

from sapi_config_lab.benchmark import Benchmark, discover_benchmarks


def select_benchmarks(root: Path, names: Iterable[str] | None = None) -> tuple[Benchmark, ...]:
    """Resolve one command's selection from its explicit search root."""
    available = discover_benchmarks(root)
    selected = tuple(item.name for item in available if item.default) if names is None else tuple(names)
    if not selected or len(set(selected)) != len(selected) or not set(selected) <= {item.name for item in available}:
        raise ValueError("Unknown, duplicate, or empty scenario selection")
    return tuple(item for name in selected for item in available if item.name == name)
