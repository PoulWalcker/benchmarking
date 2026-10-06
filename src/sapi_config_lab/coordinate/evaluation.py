"""One result shape over two evaluators, the independent verifier and the pinned upstream scorer.

Both read recorded evidence and report execution, acceptance and an optional
quality score (null is never zero); they share no scoring semantics.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable
import json
from pathlib import Path
from typing import Any

from sapi_config_lab.coordinate.ledger import open_ledger, parse_ceilings
from sapi_config_lab.coordinate.scenarios import SCENARIOS, Scenario, scenario_for_challenge
from sapi_config_lab.evaluate.judge_calibration import calibration_fixture, compare_calibration
from sapi_config_lab.evaluate.task_evaluation import (
    FrozenTaskContract,
    evaluate_once,
    freeze_contract,
    recorded_run_log,
)
from sapi_config_lab.evidence import write_json
from sapi_config_lab.pinned_source import pinned_source

UPSTREAM_REPORT = "sapi-lab-upstream-acceptance/v1"
ADMISSION_REPORT = "sapi-lab-admission/v1"
NOT_EVALUATED: dict[str, Any] = {"execution": None, "acceptance": None, "quality": None}


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


def quality(evaluation: dict | None) -> dict | None:
    if evaluation is None:
        return None
    return {key: evaluation.get(key) for key in ("status", "score_0_10", "normalized_reward")}


def verifier_result(report: dict | None, rubric: dict | None) -> dict:
    if not report:
        return dict(NOT_EVALUATED)
    executed = [
        row.get("execution", {}).get("succeeded") for row in report.get("cases", []) if row.get("kind") == "positive"
    ]
    return {
        "execution": all(value is True for value in executed) if executed else None,
        "acceptance": report.get("passed") is True,
        "quality": quality(rubric),
    }


def upstream_result(evaluation: dict, termination_reason: str) -> dict:
    return {
        "execution": termination_reason == "completed",
        "acceptance": evaluation.get("execution_pass") is True,
        "quality": quality(evaluation),
    }


def trial_result(trial: dict) -> dict:
    acceptance = trial["acceptance"] or {}
    if acceptance.get("schema") == UPSTREAM_REPORT:
        return acceptance["result"]
    if acceptance.get("schema") == ADMISSION_REPORT:
        return {**NOT_EVALUATED, "acceptance": acceptance.get("passed") is True}
    rubric = Path(trial["result_path"]).parent / "verifier/evaluation/evaluation.json"
    return verifier_result(acceptance, json.loads(rubric.read_text()) if rubric.exists() else None)


def trial_accepted(trial: dict) -> bool:
    """No Harbor exception, and the scenario's evaluator accepted the trial."""
    acceptance = trial["acceptance"] or {}
    if trial["exception"]:
        return False
    if acceptance.get("schema") == UPSTREAM_REPORT:
        return acceptance["result"]["acceptance"] is True
    return trial["rewards"] == {"reward": 1.0} and acceptance.get("passed") is True


def control_passed(agent: str, trial: dict) -> bool:
    """oracle: the reference is accepted; nop: an absent submission is not, and earns nothing."""
    scenario = SCENARIOS[trial["task_name"]]
    if scenario.evaluator == "upstream":
        result = trial["result"]
        if agent == "nop":
            return result["acceptance"] is False and (result["quality"] or {}).get("normalized_reward") is None
        expected = scenario.reference_reward
        return (
            trial_accepted(trial)
            and result["quality"]["status"] == "complete"
            and (expected is None or trial["rewards"] == {"reward": expected})
        )
    reward = {"oracle": 1.0, "nop": 0.0}[agent]
    return (
        not trial["exception"]
        and trial["rewards"] == {"reward": reward}
        and (agent == "nop" or (trial["acceptance"] or {}).get("passed") is True)
    )


def upstream_evaluate(contracts: dict[str, FrozenTaskContract]) -> Callable[[str, Path], dict]:
    """Evaluate a recorded simulator trial once; a codex judge here is a paid call the caller reserved."""

    def evaluate(scenario: str, record: Path) -> dict:
        contract = contracts[scenario]
        trial = json.loads((record / "evidence/trial.json").read_text())
        evaluation = evaluate_once(
            contract, recorded_run_log(contract, record / "evidence"), record / "evaluation", dispatch=True
        )
        result = upstream_result(evaluation, trial["termination_reason"])
        report = {
            "schema": UPSTREAM_REPORT,
            "scenario": scenario,
            "mode": trial["llm_mode"],
            "submission_sha256": trial["submission_sha256"],
            "passed": result["acceptance"],
            "result": result,
        }
        write_json(record / "evaluation/report.json", report)
        return report

    return evaluate


def reevaluate_upstream(record: Path, output: Path, args: argparse.Namespace) -> dict:
    trial = json.loads((record / "evidence/trial.json").read_text())
    scenario = scenario_for_challenge(trial["challenge"])
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
    return upstream_result(evaluation, run_log["termination_reason"])


def reevaluate_verifier(record: Path, output: Path, args: argparse.Namespace) -> dict:
    from sapi_config_lab.coordinate.live_evidence import load_verifier

    verification, _ = load_verifier()
    plan = json.loads((record / "plan.json").read_text())
    scenario = SCENARIOS[plan["scenario"]]
    cases = json.loads((args.cases or scenario.directory / "cases.json").read_text())
    cases = cases.get(scenario.name, cases)
    selected = plan["entries"][0]["name"] if plan["mode"] == "live" and len(plan["entries"]) == 1 else None
    report = verification.evaluate(
        scenario.name,
        record / "evidence/submission.yaml",
        record / "evidence",
        cases,
        plan["mode"],
        selected,
        evaluation=output / "evaluation",
    )
    rubric = output / "evaluation/evaluation.json"
    return verifier_result(report, json.loads(rubric.read_text()) if rubric.exists() else None)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate one recorded trial again into a new directory.")
    parser.add_argument("--record", type=Path, required=True, help="<trial>/verifier, or environments/<job>/<scenario>")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--cases", type=Path, help="Verifier fixtures the trial ran with (its staged tests/cases.json)")
    parser.add_argument("--judgement", type=Path, help="A saved judge reply for exactly this judge and run")
    parser.add_argument("--dispatch-judge", action="store_true", help="Call the judge. A codex judge costs one call")
    parser.add_argument("--calibration", help="Judge a predefined counterfactual of the recorded run instead")
    parser.add_argument("--judge-model", help="Judge identity for --calibration")
    parser.add_argument("--series-dir", type=Path, help="Reserve the judge call in a shared ledger")
    parser.add_argument("--series-ceiling", action="append")
    args = parser.parse_args(argv)
    if args.calibration and not args.judge_model:
        parser.error("--calibration needs --judge-model")
    if args.judgement and args.dispatch_judge:
        parser.error("Supply a saved judgement or dispatch the judge, not both")
    upstream = (args.record / "evidence/trial.json").is_file()
    if not upstream and (args.judgement or args.dispatch_judge or args.calibration):
        parser.error("The independent verifier has no semantic judge")
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    result = (reevaluate_upstream if upstream else reevaluate_verifier)(args.record.resolve(), output, args)
    write_json(output / "result.json", result)
    print(json.dumps(result))
    return 0 if result["acceptance"] else 1
