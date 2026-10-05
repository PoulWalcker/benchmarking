"""Select the experiment dependency from this interpreter, never a global tool."""

from importlib.metadata import PackageNotFoundError, version
import sys


def harbor_command() -> list[str]:
    try:
        installed = version("harbor")
    except PackageNotFoundError as error:
        raise RuntimeError("Run uv sync --extra harbor before experiments") from error
    if installed != "0.21.0":
        raise RuntimeError(f"Expected Harbor 0.21.0, got {installed}")
    return [sys.executable, "-c", "from harbor.cli.main import app; app()"]
