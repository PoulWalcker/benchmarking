"""Compose workflow compilation from explicitly selected trusted benchmark material."""

from dataclasses import dataclass
from pathlib import Path

from sapi_config_lab.benchmark import Benchmark
from sapi_config_lab.coordinate.backend import N8nBackend
from sapi_config_lab.coordinate.benchmark_discovery import LegacyBenchmark, list_benchmarks
from sapi_config_lab.coordinate.legacy_compilation import compilation_files
from sapi_config_lab.paths import workspace_root
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
) -> CompilationContext:
    """Candidate YAML cannot select trusted paths; CLI selection resolves metadata only."""
    if root is None:
        try:
            root = workspace_root() / "benchmarks"
        except RuntimeError as error:
            if scenario is not None:
                raise ValueError("Explicit benchmark selection requires an editable workspace") from error
    items = list_benchmarks(root) if root is not None else ()
    matches = (
        [item for item in items if item.name == scenario]
        if scenario is not None
        else [item for item in items if (item.directory / "config.yaml").resolve() == config.resolve()]
    )
    if scenario is not None and len(matches) != 1:
        raise ValueError("Unknown scenario selection: " + scenario)
    if matches and isinstance(matches[0], Benchmark):
        benchmark = matches[0]
        files = {item.destination: item.source for item in benchmark.files}
        return CompilationContext(bindings or files[benchmark.bindings], files[benchmark.operations].read_text())
    # Historical detached forms retain a fixed installed bundle; no candidate code is imported.
    catalog, source = compilation_files(bindings)
    if matches and isinstance(matches[0], LegacyBenchmark) and bindings is None:
        from sapi_config_lab.benchmark import read_manifest

        metadata = read_manifest(matches[0].directory / "scenario.json")
        if "bindings" in metadata:
            from sapi_config_lab.benchmark import contained_file

            catalog = contained_file(matches[0].directory, metadata["bindings"])
    return CompilationContext(catalog, source)
