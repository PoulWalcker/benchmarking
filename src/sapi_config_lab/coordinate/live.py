#!/usr/bin/env python3
"""Live replay of saved submissions: one bounded Agency grant per case, after unpaid controls."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import time
import tomllib
from typing import Any

from sapi_config_lab.coordinate.controls import trial_accepted
from sapi_config_lab.coordinate.ledger import open_ledger, parse_ceilings
from sapi_config_lab.coordinate.live_evidence import case_budget, collect_native, live_cohort, reconcile_dispatches
from sapi_config_lab.coordinate.replay import load_selection, read_json, require
from sapi_config_lab.coordinate.runs import Run, run_experiment
from sapi_config_lab.coordinate.scenarios import SCENARIOS, select_scenarios
from sapi_config_lab.evidence import sha256
from sapi_config_lab.execute.agency import WRAPPER_MODEL, WRAPPER_UPSTREAM, strict_json
from sapi_config_lab.execute.host import LAB_IMAGE
from sapi_config_lab.paths import workspace_root
from sapi_config_lab.profile import read

ROOT = workspace_root()
CASE_SECONDS = 600


def validate_control(path: Path, current: dict[str, str], identity: str) -> dict:
    gate = read_json(path)
    require(
        gate.get("schema") == "sapi-lab-harbor/v1" and gate.get("status") == "passed" and gate.get("mode") == "stub",
        "Live calls require a passing control report",
    )
    require(
        gate.get("source_unchanged") is True and read_json(path.parent / gate["source_manifest"]) == current,
        "Control report does not match current sources",
    )
    require(gate.get("image_id") == identity and identity.startswith("sha256:"), "Control report image mismatch")
    require(
        gate.get("transport", {}).get("passed") is True
        and gate.get("oracle", {}).get("passed") is True
        and gate.get("nop", {}).get("passed") is True,
        "Missing fresh transport/oracle/nop controls",
    )
    require(
        gate.get("checks") and all(check.get("passed") is True for check in gate["checks"]),
        "Control checks did not all pass",
    )
    return gate


def wrapper_identity(path: Path, endpoint: str) -> dict:
    evidence = read_json(path)
    require(
        evidence.get("schema") == "sapi-lab-wrapper-identity/v1" and evidence.get("endpoint") == endpoint,
        "Wrapper identity endpoint mismatch",
    )
    require(
        evidence.get("dispatch") == "codex-exec"
        and evidence.get("response_substitution") is False
        and evidence.get("wrapper_retries") == 0
        and evidence.get("model") == WRAPPER_MODEL,
        "Wrapper dispatch inspection is missing or incompatible",
    )
    require(
        evidence.get("provider_internal_retries") in ("unknown", "none", "observed"),
        "Missing lower-layer retry limitation",
    )
    files = evidence.get("files")
    require(isinstance(files, list) and len(files) >= 1, "Missing private wrapper source identity")
    require(len({row["path"] for row in files}) == len(files), "Duplicate wrapper source identity")
    for row in files:
        require(sha256(Path(row["path"])) == row["sha256"], "Wrapper/config identity changed")
    return evidence


def validate_packages(path: Path, submissions: dict, image: str):
    for scenario, selected in submissions.items():
        task = path / scenario
        require(sha256(task / "environment/base.yaml") == selected["sha256"], "Staged submission hash mismatch")
        if "cases_sha256" in selected:
            require(sha256(task / "tests/cases.json") == selected["cases_sha256"], "Staged fixture hash mismatch")
        settings = tomllib.loads((task / "task.toml").read_text())
        require(
            settings["agent"]["timeout_sec"] == 600 and settings["verifier"]["timeout_sec"] == 1800,
            "Staged Harbor timeouts changed",
        )
        require(
            (task / "environment/Dockerfile").read_text().startswith(f"FROM {image}\n"),
            "Task image differs from frozen image",
        )
        require(
            (task / "solution/solve.sh").read_bytes() == (ROOT / "harbor/templates/solve.sh").read_bytes(),
            "Copying agent was changed",
        )


def check_trials(trials: list[dict], submissions: dict, *, mode: str, expected_cases: dict | None = None) -> None:
    names = [trial["task_name"] for trial in trials]
    require(len(names) == len(set(names)) and set(names) <= set(submissions), "Unexpected or duplicate Harbor trial")
    for trial in trials:
        require(trial_accepted(trial), "Harbor or independent acceptance failed")
        acceptance = trial["acceptance"]
        scenario = trial["task_name"]
        verifier = Path(trial["result_path"]).parent / "verifier"
        require(
            acceptance.get("mode") == mode and acceptance.get("scenario") == scenario, "Verifier scenario/mode mismatch"
        )
        require(
            acceptance.get("submission_sha256")
            == submissions[scenario]["sha256"]
            == sha256(verifier / "evidence/submission.yaml"),
            "Container submission hash mismatch",
        )
        require(
            acceptance.get("cases") and all(row.get("passed") is True for row in acceptance["cases"]),
            "Missing independent case acceptance",
        )
        if expected_cases is not None:
            require(
                [row["name"] for row in acceptance["cases"]] == sorted(expected_cases[scenario]),
                "Required live-case subset changed",
            )


def audit_records(path: Path) -> list[dict]:
    records = [strict_json(line) for line in path.read_text().splitlines()] if path.exists() else []
    require(all(isinstance(row, dict) for row in records), "Audit records must be objects")
    return records


def failure_category(trials: list[dict], audit: list[dict], default: str) -> str:
    failed = next((row for row in audit if row.get("event") == "failure"), None)
    if failed:
        return failed["category"]
    for trial in trials:
        if trial.get("exception") or not trial.get("acceptance"):
            return "harbor_environment"
        for row in trial["acceptance"].get("cases", []):
            if row.get("passed"):
                continue
            status = row.get("execution", {}).get("status")
            return {
                "compile_error": "compilation",
                "import_error": "n8n_import",
                "error": "n8n_execution",
                "success": "business_acceptance",
            }.get(status, "yaml_profile")
    return default


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stub-report", type=Path, required=True, help="A passing control report for this image")
    parser.add_argument("--submissions-manifest", type=Path, help="From `sapi-lab select`; default: reference configs")
    parser.add_argument("--scenario", action="append", help="Reference scenarios when no manifest is given")
    parser.add_argument("--max-calls", type=int, required=True, help="Aggregate model-call ceiling for this run")
    parser.add_argument("--report-dir", type=Path)
    parser.add_argument("--preflight-only", action="store_true")
    parser.add_argument("--wrapper-evidence", type=Path)
    parser.add_argument("--upstream", default=WRAPPER_UPSTREAM)
    parser.add_argument("--bridge-port", type=int, default=18765)
    parser.add_argument("--series-dir", type=Path, help="Reserve in a ledger shared with other runs")
    parser.add_argument("--series-ceiling", action="append", help="PHASE=N, fixed when a series ledger is created")
    parser.add_argument("--stop-after-failure", action="store_true", help="A failed case blocks later reservations")
    args = parser.parse_args(argv)
    if args.submissions_manifest and args.scenario:
        parser.error("A manifest selects its own scenarios")
    if not args.preflight_only and args.wrapper_evidence is None:
        parser.error("Live dispatch requires --wrapper-evidence")
    try:
        series_ceilings = parse_ceilings(args.series_ceiling)
        reference = select_scenarios(args.scenario) if not args.submissions_manifest else {}
    except ValueError as error:
        parser.error(str(error))
    output = args.report_dir or ROOT / "reports" / (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-live")
    report: dict[str, Any] = {
        "schema": "sapi-lab-live/v3",
        "mode": "preflight" if args.preflight_only else "live",
        "stub_report": str(args.stub_report.resolve()),
        "generation_calls": 0,
        "budget": {"max_calls": args.max_calls, "cases": {}},
        "trials": [],
        "not_run": [],
        "correlation": [],
        "audit": [],
        "limitations": [
            "Call and time caps are enforced; currency cost and provider-internal retries remain unknown.",
            "The wrapper is trusted; its tool restrictions are cooperative, not a sandbox.",
            "Timeout cancellation is not propagated; an upstream model outcome can remain unknown.",
        ],
    }

    def body(run: Run) -> None:
        report["stub_report_sha256"] = sha256(args.stub_report)
        identity = run.use_image(LAB_IMAGE)
        gate = validate_control(args.stub_report.resolve(), run.sources, identity)
        shutil.copyfile(args.stub_report, run.output / "control-report.json")
        if args.submissions_manifest:
            submissions = load_selection(args.submissions_manifest, copy_to=run.output / "selection")
            report["selection_sha256"] = sha256(args.submissions_manifest)
            run.pin("selection", run.output / "selection")
            run.pin("selection manifest", args.submissions_manifest)
        else:
            submissions = {
                name: {"path": s.config, "sha256": sha256(s.config), "cases": s.cases()}
                for name, s in reference.items()
            }
        scenarios = tuple(submissions)
        controlled = {trial["task_name"] for trial in gate["oracle"]["trials"]}
        require(set(scenarios) <= controlled, "The control report does not cover every scenario")
        grants = {}
        for scenario, submission in submissions.items():
            for name in live_cohort(scenario, submission):
                config = read(Path(submission["path"]))
                config["workflow"]["inputs"] = next(
                    case["inputs"] for case in submission["cases"]["positive"] if case["name"] == name
                )
                grants[scenario, name] = (case_budget(scenario, name, config, submission["cases"]), config)
        report["budget"]["cases"] = {f"{s}/{c}": grant["max_attempts"] for (s, c), (grant, _) in grants.items()}
        needed = sum(report["budget"]["cases"].values())
        require(
            needed <= args.max_calls,
            f"The cohort needs up to {needed} model calls; --max-calls allows {args.max_calls}",
        )
        report["human_review"] = {s: SCENARIOS[s].human_review for s in scenarios}
        ledger = open_ledger(
            run.output, args.series_dir, {"runtime": args.max_calls}, args.stop_after_failure, series_ceilings
        )
        report["ledger"] = str(ledger.path)
        if args.wrapper_evidence:
            report["wrapper_identity"] = wrapper_identity(args.wrapper_evidence, args.upstream)
            shutil.copyfile(args.wrapper_evidence, run.output / "wrapper-identity.json")
            run.pin("wrapper identity", args.wrapper_evidence)
        run.stage(
            "replay" if args.submissions_manifest else "oracle",
            scenarios,
            submissions={s: {"path": v["path"], "sha256": v["sha256"]} for s, v in submissions.items()}
            if args.submissions_manifest
            else None,
            cases={s: v["cases"] for s, v in submissions.items()},
        )
        validate_packages(run.tasks, submissions, run.image or "")
        run.check("before-stub")
        exit_code, stub_trials = run.harbor(
            "stub-replay", run.tasks, "oracle", timeout=2400, verifier_env=["SAPI_LLM_MODE=stub"]
        )
        report["preflight"] = {"harbor_exit_code": exit_code, "trials": stub_trials}
        require(
            exit_code == 0 and sorted(t["task_name"] for t in stub_trials) == sorted(scenarios),
            "Unpaid stub replay failed",
        )
        check_trials(stub_trials, submissions, mode="stub")
        if args.preflight_only:
            report["status"] = "passed"
            return
        report["not_run"] = list(report["budget"]["cases"])
        report_path = run.output / "report.json"
        for (scenario, name), (grant, config) in grants.items():
            run.check(f"before-{scenario}-{name}")
            budget = {**grant, "expires_at": time.time() + CASE_SECONDS}
            upper_bound = "refinement" in config["execution"]
            label = f"{scenario}-{name}"
            with ledger.reserved(
                "runtime", f"{run.output.name}/{scenario}/{name}", grant["max_attempts"], report_path, args.max_calls
            ) as outcome:
                with run.bridge(args.bridge_port, args.upstream, budget, label) as audit:
                    exit_code, trials = run.harbor(
                        "live-" + label,
                        run.tasks / scenario,
                        "oracle",
                        timeout=budget["expires_at"] - time.time(),
                        verifier_env=[
                            "SAPI_LLM_MODE=live",
                            "SAPI_CASE_NAME=" + name,
                            f"SAPI_BRIDGE_URL=http://host.docker.internal:{args.bridge_port}",
                        ],
                    )
                report["trials"].extend(trials)
                records = audit_records(audit)
                report["audit"].extend(records)
                require(exit_code == 0 and len(trials) == 1, "Live Harbor case failed")
                check_trials(trials, submissions, mode="live", expected_cases={scenario: {name}})
                native = collect_native(trials, submissions, {scenario: {name}})
                correlation = reconcile_dispatches(native, records, grant["model"])
                calls, cap = len(correlation), grant["max_attempts"]
                require(
                    calls <= cap and (calls >= min(1, cap) if upper_bound else calls == cap), "Unexpected call count"
                )
                run.check(f"after-{scenario}-{name}")
                report["correlation"].extend(correlation)
                report["not_run"].remove(f"{scenario}/{name}")
                outcome.passed = True
        report["counts"] = {
            event: sum(row.get("event") == event for row in report["audit"])
            for event in ("dispatch_attempt", "completion", "failure")
        }
        report["status"] = "passed"

    def classify(error: Exception) -> str:
        return failure_category(
            report["trials"] + report.get("preflight", {}).get("trials", []), report["audit"], "live_gate"
        )

    try:
        run_experiment(output, report, body, prefix="sapi-lab-live", classify=classify)
    except ValueError as error:
        parser.error(str(error))
    print(json.dumps({"status": report["status"], "report": str(Path(output).resolve() / "report.json")}), flush=True)
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
