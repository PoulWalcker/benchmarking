"""One result shape over the independent verifier and each hosted evaluator.

All read recorded evidence and report execution, acceptance and an optional
quality score (null is never zero); they share no scoring semantics.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable, Iterable
from dataclasses import asdict
import json
import math
from pathlib import Path
from typing import Any

from sapi_config_lab.benchmark_loading import freeze_identity, load_entrypoints
from sapi_config_lab.coordinate.providers import EVALUATORS, ReevaluationOptions
from sapi_config_lab.coordinate.scenarios import SCENARIOS
from sapi_config_lab.evidence import sha256, write_json
from sapi_config_lab.paths import workspace_root

# Recorded trials carry this schema, so its pre-provider name stays.
HOSTED_REPORT = "sapi-lab-upstream-acceptance/v1"
ADMISSION_REPORT = "sapi-lab-admission/v1"
NOT_EVALUATED: dict[str, Any] = {"execution": None, "acceptance": None, "quality": None}


def validate_result(result: Any) -> dict:
    """Validate the normalized boundary without imposing any benchmark's scoring semantics."""
    if not isinstance(result, dict) or not {"execution", "acceptance", "quality"} <= result.keys():
        raise ValueError("Evaluator result requires execution, acceptance and quality")
    for key in ("execution", "acceptance"):
        if result[key] is not None and type(result[key]) is not bool:
            raise ValueError(f"Evaluator result {key} must be boolean or null")
    value = result["quality"]
    if value is None:
        return result
    if not isinstance(value, dict) or not {"status", "score_0_10", "normalized_reward"} <= value.keys():
        raise ValueError("Evaluator quality requires status, score_0_10 and normalized_reward")
    if not isinstance(value["status"], str) or not value["status"]:
        raise ValueError("Evaluator quality status must be a nonempty string")
    for key, maximum in (("score_0_10", 10), ("normalized_reward", 1)):
        number = value[key]
        if value["status"] == "complete":
            if type(number) not in (int, float) or not math.isfinite(number) or not 0 <= number <= maximum:
                raise ValueError(f"Complete evaluator quality requires a finite {key} in [0, {maximum}]")
        elif number is not None:
            raise ValueError("Unscored evaluator quality must retain null scores")
    return result


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


def trial_result(trial: dict) -> dict:
    acceptance = trial["acceptance"] or {}
    if acceptance.get("schema") == HOSTED_REPORT:
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
    if acceptance.get("schema") == HOSTED_REPORT:
        return acceptance["result"]["acceptance"] is True
    return trial["rewards"] == {"reward": 1.0} and acceptance.get("passed") is True


def control_passed(agent: str, trial: dict) -> bool:
    """oracle: the reference is accepted; nop: an absent submission is not, and earns nothing."""
    scenario = SCENARIOS[trial["task_name"]]
    if scenario.hosted:
        result = trial["result"]
        if agent == "nop":
            if result["acceptance"] is not False:
                return False
            quality = result["quality"]
            if quality is None:
                return not trial["exception"] and trial["rewards"] == {"reward": 0.0}
            # Pinned Harbor reports this exact exception when an unscored nop writes no reward.
            exception = trial["exception"] or {}
            return (
                quality.get("status") != "complete"
                and quality.get("score_0_10") is None
                and quality.get("normalized_reward") is None
                and trial["rewards"] is None
                and (not exception or exception.get("exception_type") == "RewardFileNotFoundError")
            )
        expected = scenario.reference_reward
        # Only a declared reference reward makes the benchmark scored; otherwise acceptance alone gates the oracle.
        return trial_accepted(trial) and (
            expected is None
            or ((result["quality"] or {}).get("status") == "complete" and trial["rewards"] == {"reward": expected})
        )
    reward = {"oracle": 1.0, "nop": 0.0}[agent]
    return (
        not trial["exception"]
        and trial["rewards"] == {"reward": reward}
        and (agent == "nop" or (trial["acceptance"] or {}).get("passed") is True)
    )


def hosted_evaluation(scenarios: Iterable[str], judge_model: str | None = None) -> Callable[[str, Path], dict]:
    """Freeze each hosted scenario's evaluator now; a named judge is a paid call the caller reserved."""
    prepared = {
        name: EVALUATORS[SCENARIOS[name].evaluator].prepare(SCENARIOS[name], judge_model)
        for name in scenarios
        if SCENARIOS[name].hosted and SCENARIOS[name].benchmark is None
    }

    def evaluate(scenario: str, record: Path) -> dict:
        trial = json.loads((record / "evidence/trial.json").read_text())
        result = validate_result(prepared[scenario](record))
        report = {
            "schema": HOSTED_REPORT,
            "scenario": scenario,
            "mode": trial["llm_mode"],
            "submission_sha256": trial["submission_sha256"],
            "passed": result["acceptance"],
            "result": result,
            "native_execution": trial.get("native_execution"),
            "terminal_completion": trial.get("terminal_completion"),
        }
        # The report's place is the core's; an evaluator need not have written beside it.
        (record / "evaluation").mkdir(exist_ok=True)
        write_json(record / "evaluation/report.json", report)
        return report

    return evaluate


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


def reevaluate_benchmark(
    record: Path,
    output: Path,
    judgement: Path | None,
    *,
    dispatch: bool = False,
    calibration: str | None = None,
    judge_model: str | None = None,
    reserved: Callable | None = None,
) -> dict:
    """Replay the selected recorded evaluator only after its complete source identity matches."""
    metadata = json.loads((record / "benchmark.json").read_text())
    benchmark = SCENARIOS[metadata["name"]].benchmark
    if benchmark is None:
        raise ValueError("Recorded versioned benchmark is unavailable")
    identity = freeze_identity(benchmark, metadata["options"])
    if json.loads(json.dumps(asdict(identity))) != metadata["identity"]:
        raise ValueError("Recorded benchmark source or options identity differs")
    root = workspace_root()
    for relative, expected in metadata["core_files"].items():
        source = root / ("src" if relative.startswith("sapi_config_lab/") else "") / relative
        if sha256(source) != expected:
            raise ValueError("Recorded trusted core source identity differs: " + relative)
    options = {
        **metadata["options"],
        **metadata.get("runtime_options", {}),
        "evaluation": str(output / "evaluation"),
        "submission": str(record / "evidence/submission.yaml"),
        "identity": {"benchmark": metadata["identity"], "core_files": metadata["core_files"]},
        "dispatch": dispatch,
    }
    contract = record / "evaluation/task-contract.json"
    if contract.is_file():
        options["contract"] = str(contract)
    if judgement is not None:
        options["judgement"] = str(judgement)
    if calibration is not None:
        options.update(calibration=calibration, judge_mode="codex", judge_model=judge_model)
    if dispatch:
        if benchmark.budgets.judge_calls == 0 or reserved is None:
            raise ValueError("Judge dispatch requires a declared cost and host reservation")
        options["reserved_judge"] = reserved
    return validate_result(dict(load_entrypoints(benchmark, identity).evaluate(record / "evidence", options)))


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
    record = args.record.resolve()
    hosted = (record / "evidence/trial.json").is_file()
    if not hosted and (args.judgement or args.dispatch_judge or args.calibration):
        parser.error("The independent verifier has no semantic judge")
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    if (record / "benchmark.json").is_file():
        from sapi_config_lab.coordinate.ledger import open_ledger, parse_ceilings

        def reserved(call: Callable) -> dict:
            metadata = json.loads((record / "benchmark.json").read_text())
            benchmark = SCENARIOS[metadata["name"]].benchmark
            if benchmark is None or benchmark.budgets.judge_calls <= 0:
                raise ValueError("Recorded benchmark has no declared judge cost")
            cost = benchmark.budgets.judge_calls
            ledger = open_ledger(output, args.series_dir, {"judge": cost}, False, parse_ceilings(args.series_ceiling))
            with ledger.reserved("judge", f"{output.name}/judge", cost, output / "result.json", cost) as outcome:
                value = call()
                outcome.passed = value["status"] == "complete"
                return value

        result = reevaluate_benchmark(
            record,
            output,
            args.judgement,
            dispatch=args.dispatch_judge,
            calibration=args.calibration,
            judge_model=args.judge_model,
            reserved=reserved,
        )
    elif hosted:
        scenario = SCENARIOS[json.loads((record / "evidence/trial.json").read_text())["scenario"]]
        evaluator = EVALUATORS[scenario.evaluator]
        options = ReevaluationOptions(
            args.judgement,
            args.dispatch_judge,
            args.calibration,
            args.judge_model,
            args.series_dir,
            tuple(args.series_ceiling or ()),
        )
        if evaluator.judge_calls == 0 and options != ReevaluationOptions():
            raise ValueError("This evaluator does not support judge or calibration options")
        result = validate_result(evaluator.reevaluate(scenario, record, output, options))
    else:
        result = reevaluate_verifier(record, output, args)
    write_json(output / "result.json", result)
    print(json.dumps(result))
    return 0 if result["acceptance"] else 1
