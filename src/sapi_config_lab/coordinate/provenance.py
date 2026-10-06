"""Hash the public sources actually used by an editable experiment checkout."""

from __future__ import annotations

import hashlib
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
import platform
import sys

from sapi_config_lab.paths import workspace_root

SOURCE_DIRECTORIES = ("src", "tests", "benchmarks", "docs", "generation", "infra", "verification", "harbor", ".github")
SOURCE_FILES = (
    "pyproject.toml",
    "uv.lock",
    ".python-version",
    ".gitignore",
    ".dockerignore",
    "README.md",
    "AGENTS.md",
    "run.sh",
    "run-generation.sh",
    "provenance/SapiensSpecNotation.hs",
    "provenance/spec-comparison.json",
    "provenance/spec-source.json",
    "provenance/autowfbench-source.json",
)
SOURCE_SUFFIXES = {".py", ".js", ".mjs", ".yaml", ".yml", ".json", ".toml", ".md", ".sh", ".txt", ".hs"}


def source_manifest(root: Path | None = None) -> dict[str, str]:
    """Hashes of every public source file; provenance/ is an allowlist, so machine snapshots never count."""
    root = root or workspace_root()
    files = {root / name for name in SOURCE_FILES}
    for directory in SOURCE_DIRECTORIES:
        files.update(
            path
            for path in (root / directory).rglob("*")
            if "__pycache__" not in path.parts and (path.suffix in SOURCE_SUFFIXES or path.name == "Dockerfile")
        )
    return {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(files)
        if path.is_file()
    }


def host_environment() -> dict[str, str | None]:
    """Record versions used by this run without paths, env vars, or credentials."""
    try:
        harbor = version("harbor")
    except PackageNotFoundError:
        harbor = None
    return {
        "python": platform.python_version(),
        "python_implementation": sys.implementation.name,
        "platform": sys.platform,
        "machine": platform.machine(),
        "harbor": harbor,
        "pyyaml": version("PyYAML"),
    }
