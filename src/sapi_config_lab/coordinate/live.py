#!/usr/bin/env python3
"""Live replay of saved submissions: one bounded Agency grant per case, after unpaid controls."""

from __future__ import annotations

import argparse
from collections import Counter
from contextlib import ExitStack
from dataclasses import replace
from datetime import UTC, datetime
import json
from pathlib import Path
import shutil
import time
from typing import Any

from sapi_config_lab.coordinate.evaluation import hosted_evaluation, trial_accepted
from sapi_config_lab.coordinate.ledger import open_ledger, parse_ceilings
from sapi_config_lab.coordinate.live_evidence import case_budget, collect_native, live_cohort, reconcile_dispatches
from sapi_config_lab.coordinate.packages import task_toml
from sapi_config_lab.coordinate.providers import EVALUATORS
from sapi_config_lab.coordinate.replay import load_selection, read_json, require
from sapi_config_lab.coordinate.runs import Hosting, Run, progress, run_experiment
from sapi_config_lab.coordinate.scenarios import SCENARIOS, select_scenarios
from sapi_config_lab.coordinate.wrapper import parse_wrapper_files, wrapper_identity
from sapi_config_lab.evidence import sha256
from sapi_config_lab.execute.agency import strict_json
from sapi_config_lab.execute.host import LAB_IMAGE, HostConfig
from sapi_config_lab.paths import workspace_root
from sapi_config_lab.profile import read

ROOT = workspace_root()
LIVE_CASE_TIMEOUT_SECONDS = 600


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


def validate_packages(path: Path, submissions: dict, image: str):
    templates = ROOT / "harbor/templates"
    for scenario, selected in submissions.items():
        task = path / scenario
        require(sha256(task / "environment/base.yaml") == selected["sha256"], "Staged submission hash mismatch")
        if "cases_sha256" in selected:
            require(sha256(task / "tests/cases.json") == selected["cases_sha256"], "Staged fixture hash mismatch")
        require(
            (task / "task.toml").read_text() == task_toml(ROOT, SCENARIOS[scenario]), "Staged Harbor settings changed"
        )
        require(
            (task / "environment/Dockerfile").read_text().startswith(f"FROM {image}\n"),
            "Task image differs from frozen image",
        )
        require(
            (task / "solution/solve.sh").read_bytes() == (templates / "solve.sh").read_bytes(),
            "Copying agent was changed",
        )


def hosted_grant(scenario: str, config: dict) -> dict:
    """A grant for exactly the model calls a hosted submission's LLM steps may make."""
    workflow = config["workflow"]
    calls = {
        f"{workflow['id']}/r{workflow['revision']}/{step['id']}": step["uses"]
        for step in workflow["steps"]
        if step["kind"] == "LLM"
    }
    cap = SCENARIOS[scenario].runtime_model_calls
    require(cap is not None and len(calls) <= cap, "Runtime model cap exceeded")
    return {"max_attempts": len(calls), "operations": dict(Counter(calls.values())), "occurrences": calls}


def check_trials(trials: list[dict], submissions: dict, *, mode: str, expected_cases: dict | None = None) -> None:
    names = [trial["task_name"] for trial in trials]
    require(len(names) == len(set(names)) and set(names) <= set(submissions), "Unexpected or duplicate Harbor trial")
    for trial in trials:
        acceptance = trial["acceptance"] or {}
        scenario = trial["task_name"]
        require(
            acceptance.get("mode") == mode and acceptance.get("scenario") == scenario, "Verifier scenario/mode mismatch"
        )
        if SCENARIOS[scenario].hosted:
            # Hosted verdicts are measured, not gated: an evaluated trial, scored when a reference reward is declared.
            require(
                not trial["exception"] and acceptance.get("submission_sha256") == submissions[scenario]["sha256"],
                "Harbor failed or the host saw another submission",
            )
            if SCENARIOS[scenario].reference_reward is None:
                require(trial["result"]["acceptance"] is not None, "Hosted evaluation is missing")
            else:
                require((trial["result"]["quality"] or {}).get("status") == "complete", "Hosted evaluation is unscored")
            continue
        require(trial_accepted(trial), "Harbor or independent acceptance failed")
        verifier = Path(trial["result_path"]).parent / "verifier"
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


def cohort_grants(submissions: dict, hosted: tuple[str, ...]) -> dict[tuple[str, str], tuple[dict, dict]]:
    """(scenario, case) -> (grant, config): one grant per verifier live case, one per hosted run."""
    grants = {}
    for scenario, submission in submissions.items():
        if scenario in hosted:
            config = read(Path(submission["path"]))
            grants[scenario, "run"] = (hosted_grant(scenario, config), config)
            continue
        for name in live_cohort(scenario, submission):
            config = read(Path(submission["path"]))
            config["workflow"]["inputs"] = next(
                case["inputs"] for case in submission["cases"]["positive"] if case["name"] == name
            )
            grants[scenario, name] = (case_budget(scenario, name, config, submission["cases"]), config)
    return grants


def main(argv: list[str] | None = None) -> int:
    host = HostConfig.from_environment()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stub-report", type=Path, required=True, help="A passing control report for this image")
    parser.add_argument("--submissions-manifest", type=Path, help="From `sapi-lab select`; default: reference configs")
    parser.add_argument("--scenario", action="append", help="Reference scenarios when no manifest is given")
    parser.add_argument("--max-calls", type=int, required=True, help="Aggregate model-call ceiling for this run")
    parser.add_argument("--report-dir", type=Path)
    parser.add_argument("--preflight-only", action="store_true")
    parser.add_argument("--wrapper-evidence", type=Path, help="The inspected wrapper identity record")
    parser.add_argument(
        "--wrapper-file", action="append", help="NAME=PATH: where an inspected wrapper file is on this machine"
    )
    parser.add_argument("--upstream", default=host.wrapper_url, help="Model wrapper URL (SAPI_WRAPPER_URL)")
    parser.add_argument("--bridge-port", type=int, default=host.bridge_port, help="(SAPI_BRIDGE_PORT)")
    parser.add_argument("--series-dir", type=Path, help="Reserve in a ledger shared with other runs")
    parser.add_argument("--series-ceiling", action="append", help="PHASE=N, fixed when a series ledger is created")
    parser.add_argument("--stop-after-failure", action="store_true", help="A failed case blocks later reservations")
    parser.add_argument("--judge-model", help="Judge model for hosted evaluators that call one")
    args = parser.parse_args(argv)
    if args.submissions_manifest and args.scenario:
        parser.error("A manifest selects its own scenarios")
    if not args.preflight_only and args.wrapper_evidence is None:
        parser.error("Live dispatch requires --wrapper-evidence")
    try:
        series_ceilings = parse_ceilings(args.series_ceiling)
        wrapper_files = parse_wrapper_files(args.wrapper_file)
        reference = select_scenarios(args.scenario) if not args.submissions_manifest else {}
    except ValueError as error:
        parser.error(str(error))
    host = replace(host, wrapper_url=args.upstream, bridge_port=args.bridge_port)
    output = args.report_dir or ROOT / "reports" / (datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-live")
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
        progress(f"controls: checking {args.stub_report}")
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
                name: {"path": s.config, "sha256": sha256(s.config)}
                | ({"cases": s.cases()} if s.environment == "fixtures" else {})
                for name, s in reference.items()
            }
        scenarios = tuple(submissions)
        controlled = {trial["task_name"] for trial in gate["oracle"]["trials"]}
        require(set(scenarios) <= controlled, "The control report does not cover every scenario")
        hosted = tuple(s for s in scenarios if SCENARIOS[s].hosted)
        judge_calls = {s: EVALUATORS[SCENARIOS[s].evaluator].judge_calls for s in hosted}
        judge_total = sum(judge_calls.values())
        require(not judge_total or args.preflight_only or args.judge_model, "Hosted evaluation needs --judge-model")
        grants = cohort_grants(submissions, hosted)
        report["budget"]["cases"] = {f"{s}/{c}": grant["max_attempts"] for (s, c), (grant, _) in grants.items()}
        report["budget"]["judge"] = judge_calls
        needed = sum(report["budget"]["cases"].values()) + judge_total
        require(
            needed <= args.max_calls,
            f"The cohort needs up to {needed} model calls; --max-calls allows {args.max_calls}",
        )
        report["human_review"] = {s: SCENARIOS[s].human_review for s in scenarios}
        ceilings = {"runtime": args.max_calls} | ({"judge": args.max_calls} if judge_total else {})
        ledger = open_ledger(run.output, args.series_dir, ceilings, args.stop_after_failure, series_ceilings)
        report["ledger"] = str(ledger.path)
        if args.wrapper_evidence:
            report["wrapper_identity"] = wrapper_identity(
                args.wrapper_evidence, host.wrapper_url, host.wrapper_model, wrapper_files
            )
            report["wrapper_files_relocated"] = sorted(wrapper_files)
            shutil.copyfile(args.wrapper_evidence, run.output / "wrapper-identity.json")
            run.pin("wrapper identity", args.wrapper_evidence)
        progress(f"staging: {len(scenarios)} task packages")
        run.stage(
            "replay" if args.submissions_manifest else "oracle",
            scenarios,
            submissions={s: {"path": v["path"], "sha256": v["sha256"]} for s, v in submissions.items()}
            if args.submissions_manifest
            else None,
            cases={s: v["cases"] for s, v in submissions.items() if "cases" in v},
        )
        validate_packages(run.tasks, submissions, run.image or "")
        run.check("before-stub")
        progress(f"preflight: unpaid stub replay of {', '.join(scenarios)}")
        started = time.monotonic()
        simulated = Hosting("stub", hosted_evaluation(hosted))
        exit_code, stub_trials = run.harbor(
            "stub-replay", run.tasks, "oracle", verifier_env=["SAPI_LLM_MODE=stub"], hosting=simulated
        )
        report["preflight"] = {"harbor_exit_code": exit_code, "trials": stub_trials}
        require(
            exit_code == 0 and sorted(t["task_name"] for t in stub_trials) == sorted(scenarios),
            "Unpaid stub replay failed",
        )
        check_trials(stub_trials, submissions, mode="stub")
        progress(f"preflight: passed ({round(time.monotonic() - started)}s)")
        if args.preflight_only:
            report["status"] = "passed"
            return
        report["not_run"] = list(report["budget"]["cases"])
        report["judge_model"] = args.judge_model
        report_path = run.output / "report.json"
        bridge_url = host.container_url(host.bridge_port)
        for number, ((scenario, name), (grant, config)) in enumerate(grants.items(), 1):
            step = f"[{number}/{len(grants)}] {scenario}/{name}"
            progress(f"{step}: live, up to {grant['max_attempts']} model calls reserved")
            started = time.monotonic()
            run.check(f"before-{scenario}-{name}")
            budget = {**grant, "model": host.wrapper_model, "expires_at": time.time() + LIVE_CASE_TIMEOUT_SECONDS}
            upper_bound = "refinement" in config["execution"]
            label = f"{scenario}-{name}"
            hosting = None
            with ExitStack() as reserved:
                outcomes = [
                    reserved.enter_context(
                        ledger.reserved(
                            "runtime",
                            f"{run.output.name}/{scenario}/{name}",
                            grant["max_attempts"],
                            report_path,
                            args.max_calls,
                        )
                    )
                ]
                if judge_calls.get(scenario):
                    outcomes.append(
                        reserved.enter_context(
                            ledger.reserved(
                                "judge",
                                f"{run.output.name}/{scenario}/judge",
                                judge_calls[scenario],
                                report_path,
                                args.max_calls,
                            )
                        )
                    )
                if scenario in hosted:
                    hosting = Hosting("live", hosted_evaluation((scenario,), args.judge_model), bridge_url)
                with run.bridge(
                    budget, label, bindings=SCENARIOS[scenario].bindings, reject_tool_use=scenario in hosted
                ) as audit:
                    exit_code, trials = run.harbor(
                        "live-" + label,
                        run.tasks / scenario,
                        "oracle",
                        verifier_env=["SAPI_LLM_MODE=live", "SAPI_CASE_NAME=" + name, "SAPI_BRIDGE_URL=" + bridge_url],
                        hosting=hosting,
                    )
                report["trials"].extend(trials)
                records = audit_records(audit)
                report["audit"].extend(records)
                require(exit_code == 0 and len(trials) == 1, "Live Harbor case failed")
                if scenario in hosted:
                    check_trials(trials, submissions, mode="live")
                    calls = sum(row.get("event") == "dispatch_attempt" for row in records)
                    require(calls <= grant["max_attempts"], "Unexpected call count")
                else:
                    check_trials(trials, submissions, mode="live", expected_cases={scenario: {name}})
                    native = collect_native(trials, submissions, {scenario: {name}}, bridge_url)
                    correlation = reconcile_dispatches(native, records, budget["model"])
                    calls, cap = len(correlation), grant["max_attempts"]
                    require(
                        calls <= cap and (calls >= min(1, cap) if upper_bound else calls == cap),
                        "Unexpected call count",
                    )
                    report["correlation"].extend(correlation)
                run.check(f"after-{scenario}-{name}")
                report["not_run"].remove(f"{scenario}/{name}")
                progress(f"{step}: passed ({round(time.monotonic() - started)}s)")
                for outcome in outcomes:
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
        run_experiment(output, report, body, prefix="sapi-lab-live", classify=classify, host=host)
    except ValueError as error:
        parser.error(str(error))
    print(json.dumps({"status": report["status"], "report": str(Path(output).resolve() / "report.json")}), flush=True)
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
