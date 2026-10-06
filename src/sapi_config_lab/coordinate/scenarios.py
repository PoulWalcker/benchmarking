"""The benchmark registry: every scenario is benchmarks/NN-<name>/scenario.json plus its files."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any

from sapi_config_lab.contracts import OutputArtifact
from sapi_config_lab.paths import CATALOG, workspace_root

# environment -> the evaluator that judges it
EVALUATORS = {"fixtures": "verifier", "simulator": "upstream"}
FIELDS = {
    "environment",
    "evaluator",
    "default",
    "provenance",
    "workflow_id",
    "bindings",
    "budgets",
    "output",
    "controls",
    "prompt_extension",
    "fresh_fixtures",
    "human_review",
}


@dataclass(frozen=True)
class Provenance:
    """Where an imported task comes from: a pinned source under provenance/ and its challenge."""

    source: str
    challenge: str


@dataclass(frozen=True)
class Scenario:
    name: str
    directory: Path
    environment: str
    evaluator: str
    default: bool = False
    provenance: Provenance | None = None
    workflow_id: str | None = None
    bindings: Path = CATALOG
    authoring_attempts: int | None = None
    runtime_model_calls: int | None = None
    artifact: OutputArtifact | None = None
    reference_reward: float | None = None
    prompt_extension: str | None = None
    fresh_fixtures: bool = False
    human_review: bool = False

    @property
    def config(self) -> Path:
        return self.directory / "config.yaml"

    @property
    def hosted(self) -> bool:
        """The host serves this scenario's environment and evaluator for each trial."""
        return self.environment == "simulator"

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


def load_scenario(directory: Path) -> Scenario:
    meta: dict[str, Any] = json.loads((directory / "scenario.json").read_text())
    environment = str(meta.get("environment"))
    budgets, output, controls = meta.get("budgets", {}), meta.get("output"), meta.get("controls", {})
    provenance = meta.get("provenance")
    if (
        set(meta) - FIELDS
        or EVALUATORS.get(environment) != meta.get("evaluator")
        or set(budgets) - {"authoring_attempts", "runtime_model_calls"}
        or set(controls) - {"reference_reward"}
        or (environment == "simulator")
        != bool(provenance and "workflow_id" in meta and "runtime_model_calls" in budgets)
    ):
        raise ValueError(f"Invalid benchmark definition: {directory.name}")
    return Scenario(
        name=directory.name.split("-", 1)[1],
        directory=directory,
        environment=environment,
        evaluator=meta["evaluator"],
        default=meta.get("default", False),
        provenance=Provenance(**provenance) if provenance else None,
        workflow_id=meta.get("workflow_id"),
        bindings=directory / meta["bindings"] if "bindings" in meta else CATALOG,
        authoring_attempts=budgets.get("authoring_attempts"),
        runtime_model_calls=budgets.get("runtime_model_calls"),
        artifact=OutputArtifact(output["artifact_field"], output["artifact_name"]) if output else None,
        reference_reward=controls.get("reference_reward"),
        prompt_extension=meta.get("prompt_extension"),
        fresh_fixtures=meta.get("fresh_fixtures", False),
        human_review=meta.get("human_review", False),
    )


def _discover() -> dict[str, Scenario]:
    found: dict[str, Scenario] = {}
    for directory in sorted((workspace_root() / "benchmarks").iterdir()):
        if (directory / "scenario.json").is_file():
            scenario = load_scenario(directory)
            if scenario.name in found:
                raise ValueError(f"Duplicate benchmark name: {scenario.name}")
            found[scenario.name] = scenario
    return found


SCENARIOS = _discover()
DEFAULT_SCENARIOS = {name: s for name, s in SCENARIOS.items() if s.default}


def select_scenarios(names: Iterable[str] | None = None) -> dict[str, Scenario]:
    selected = tuple(DEFAULT_SCENARIOS if names is None else names)
    if not selected or len(set(selected)) != len(selected) or any(name not in SCENARIOS for name in selected):
        raise ValueError("Unknown, duplicate, or empty scenario selection")
    return {name: SCENARIOS[name] for name in selected}


def scenario_for_challenge(challenge: str) -> Scenario:
    return next(s for s in SCENARIOS.values() if s.provenance and s.provenance.challenge == challenge)


def all_cases() -> dict[str, dict]:
    return {name: scenario.cases() for name, scenario in SCENARIOS.items() if scenario.environment == "fixtures"}
