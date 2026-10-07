"""The one place scenario names become hosted provider code."""

from __future__ import annotations

import argparse
from collections.abc import Callable
from dataclasses import dataclass
import json
from pathlib import Path
from typing import TYPE_CHECKING

from sapi_config_lab.coordinate.ledger import open_ledger, parse_ceilings
from sapi_config_lab.evaluate.autowfbench import FrozenTaskContract, evaluate_once, freeze_contract, recorded_run_log
from sapi_config_lab.evaluate.judge_calibration import calibration_fixture, compare_calibration
from sapi_config_lab.evidence import write_json
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


@dataclass(frozen=True)
class HostedEvaluator:
    """What the core needs from an evaluator that reads a hosted trial's recorded evidence."""

    judge_calls: int  # paid model calls one evaluation makes when a judge model is named
    prepare: Callable[[Scenario, str | None], Callable[[Path], dict]]  # freeze before solving; then record -> result
    reevaluate: Callable[[Scenario, Path, Path, argparse.Namespace], dict]  # one recorded trial into a new directory
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


def _autowfbench_result(evaluation: dict, termination_reason: str) -> dict:
    return {
        "execution": termination_reason == "completed",
        "acceptance": evaluation.get("execution_pass") is True,
        "quality": {key: evaluation.get(key) for key in ("status", "score_0_10", "normalized_reward")},
    }


def _autowfbench_prepare(scenario: Scenario, judge_model: str | None) -> Callable[[Path], dict]:
    contract = contract_for(scenario, judge_model)

    def evaluate(record: Path) -> dict:
        trial = json.loads((record / "evidence/trial.json").read_text())
        run_log = recorded_run_log(contract, record / "evidence")
        evaluation = evaluate_once(contract, run_log, record / "evaluation", dispatch=True)
        return _autowfbench_result(evaluation, trial["termination_reason"])

    return evaluate


def _autowfbench_reevaluate(scenario: Scenario, record: Path, output: Path, args: argparse.Namespace) -> dict:
    recorded = json.loads((record / "evaluation/task-contract.json").read_text())
    if args.calibration:
        contract = contract_for(scenario, args.judge_model)
    else:
        contract = contract_for(scenario, recorded["judge"]["model"] if recorded["judge"]["mode"] == "codex" else None)
        if contract.as_dict()["contract_digest"] != recorded["contract_digest"]:
            raise ValueError("The pinned evaluator or judge identity differs from the one this trial was recorded with")
    run_log = recorded_run_log(contract, record / "evidence")
    fixture = None
    if args.calibration:
        fixture = calibration_fixture(contract, run_log, args.calibration)
        write_json(output / "fixture.json", fixture, ensure_ascii=True)
        run_log = fixture["run_log"]
    judgement = json.loads(args.judgement.read_text()) if args.judgement else None
    if args.dispatch_judge and contract.judge_mode == "codex":
        ledger = open_ledger(output, args.series_dir, {"judge": 1}, False, parse_ceilings(args.series_ceiling))
        with ledger.reserved("judge", f"{output.name}/judge", 1, output / "result.json", 1) as outcome:
            evaluation = evaluate_once(contract, run_log, output / "evaluation", dispatch=True)
            outcome.passed = evaluation["status"] == "complete"
    else:
        evaluation = evaluate_once(
            contract, run_log, output / "evaluation", judgement=judgement, dispatch=args.dispatch_judge
        )
    if fixture is not None:
        write_json(output / "comparison.json", compare_calibration(fixture, evaluation), ensure_ascii=True)
    return _autowfbench_result(evaluation, run_log["termination_reason"])


ENVIRONMENTS: dict[str, HostedEnvironment] = {
    "autowfbench": HostedEnvironment(
        evaluators=frozenset({"autowfbench"}),
        limit_seconds=_autowfbench_limit,
        task=_autowfbench_task,
        start=_autowfbench_start,
        modules=("sapi_config_lab/execute/autowfbench.py",),
    ),
}

EVALUATORS: dict[str, HostedEvaluator] = {
    "autowfbench": HostedEvaluator(
        judge_calls=1,
        prepare=_autowfbench_prepare,
        reevaluate=_autowfbench_reevaluate,
        modules=(
            "sapi_config_lab/evaluate/autowfbench.py",
            "sapi_config_lab/evaluate/judge_calibration.py",
            "sapi_config_lab/evaluate/judge-calibration.json",
        ),
    ),
}


def host_only_modules() -> tuple[str, ...]:
    """Sources scrubbed from hosted containers; `rm -rf` ignores a missing path, so tests check each exists."""
    environments = (module for environment in ENVIRONMENTS.values() for module in environment.modules)
    evaluators = (module for evaluator in EVALUATORS.values() for module in evaluator.modules)
    return tuple(dict.fromkeys((*HOST_MODULES, *environments, *evaluators)))
