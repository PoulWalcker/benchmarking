"""The benchmark scenarios, discovered from benchmarks/NN-<scenario>/.

A scenario is a task, the environment it runs in and the evaluator that judges
it. Every directory holds `scenario.json`, the reference `config.yaml` and the
container `instruction.md`. A local task adds the public `task.md` and the
evaluator-only `cases.json`; an imported task names its pinned upstream
challenge and adds `authoring-notes.md` and its own `bindings.yaml`.
The number prefix fixes the order within a group.
Registration never extends workflow semantics.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
import json
from pathlib import Path

from sapi_config_lab.paths import CATALOG, workspace_root

GROUPS = ("baseline", "extension", "lifecycle", "expansion", "imported")
# environment -> the evaluator it is judged by
ENVIRONMENTS = {"fixtures": "verifier", "simulator": "upstream"}


@dataclass(frozen=True)
class Scenario:
    name: str
    directory: Path
    group: str
    prompt_extension: str | None = None
    fixture_overlay: str | None = None
    human_review: bool = False
    environment: str = "fixtures"
    evaluator: str = "verifier"
    bindings: Path = CATALOG
    task_source: dict = field(default_factory=lambda: {"source": "local"})
    budgets: dict = field(default_factory=dict)
    output: dict = field(default_factory=dict)
    controls: dict = field(default_factory=dict)

    @property
    def config(self) -> Path:
        return self.directory / "config.yaml"

    @property
    def imported(self) -> bool:
        return self.task_source["source"] != "local"

    def task(self) -> str:
        """The public task text exactly as models receive it (no trailing newline)."""
        return (self.directory / "task.md").read_text().removesuffix("\n")

    def authoring_notes(self) -> str:
        return (self.directory / "authoring-notes.md").read_text().removesuffix("\n")

    def cases(self) -> dict:
        """Evaluator-only fixtures; never staged where a candidate can read them."""
        if self.environment != "fixtures":
            raise ValueError(f"{self.name} runs in a {self.environment} environment, not on fixtures")
        return json.loads((self.directory / "cases.json").read_text())


def _discover() -> dict[str, Scenario]:
    found = {}
    for directory in sorted((workspace_root() / "benchmarks").iterdir()):
        if not (directory / "scenario.json").is_file():
            continue
        meta = json.loads((directory / "scenario.json").read_text())
        name = directory.name.split("-", 1)[1]
        environment = meta.get("environment", "fixtures")
        if (
            name in found
            or meta.get("group") not in GROUPS
            or ENVIRONMENTS.get(environment) != meta.get("evaluator", "verifier")
            or (environment == "simulator") != ("task" in meta)
        ):
            raise ValueError(f"Invalid benchmark definition: {directory.name}")
        found[name] = Scenario(
            name,
            directory,
            meta["group"],
            meta.get("prompt_extension"),
            meta.get("fixture_overlay"),
            meta.get("human_review", False),
            environment,
            ENVIRONMENTS[environment],
            directory / meta["bindings"] if "bindings" in meta else CATALOG,
            meta.get("task", {"source": "local"}),
            meta.get("budgets", {}),
            meta.get("output", {}),
            meta.get("controls", {}),
        )
    return found


SCENARIOS = _discover()
BASELINE_SCENARIOS = {name: s for name, s in SCENARIOS.items() if s.group == "baseline"}


def select_scenarios(names: Iterable[str] | None = None) -> dict[str, Scenario]:
    selected = tuple(BASELINE_SCENARIOS if names is None else names)
    if not selected or len(set(selected)) != len(selected) or any(name not in SCENARIOS for name in selected):
        raise ValueError("Unknown, duplicate, or empty scenario selection")
    return {name: SCENARIOS[name] for name in selected}


def all_cases() -> dict[str, dict]:
    return {name: scenario.cases() for name, scenario in SCENARIOS.items() if scenario.environment == "fixtures"}
