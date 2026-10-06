"""The benchmark scenarios, discovered from benchmarks/NN-<scenario>/.

Each directory holds one scenario's definition: the reference `config.yaml`,
the public `task.md`, the container `instruction.md`, the evaluator-only
`cases.json`, and `scenario.json` (its group and, for paid series, the per-case
runtime model-call caps). The number prefix fixes the order within a group.
Registration never extends workflow semantics.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
import json
from pathlib import Path

from sapi_config_lab.paths import workspace_root

GROUPS = ("baseline", "extension", "lifecycle", "expansion")


@dataclass(frozen=True)
class Scenario:
    name: str
    directory: Path
    group: str
    runtime_caps: dict[str, int] = field(default_factory=dict)
    prompt_extension: str | None = None

    @property
    def config(self) -> Path:
        return self.directory / "config.yaml"

    def task(self) -> str:
        """The public task text exactly as models receive it (no trailing newline)."""
        return (self.directory / "task.md").read_text().removesuffix("\n")

    def cases(self) -> dict:
        """Evaluator-only fixtures; never staged where a candidate can read them."""
        return json.loads((self.directory / "cases.json").read_text())


def _discover() -> dict[str, Scenario]:
    found = {}
    for directory in sorted((workspace_root() / "benchmarks").iterdir()):
        if not (directory / "scenario.json").is_file():
            continue
        meta = json.loads((directory / "scenario.json").read_text())
        name = directory.name.split("-", 1)[1]
        if name in found or meta.get("group") not in GROUPS:
            raise ValueError(f"Invalid benchmark definition: {directory.name}")
        found[name] = Scenario(
            name, directory, meta["group"], meta.get("runtime_caps", {}), meta.get("prompt_extension")
        )
    return found


SCENARIOS = _discover()
BASELINE_SCENARIOS = {name: s for name, s in SCENARIOS.items() if s.group == "baseline"}
EXPANSION_SCENARIOS = {name: s for name, s in SCENARIOS.items() if s.group == "expansion"}
EXTENSION_SCENARIOS = {name: s for name, s in SCENARIOS.items() if s.group == "extension"}
LIFECYCLE_SCENARIOS = {name: s for name, s in SCENARIOS.items() if s.group == "lifecycle"}


def select_scenarios(names: Iterable[str] | None = None) -> dict[str, Scenario]:
    selected = tuple(BASELINE_SCENARIOS if names is None else names)
    if not selected or len(set(selected)) != len(selected) or any(name not in SCENARIOS for name in selected):
        raise ValueError("Unknown, duplicate, or empty scenario selection")
    return {name: SCENARIOS[name] for name in selected}


def all_cases() -> dict[str, dict]:
    return {name: scenario.cases() for name, scenario in SCENARIOS.items()}
