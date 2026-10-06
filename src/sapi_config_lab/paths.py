"""Installed resources and explicit discovery of the experiment workspace."""

import os
from pathlib import Path

CATALOG = Path(__file__).resolve().parent / "bindings.yaml"


def workspace_root() -> Path:
    """Experiments need fixtures/templates; the installed compiler does not.

    SAPI_LAB_ROOT selects a checkout explicitly. Otherwise discover it from
    the working directory or an editable source installation. Never create one.
    """
    explicit = os.environ.get("SAPI_LAB_ROOT")
    candidates = [Path(explicit)] if explicit else [Path.cwd(), *Path.cwd().parents, *Path(__file__).resolve().parents]
    for path in candidates:
        if (path / "benchmarks").is_dir() and (path / "generation/PROFILE.md").is_file():
            if Path(__file__).resolve().parent != (path / "src/sapi_config_lab").resolve():
                raise RuntimeError(
                    "Experiments require the editable package from this workspace. "
                    "Run uv sync --locked --extra harbor in the checkout; "
                    "an installed wheel may contain different code than the source manifest."
                )
            return path.resolve()
    raise RuntimeError("Experiment workspace not found. Run in the checkout or set SAPI_LAB_ROOT.")
