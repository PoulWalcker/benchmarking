"""Hash the public sources actually used by an editable experiment checkout."""

from __future__ import annotations

import hashlib
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
import platform
import sys

from sapi_config_lab.paths import resource_root

SOURCE_DIRECTORIES = (
    "src",
    "tests",
    "tasks",
    "docs",
    "generation",
    "infra",
    "verification",
    ".github",
)
SOURCE_FILES = (
    "pyproject.toml",
    "uv.lock",
    ".python-version",
    ".gitignore",
    ".dockerignore",
    "README.md",
    "NATIVE_PARITY.md",
    "AGENTS.md",
    "run.sh",
    "run-generation.sh",
    "provenance/SapiensSpecNotation.hs",
    "provenance/spec-comparison.json",
    "provenance/spec-source.json",
)
SOURCE_SUFFIXES = {
    ".py",
    ".js",
    ".mjs",
    ".yaml",
    ".yml",
    ".json",
    ".toml",
    ".md",
    ".sh",
    ".txt",
    ".hs",
    ".dockerignore",
}


def source_manifest(root: Path | None = None) -> dict[str, str]:
    """Hashes of every public source file; provenance/ is an allowlist, so machine snapshots never count."""
    root = root or resource_root()
    files = {root / name for name in SOURCE_FILES}
    files.update(path for path in (root / "tasks").rglob("*") if path.is_file() and "__pycache__" not in path.parts)
    for directory in SOURCE_DIRECTORIES:
        files.update(
            path
            for path in (root / directory).rglob("*")
            if "__pycache__" not in path.parts and (path.suffix in SOURCE_SUFFIXES or path.name == "Dockerfile")
        )
    result = {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(files)
        if path.is_file()
    }
    return result


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
