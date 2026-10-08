"""Bounded compatibility context for historical compile and UI forms."""

from pathlib import Path

from sapi_config_lab.paths import CATALOG


def compilation_files(bindings: Path | None = None) -> tuple[Path, str]:
    """Use only the installed historical bundle, never candidate-nominated executable code."""
    source = Path(__file__).resolve().parents[1] / "compile/operations.js"
    return bindings or CATALOG, source.read_text()
