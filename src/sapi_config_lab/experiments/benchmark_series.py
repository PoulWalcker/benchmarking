"""Comparable immutable experiment inventory; controls and missing outcomes stay separate."""

import argparse
import hashlib
import json
from pathlib import Path
from sapi_config_lab.runtime.task_evaluation import summarize_stages


def read(path):
    return json.loads(path.read_text()) if path.exists() else None


def stage_summary(directory):
    directory = Path(directory)
    authors = [read(p.parent / "authoring.json") for p in sorted(directory.glob("authoring-*/dispatch.json"))]
    runtime = []
    for p in sorted(directory.glob("*/runtime-dispatch.jsonl")):
        events = [json.loads(line) for line in p.read_text().splitlines()]
        for attempt in (e for e in events if e.get("event") == "dispatch_attempt"):
            outcome = next(
                (
                    e
                    for e in events
                    if e.get("invocation_id") == attempt["invocation_id"]
                    and e.get("event") in ("completion", "failure")
                ),
                None,
            )
            runtime.append(
                None
                if outcome is None
                else {
                    "status": "completed" if outcome["event"] == "completion" else "failed",
                    "duration_seconds": max(0, outcome["timestamp_unix"] - attempt["timestamp_unix"]),
                    **{k: outcome.get("wrapper", {}).get(k) for k in ("cli_reported_tokens", "provider_call_count")},
                }
            )
    judges = []
    for p in sorted(directory.glob("*/judge-dispatch.json")):
        response = read(p.parent / "judge-reply.json")
        evaluation = read(p.parent / "evaluation/evaluation.json")
        judges.append(
            {**response["provenance"], "status": evaluation.get("status") if evaluation else "error"}
            if response
            else None
        )
    return summarize_stages(authors, runtime, judges)


def collect_series(reports):
    rows = []
    for report_path in reports:
        path = Path(report_path).resolve()
        report = read(path)
        if report is None:
            raise ValueError("Missing immutable report")
        plan = read(path.parent / "plan.json")
        contract = read(path.parent / "task-contract.json")
        rows.append(
            {
                "report": str(path),
                "report_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "task": contract["package"]["definition"]["id"],
                "task_version": contract["package"]["definition"]["version"],
                "task_hashes": contract["package"]["hashes"],
                "source_manifest_sha256": hashlib.sha256(
                    (path.parent / "source-manifest.json").read_bytes()
                ).hexdigest(),
                "image_id": report.get("image_id"),
                "mode": report["mode"],
                "status": report["status"],
                "plan": plan,
                "candidate_evaluation": report.get("candidate_evaluation")
                if (path.parent / "candidate").exists()
                else None,
                "candidate_trial_started": (path.parent / "candidate").exists(),
                "reference_evaluation": report.get("reference_evaluation"),
                "authoring_attempts": report["authoring"],
                "stage_summary": stage_summary(path.parent),
                "full_pipeline_seconds": report.get("full_pipeline_seconds"),
                "actual_model_attempts": report.get("actual_model_attempts"),
            }
        )
    return {
        "schema": "sapi-lab-benchmark-series/v1",
        "runs": rows,
        "task_count": len({r["task"] for r in rows}),
        "independent_live_candidates": sum(r["mode"] == "live" and r["candidate_trial_started"] for r in rows),
        "actual_model_dispatches": sum(r["actual_model_attempts"] or 0 for r in rows),
        "pooled_score": None,
        "statistical_claim": "Pilot cases only; versions, seeds, control roles and missing outcomes remain explicit.",
    }


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("reports", type=Path, nargs="+")
    p.add_argument("--output", type=Path, required=True)
    a = p.parse_args()
    result = collect_series(a.reports)
    a.output.parent.mkdir(parents=True, exist_ok=True)
    with a.output.open("x") as f:
        json.dump(result, f, indent=2)
        f.write("\n")
    print(a.output)
    return 0
