"""Counterfactual judge controls built from an already recorded, trusted run; no model call here.

Expectations are frozen before judging and never change an official score.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

from .scoring import Document, FrozenTaskContract, digest

DEFINITIONS = Path(__file__).with_name("judge-calibration.json")


def calibration_fixture(
    contract: FrozenTaskContract, base_run: Document, case_id: str, *, definitions: Path | None = None
) -> Document:
    """Swap candidate prose into a recorded run; the result never claims a solver produced it."""
    contract.verify()
    catalog = json.loads((definitions or DEFINITIONS).read_text())
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
    artifact = contract.artifact
    artifacts = [{"name": artifact.name, "media_type": "text/markdown", "content": text}] if artifact else []
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
