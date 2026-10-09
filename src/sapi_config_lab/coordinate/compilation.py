"""Compose workflow compilation from explicitly selected trusted benchmark material."""

from dataclasses import dataclass
from pathlib import Path

from sapi_config_lab.coordinate.backend import N8nBackend
from sapi_config_lab.coordinate.native_tasks import select_tasks
from sapi_config_lab.paths import benchmark_root
from sapi_config_lab.profile import read_bindings


@dataclass(frozen=True)
class CompilationContext:
    bindings: Path
    operation_source: str

    def backend(self) -> N8nBackend:
        return N8nBackend(self.operation_source)

    def operation_url(self) -> str | None:
        """An inspection artifact needs a placeholder for declared remote tools."""
        return (
            "http://tools/tools"
            if any(b.get("transport") == "http" for b in read_bindings(self.bindings).values())
            else None
        )


def compose_compilation(
    config: Path,
    root: Path | None = None,
    *,
    scenario: str | None = None,
    bindings: Path | None = None,
    operations: Path | None = None,
) -> CompilationContext:
    """Candidate YAML cannot select trusted paths; CLI selection resolves metadata only."""
    if root is None:
        try:
            root = benchmark_root()
        except RuntimeError as error:
            if scenario is not None:
                raise ValueError("Explicit task selection requires an editable task workspace") from error
    items = select_tasks(root, [p.parent.name for p in sorted(root.glob("*/task.toml"))]) if root is not None else ()
    matches = (
        [item for item in items if item.name == scenario]
        if scenario is not None
        else [item for item in items if (item / "solution/config.yaml").resolve() == config.resolve()]
    )
    if scenario is not None and len(matches) != 1:
        raise ValueError("Unknown scenario selection: " + scenario)
    if matches:
        task = matches[0]
        return CompilationContext(bindings or task / "bindings.yaml", (task / "operations.js").read_text())
    if bindings is None or operations is None:
        raise ValueError("Detached compilation requires explicit --bindings and --operations trusted files")
    return CompilationContext(bindings, operations.read_text())
