"""The one place scenario names become hosted provider code."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from sapi_config_lab.evaluate.autowfbench import FrozenTaskContract, freeze_contract
from sapi_config_lab.execute.autowfbench import start_environment
from sapi_config_lab.execute.host import HostConfig
from sapi_config_lab.execute.hosting import EnvironmentSession
from sapi_config_lab.pinned_source import pinned_source

if TYPE_CHECKING:
    # scenarios.py validates against ENVIRONMENTS at import time.
    from sapi_config_lab.coordinate.scenarios import Scenario


# A hosted task's environment and evaluator run on the host; its container must not be able to read them.
HOST_MODULES = (
    "sapi_config_lab/coordinate/evaluation.py",
    "sapi_config_lab/coordinate/providers.py",
    "sapi_config_lab/execute/hosting.py",
    "sapi_config_lab/pinned_source.py",
)


@dataclass(frozen=True)
class HostedEnvironment:
    """What the core needs from a provider that serves a fresh world per trial."""

    evaluators: frozenset[str]  # evaluators that can read its evidence
    limit_seconds: Callable[[Scenario], int]  # trial wall clock: staging bounds, admission deadline, prompt
    task: Callable[[Scenario], str]  # public task text for the authoring prompt
    start: Callable[[Scenario, int, HostConfig], EnvironmentSession]  # one session for (scenario, seed)
    modules: tuple[str, ...]  # host-only sources scrubbed from hosted containers


def contract_for(scenario: Scenario, judge_model: str | None = None) -> FrozenTaskContract:
    """The frozen upstream task, rubric and judge identity; the simulated judge unless a model is named."""
    if scenario.provenance is None:
        raise ValueError(f"{scenario.name} has no pinned upstream task")
    return freeze_contract(
        pinned_source(scenario.provenance.source),
        scenario.provenance.challenge,
        judge_model=judge_model,
        judge_mode="codex" if judge_model else "demo",
        artifact=scenario.artifact,
    )


def _autowfbench_limit(scenario: Scenario) -> int:
    return contract_for(scenario).package["definition"]["limits"]["wall_clock_seconds"]


def _autowfbench_task(scenario: Scenario) -> str:
    definition = contract_for(scenario).package["definition"]
    return definition["task"] + "\n" + definition["completion"]


def _autowfbench_start(scenario: Scenario, seed: int, host: HostConfig) -> EnvironmentSession:
    if scenario.provenance is None:
        raise ValueError(f"{scenario.name} has no pinned upstream task")
    return start_environment(
        pinned_source(scenario.provenance.source),
        scenario.provenance.challenge,
        seed,
        bind_host=host.listen_host,
        public_host=host.container_host,
    )


ENVIRONMENTS: dict[str, HostedEnvironment] = {
    "autowfbench": HostedEnvironment(
        evaluators=frozenset({"upstream"}),
        limit_seconds=_autowfbench_limit,
        task=_autowfbench_task,
        start=_autowfbench_start,
        modules=(
            "sapi_config_lab/execute/autowfbench.py",
            "sapi_config_lab/evaluate/autowfbench.py",
            "sapi_config_lab/evaluate/judge_calibration.py",
            "sapi_config_lab/evaluate/judge-calibration.json",
        ),
    ),
}


def host_only_modules() -> tuple[str, ...]:
    """Sources scrubbed from hosted containers; `rm -rf` ignores a missing path, so tests check each exists."""
    provided = (module for environment in ENVIRONMENTS.values() for module in environment.modules)
    return tuple(dict.fromkeys((*HOST_MODULES, *provided)))
