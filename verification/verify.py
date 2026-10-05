#!/usr/bin/env python3
"""Task acceptance, deliberately independent from compiler/operation helpers.

Business assertions consume engine-neutral observations. The n8n evidence adapter
separately establishes native execution provenance. The common runtime runner
receives definitions, never expected answers.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
from typing import Any, TYPE_CHECKING
import os
from pathlib import Path

import yaml

from sapi_config_lab.scenarios import SCENARIOS

if TYPE_CHECKING or __package__:
    from .business import check_business_result
    from .extensions import refinement_corruptions, verify_refinement
    from .contracts import Rejected, require
    from .n8n_provenance import check_operation_order, check_provenance, check_rejection, observe_execution, rows
else:  # Harbor executes its copied verifier directly.
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from business import check_business_result
    from extensions import refinement_corruptions, verify_refinement
    from contracts import Rejected, require
    from n8n_provenance import check_operation_order, check_provenance, check_rejection, observe_execution, rows


def check_execution(
    scenario: str, inputs: dict, run: dict, mode: str = "stub", *, config: dict | None = None, case: dict | None = None
) -> dict:
    """Both independent business acceptance and engine provenance must pass."""
    if scenario == "revise-answer":
        if config is None or case is None:
            raise Rejected("Refinement requires submitted config and frozen case")
        require(
            config["workflow"]["id"] == scenario and config["workflow"]["revision"] == 1, "Wrong reply task identity"
        )
        require(config["execution"]["refinement"]["max_attempts"] == 3, "Reply task requires three maximum attempts")
        result = verify_refinement(config, run, mode=mode)
        require(result["exhausted"] == (case.get("expected") == "exhausted"), "Wrong refinement business outcome")
        return {key: value for key, value in result.items() if key != "calls"}
    observation, records = observe_execution(scenario, inputs, run, mode, config=config)
    result = check_business_result(scenario, inputs, observation, mode, case=case)
    check_operation_order(scenario, inputs, records, observation.roles)
    return {**result, "n8n_node_count": len(run["run_data"]), "engine_provenance_verified": True}


def record_acceptance(run: dict, artifact_dir: Path, passed: bool, reason: str | None = None) -> dict:
    """Attach an independent acceptance decision to a newly executed case."""
    decision: dict[str, Any] = {"status": "accepted" if passed else "rejected", "passed": passed}
    if reason:
        decision["reason"] = reason
    run["acceptance"] = decision
    artifact_dir.mkdir(parents=True, exist_ok=True)
    (artifact_dir / "case.json").write_text(json.dumps(run, ensure_ascii=False, indent=2) + "\n")
    return decision


def execution_summary(run: dict) -> dict:
    return {
        "execution": run.get("execution", {"status": run.get("status"), "succeeded": run.get("status") == "success"}),
        "input": run.get("input"),
        "llm": run.get("llm"),
    }


def corruption_checks(
    scenario: str, inputs: dict, run: dict, mode: str, *, config: dict | None = None, case: dict | None = None
) -> list[str]:
    """Deliberately corrupt results to test that acceptance cannot be vacuous."""
    mutations = []

    def output_broken(candidate: dict) -> None:
        record = candidate["run_data"]["Result"][0]
        final = rows(record)[0]
        if scenario == "invoice-total":
            final["output"]["total_minor"] += 1
        elif scenario == "ticket-routing":
            final["output"]["action"] = "normal_reply" if final["output"]["action"] == "escalate" else "escalate"
        elif scenario == "competitor-report":
            final["output"]["evidence"] = final["output"]["evidence"][:1]
        else:
            final["output"] = {"deliberately_wrong": True}
        candidate["output"] = copy.deepcopy(final["output"])
        candidate["result"] = copy.deepcopy(final)

    mutations.append(("wrong-final-output", output_broken))

    def absent_execution(candidate: dict) -> None:
        candidate["run_data"].pop("Result")

    mutations.append(("output-without-real-result-node", absent_execution))

    def false_status(candidate: dict) -> None:
        final = rows(candidate["run_data"]["Result"][0])[0]
        final["trace"][0]["status"] = "skipped"

    mutations.append(("falsified-step-trace", false_status))
    if scenario == "competitor-report":

        def missing_branch(candidate: dict) -> None:
            event = next(
                event
                for event in rows(candidate["run_data"]["Result"][0])[0]["trace"]
                if event["operation"] == "research.marketing"
            )
            candidate["run_data"].pop(candidate["mapping"][event["step_id"]])

        mutations.append(("missing-analysis-execution", missing_branch))

        def contentless_report(candidate: dict) -> None:
            final = rows(candidate["run_data"]["Result"][0])[0]
            final["output"]["report"] = "An unrelated but nonempty report."
            writer = next(event for event in final["trace"] if event["operation"] == "research.write")
            envelope = rows(candidate["run_data"][candidate["mapping"][writer["step_id"]]][0])[0]
            envelope["steps"][writer["step_id"]]["report"] = final["output"]["report"]
            final["steps"][writer["step_id"]]["report"] = final["output"]["report"]
            candidate["output"] = copy.deepcopy(final["output"])
            candidate["result"] = copy.deepcopy(final)

        mutations.append(("contentless-report-with-preserved-evidence", contentless_report))
    accepted = []
    for name, mutate in mutations:
        candidate = copy.deepcopy(run)
        mutate(candidate)
        try:
            check_execution(scenario, inputs, candidate, mode, config=config, case=case)
        except Rejected, KeyError, TypeError, IndexError:
            accepted.append(name)
        else:
            raise Rejected("Verifier accepted deliberate corruption: " + name)
    return accepted


def invalid_configs(config: dict):
    bad = copy.deepcopy(config)
    bad["workflow"]["steps"][0]["uses"] = "unregistered.must_be_rejected"
    yield "unknown-operation", bad
    bad = copy.deepcopy(config)
    edges = bad["workflow"].setdefault("dependencies", [])
    if edges:
        first, second = edges[0]
        edges.append([second, first])
    else:
        ids = [step["id"] for step in bad["workflow"]["steps"]]
        edges.extend([[ids[0], ids[-1]], [ids[-1], ids[0]]] if len(ids) > 1 else [[ids[0], ids[0]]])
    for step in bad["workflow"]["steps"]:
        if sum(target == step["id"] for _, target in edges) > 1:
            step["join"] = "all_terminal"
    yield "cycle", bad
    bad = copy.deepcopy(config)
    bad["execution"]["concurrency"] = "required_parallel"
    yield "unsupported-required-parallel", bad


class UniqueLoader(yaml.SafeLoader):
    pass


def unique_mapping(loader: UniqueLoader, node, deep=False):
    output = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in output:
            raise Rejected("Duplicate YAML key: " + str(key))
        output[key] = loader.construct_object(value_node, deep=deep)
    return output


UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, unique_mapping)


def verify_submission(
    scenario: str,
    config_path: Path,
    report_dir: Path,
    mode: str = "stub",
    bridge_url: str | None = None,
    selected_case: str | None = None,
    *,
    runner=None,
) -> dict:
    if scenario == "daily-digest":
        if TYPE_CHECKING or __package__:
            from .lifecycle_submission import verify_submission as verify_digest
        else:
            from lifecycle_submission import verify_submission as verify_digest
        return verify_digest(config_path, report_dir, mode=mode)
    report_dir.mkdir(parents=True, exist_ok=True)
    report: dict[str, Any] = {
        "scenario": scenario,
        "mode": mode,
        "passed": False,
        "report_schema": "sapi-lab-verification/v1",
        "passed_means": "All expected positive acceptances and deliberate rejection probes passed",
        "cases": [],
        "limits": [
            "Checks the supported static sapi-lab/v0 profile only.",
            "Live report checks source evidence and dataflow; natural-language semantic completeness is not proven.",
        ],
    }
    try:
        require(scenario in SCENARIOS, "Unknown acceptance scenario")
        submission = config_path.read_bytes()
        report["submission_sha256"] = hashlib.sha256(submission).hexdigest()
        (report_dir / "submission.yaml").write_bytes(submission)
        expected_submission = os.environ.get("SAPI_EXPECTED_SUBMISSION_SHA256")
        require(
            not expected_submission or report["submission_sha256"] == expected_submission,
            "Container submission hash mismatch",
        )
        report["config_transformations"] = ["workflow.inputs replaced by case fixture"] + (
            ["execution.deadline_seconds set to 600"] if mode == "live" else []
        )
        config = yaml.load(submission, Loader=UniqueLoader)
        require(isinstance(config, dict), "Submission must be a YAML object")
        if runner is None:
            from sapi_config_lab.runtime.execution import run_case

            runner = run_case
        cases = json.loads((Path(__file__).parent / "cases.json").read_text())[scenario]
        for kind in ("positive", "negative"):
            if mode == "live" and kind == "negative":
                continue  # live schema failures are a separate bridge test, not this stub diagnostic contract
            for case in cases[kind]:
                if (
                    mode == "live"
                    and scenario == "ticket-routing"
                    and case["name"] not in {"high-three-days", "normal-boundary-two"}
                ):
                    continue
                if mode == "live" and cases.get("live_cases") and case["name"] not in cases["live_cases"]:
                    continue
                if selected_case and case["name"] != selected_case:
                    continue
                candidate = copy.deepcopy(config)
                candidate["workflow"]["inputs"] = case["inputs"]
                if mode == "live":
                    candidate["execution"]["deadline_seconds"] = 600
                artifact_dir = report_dir / "cases" / case["name"]
                run = runner(candidate, artifact_dir, llm_mode=mode, bridge_url=bridge_url)
                if "mapping" not in run and (artifact_dir / "mapping.json").exists():
                    run["mapping"] = json.loads((artifact_dir / "mapping.json").read_text())
                row = {
                    "name": case["name"],
                    "kind": kind,
                    "passed": False,
                    "execution_id": run.get("execution_id"),
                    "workflow_id": run.get("workflow_id"),
                    "n8n_version": run.get("n8n_version"),
                    "artifacts": str(artifact_dir),
                }
                row["deadline_seconds"] = candidate["execution"]["deadline_seconds"]
                if (artifact_dir / "config.json").exists():
                    row["config_sha256"] = hashlib.sha256((artifact_dir / "config.json").read_bytes()).hexdigest()
                report["cases"].append(row)
                row.update(execution_summary(run))
                try:
                    if kind == "positive":
                        row.update(check_execution(scenario, case["inputs"], run, mode, config=candidate, case=case))
                        row["acceptance"] = record_acceptance(
                            run,
                            artifact_dir,
                            not row.get("exhausted", False),
                            "Expected refinement exhaustion; no accepted reply" if row.get("exhausted") else None,
                        )
                        row["verifier_corruptions_rejected"] = (
                            refinement_corruptions(candidate, run, mode=mode)
                            if scenario == "revise-answer"
                            else corruption_checks(scenario, case["inputs"], run, mode, config=candidate, case=case)
                        )
                        row["output"] = run["output"]
                    else:
                        check_rejection(run, case, candidate)
                        row["acceptance"] = record_acceptance(
                            run, artifact_dir, False, "Expected invalid input rejection"
                        )
                        row["expected_error"] = case["error"]
                except Rejected as error:
                    row["acceptance"] = record_acceptance(run, artifact_dir, False, str(error))
                    raise
                row["passed"] = True
        if mode == "stub" and not selected_case:
            # Mutate compiled code, import and execute it successfully in n8n,
            # then require independent acceptance to reject its wrong data.
            def wrong_result(artifact):
                for node in artifact["nodes"]:
                    if node["name"] == "Result":
                        original = node["parameters"]["jsCode"]
                        node["parameters"]["jsCode"] = (
                            "const result = await (async () => {\n"
                            + original
                            + "\n})();\nresult[0].json.output = {deliberately_wrong: true};\nreturn result;"
                        )
                return artifact

            artifact_dir = report_dir / "cases" / "mutated-generated-result"
            candidate = copy.deepcopy(config)
            candidate["workflow"]["inputs"] = cases["positive"][0]["inputs"]
            run = runner(candidate, artifact_dir, llm_mode=mode, artifact_transform=wrong_result)
            row = {
                "name": "wrong-result-in-generated-json",
                "kind": "mutated-generated-workflow",
                "passed": False,
                "execution_id": run.get("execution_id"),
                "workflow_id": run.get("workflow_id"),
                "n8n_version": run.get("n8n_version"),
                "artifacts": str(artifact_dir),
            }
            row.update(execution_summary(run))
            report["cases"].append(row)
            check_provenance(run)
            require(run.get("status") == "success", "Broken-output probe did not complete n8n execution")
            try:
                check_execution(
                    scenario, candidate["workflow"]["inputs"], run, mode, config=candidate, case=cases["positive"][0]
                )
            except Rejected as error:
                row["rejection"] = str(error)
                row["acceptance"] = record_acceptance(run, artifact_dir, False, str(error))
                row["passed"] = True
            else:
                raise Rejected("Verifier accepted deliberately wrong generated workflow output")
            for name, bad in invalid_configs(config):
                artifact_dir = report_dir / "cases" / ("invalid-yaml-" + name)
                run = runner(bad, artifact_dir, llm_mode=mode, bridge_url=bridge_url)
                row = {
                    "name": name,
                    "kind": "invalid-definition",
                    "passed": False,
                    "artifacts": str(artifact_dir),
                    **execution_summary(run),
                }
                row["acceptance"] = record_acceptance(run, artifact_dir, False, "Definition rejected before execution")
                report["cases"].append(row)
                require(
                    run.get("status") == "compile_error", "Compiler accepted invalid/unsupported definition: " + name
                )
                require(
                    not run.get("workflow_id") and not run.get("execution_id"),
                    "Rejected definition was imported or executed",
                )
                row["passed"] = True
        require(bool(report["cases"]), "No test cases selected")
        report["passed"] = all(row["passed"] for row in report["cases"])
    except Exception as error:
        report["error"] = str(error)
        report["error_type"] = type(error).__name__
    (report_dir / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", choices=sorted(SCENARIOS), required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--report-dir", type=Path, required=True)
    parser.add_argument("--mode", choices=["stub", "live"], default=os.environ.get("SAPI_LLM_MODE", "stub"))
    parser.add_argument("--bridge-url", default=os.environ.get("SAPI_BRIDGE_URL"))
    parser.add_argument("--case", default=os.environ.get("SAPI_CASE_NAME"))
    args = parser.parse_args()
    report = verify_submission(args.scenario, args.config, args.report_dir, args.mode, args.bridge_url, args.case)
    print(
        json.dumps(
            {
                "scenario": report["scenario"],
                "passed": report["passed"],
                "case_count": len(report["cases"]),
                "error": report.get("error"),
            },
            ensure_ascii=False,
        )
    )
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
