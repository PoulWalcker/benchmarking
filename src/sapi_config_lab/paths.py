"""Installed resources and explicit discovery of the experiment workspace."""

import os
from pathlib import Path


def resource_root() -> Path:
    """Native task assets belong to the editable experiment checkout."""
    return workspace_root()


def benchmark_root() -> Path:
    """The explicitly discoverable benchmark resources for this installation."""
    return resource_root() / "tasks"


def workspace_root() -> Path:
    """The checkout experiments read fixtures from: SAPI_LAB_ROOT, else the working directory or this source tree."""
    explicit = os.environ.get("SAPI_LAB_ROOT")
    candidates = [Path(explicit)] if explicit else [Path.cwd(), *Path.cwd().parents, *Path(__file__).resolve().parents]
    for path in candidates:
        if (path / "tasks").is_dir() and (path / "generation/PROFILE.md").is_file():
            if Path(__file__).resolve().parent != (path / "src/sapi_config_lab").resolve():
                raise RuntimeError(
                    "Experiments require the editable package from this workspace. "
                    "Run uv sync --locked --extra harbor in the checkout; "
                    "an installed wheel may contain different code than the source manifest."
                )
            return path.resolve()
    raise RuntimeError("Experiment workspace not found. Run in the checkout or set SAPI_LAB_ROOT.")


def display_path(path: Path) -> Path:
    """A path as people at this shell can use it: relative inside the working directory, else unchanged."""
    try:
        return path.absolute().relative_to(Path.cwd())
    except OSError, ValueError:
        return path
