"""Compare a separate semantic judge with predefined evaluator-only controls.

A run of this module COSTS ONE REAL, PAID JUDGE CALL unless --prepare-only is
passed. `main()` is the `sapi-lab benchmark-calibrate` entry point; it writes
the contract and fixture, then dispatches the judge.

`calibration_fixture()` alone makes no model call: it swaps candidate prose in
an already recorded, trusted environment trace, and does not claim the
replacement text was produced by a solver. Expectations are frozen before
judging and never modify the original scorecard or the candidate's official
score.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

from sapi_config_lab.runtime.task_evaluation import Document, FrozenTaskContract, digest
from sapi_config_lab.paths import workspace_root


def calibration_fixture(
    contract: FrozenTaskContract, base_run: Document, case_id: str, *, definitions: Path | None = None
) -> Document:
    contract.verify()
    definition_path = definitions or workspace_root() / "generation/judge-calibration.json"
    catalog = json.loads(definition_path.read_text())
    if catalog.get("schema") != "sapi-lab-judge-calibration/v1":
        raise ValueError("Unsupported calibration schema")
    case = catalog["cases"][case_id]
    task = contract.package["definition"]["id"]
    if base_run["challenge"]["hashes"] != contract.package["hashes"] or base_run["challenge"]["id"] != task:
        raise ValueError("Calibration evidence belongs to a different frozen task")
    checks = base_run["checks"]
    if not checks or any(type(value) is not bool for value in checks.values()):
        raise ValueError("Calibration requires original boolean environment checks")
    if all(checks.values()) != (case["environment"] == "passed"):
        raise ValueError("Calibration environment does not match its predefined control")
    if not {"environment", "verification"} <= {event["source"] for event in base_run["events"]}:
        raise ValueError("Calibration needs observed environment actions and verification, not only a nop")
    run = copy.deepcopy(base_run)
    run["run_id"] += "-calibration-" + case_id
    text = case["text"][task]
    artifacts = (
        [{"name": "incident-summary.md", "media_type": "text/markdown", "content": text}]
        if task == "production-checkout-recovery"
        else []
    )
    run["submission"] = {
        "protocol_version": "1.0",
        "run_id": run["run_id"],
        "status": "completed" if run["termination_reason"] == "completed" else "failed",
        "final_answer": text,
        "artifacts": artifacts,
        "trace": [],
    }
    run["events"] = [event for event in run["events"] if event["source"] != "candidate"]
    run["events"].append(
        {
            "id": "candidate-calibration",
            "source": "candidate",
            "timestamp": run["finished_at"],
            "kind": "final_output",
            "data": {"final_answer": text, "artifacts": artifacts},
        }
    )
    return {
        "schema": "sapi-lab-calibration-fixture/v1",
        "case_id": case_id,
        "definition_digest": digest(catalog),
        "contract_digest": contract.as_dict()["contract_digest"],
        "source_run_log_digest": digest(base_run),
        "run_log_digest": digest(run),
        "counterfactual_candidate_text": True,
        "run_log": run,
        "expected": {key: case[key] for key in ("environment", "semantic_points_band", "answers")},
        "measurement_status": "not_judged",
        "affects_candidate_score": False,
    }


def compare_calibration(fixture: Document, evaluation: Document | None) -> Document:
    """Report measured agreement, failure or unavailable; never turn missing into pass."""
    result: Document = {
        "case_id": fixture["case_id"],
        "expected": fixture["expected"],
        "status": "unscored",
        "passed": None,
        "affects_candidate_score": False,
    }
    if evaluation is None or evaluation.get("status") != "complete":
        return result
    if evaluation.get("evaluation_mode") != "codex":
        return {**result, "status": "simulated_only"}
    if (
        evaluation["run_log_digest"] != fixture["run_log_digest"]
        or evaluation["contract_digest"] != fixture["contract_digest"]
    ):
        raise ValueError("Calibration result does not match its frozen fixture")
    semantic = {row["id"]: row for row in evaluation["criteria"] if row["evaluator"] == "llm"}
    points = sum(row["points"] for row in semantic.values())
    low, high = fixture["expected"]["semantic_points_band"]
    checks = {"semantic_points_band": low <= points <= high}
    checks.update(
        {
            key: key in semantic and semantic[key]["answer"] in answers
            for key, answers in fixture["expected"]["answers"].items()
        }
    )
    return {
        **result,
        "status": "measured",
        "passed": all(checks.values()),
        "semantic_points": points,
        "checks": checks,
        "evaluation_run_id": evaluation["run_id"],
    }


def main():
    """One explicit independent judge dispatch; no repairs or hidden retries.

    Paid unless --prepare-only is passed; see the module docstring.
    """
    import argparse
    import sys
    from datetime import datetime, timezone
    from sapi_config_lab.runtime.task_evaluation import freeze_contract, judge, evaluate, write_evaluation

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--base-run", type=Path, required=True)
    parser.add_argument("--case", choices=["supported-good", "contentless", "misleading-success"], required=True)
    parser.add_argument("--judge-model", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--prepare-only", action="store_true")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    base = json.loads(args.base_run.read_text())
    contract = freeze_contract(args.source, base["challenge"]["id"], judge_model=args.judge_model)
    fixture = calibration_fixture(contract, base, args.case)

    def save(name, value):
        (args.output / name).write_text(json.dumps(value, indent=2) + "\n")

    save("task-contract.json", contract.as_dict())
    save("fixture.json", fixture)
    if args.prepare_only:
        return 0
    # Everything above is unpaid; --prepare-only returns before this point.
    # From here one real judge call is billed against args.judge_model.
    print(f"Dispatching one paid judge call to {args.judge_model}.", file=sys.stderr)
    save(
        "dispatch.json",
        {"attempts": 1, "model": args.judge_model, "started_at": datetime.now(timezone.utc).isoformat()},
    )
    reply = None
    error = None
    try:
        reply = judge(contract, fixture["run_log"], args.output / "judge", timeout=180)
        save("judge-reply.json", reply)
    except Exception as exc:
        error = type(exc).__name__
    result = evaluate(contract, fixture["run_log"], reply, judge_error=error)
    write_evaluation(args.output / "evaluation", result)
    comparison = compare_calibration(fixture, result)
    save("comparison.json", comparison)
    return 0 if comparison.get("passed") is True else 1


if __name__ == "__main__":
    raise SystemExit(main())
