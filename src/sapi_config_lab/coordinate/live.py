#!/usr/bin/env python3
"""Live replay of saved submissions: one bounded Agency grant per case, after unpaid controls."""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import replace
from datetime import UTC, datetime
import json
from pathlib import Path
import shutil
import time
from typing import Any

from sapi_config_lab.benchmark_loading import freeze_identity, load_entrypoints
from sapi_config_lab.coordinate.benchmark_discovery import select_benchmarks
from sapi_config_lab.coordinate.evaluation import ADMISSION_REPORT, trial_accepted, validate_result
from sapi_config_lab.coordinate.ledger import open_ledger, parse_ceilings
from sapi_config_lab.coordinate.live_evidence import collect_native, reconcile_dispatches
from sapi_config_lab.coordinate.packages import runtime_grant_seconds, selected_cases
from sapi_config_lab.coordinate.replay import load_selection, read_json, require
from sapi_config_lab.coordinate.runs import Run, progress, run_experiment
from sapi_config_lab.coordinate.wrapper import parse_wrapper_files, wrapper_identity
from sapi_config_lab.evidence import sha256, write_json
from sapi_config_lab.execute.agency import strict_json
from sapi_config_lab.execute.host import LAB_IMAGE, HostConfig
from sapi_config_lab.paths import workspace_root

ROOT = workspace_root()


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


def validate_packages(path: Path, submissions: dict, benchmarks: dict):
    from sapi_config_lab.coordinate.benchmark_packages import validate_selected

    for name, selected in submissions.items():
        benchmark = benchmarks[name]
        cases = selected.get("cases", selected_cases(benchmark))
        validate_selected(benchmark, path / name, ROOT, {**selected, "cases": cases})


def model_grant(benchmark, config: dict) -> dict:
    """A grant for exactly the model calls a hosted submission's LLM steps may make."""
    workflow = config["workflow"]
    calls = {
        f"{workflow['id']}/r{workflow['revision']}/{step['id']}": step["uses"]
        for step in workflow["steps"]
        if step["kind"] == "LLM"
    }
    cap = benchmark.budgets.runtime_model_calls
    require(cap is None or len(calls) <= cap, "Runtime model cap exceeded")
    return {"max_attempts": len(calls), "operations": dict(Counter(calls.values())), "occurrences": calls}


def check_trials(
    trials: list[dict],
    submissions: dict,
    *,
    benchmarks: dict,
    mode: str,
    expected_cases: dict | None = None,
    admission: bool = False,
) -> None:
    names = [trial["task_name"] for trial in trials]
    require(len(names) == len(set(names)) and set(names) <= set(submissions), "Unexpected or duplicate Harbor trial")
    for trial in trials:
        acceptance = trial["acceptance"] or {}
        scenario = trial["task_name"]
        identity = trial_identity(trial)
        require(
            identity.get("mode") == mode and identity.get("scenario") == scenario, "Verifier scenario/mode mismatch"
        )
        world = "prepare" in benchmarks[scenario].entrypoints
        if admission and world:
            require(
                mode == "stub"
                and acceptance.get("schema") == ADMISSION_REPORT
                and acceptance.get("passed") is True
                and not trial["exception"]
                and trial["rewards"] == {"reward": 1.0}
                and identity.get("submission_sha256") == submissions[scenario]["sha256"],
                "Hosted admission failed or the worker saw another submission",
            )
            continue
        require(acceptance.get("schema") != ADMISSION_REPORT, "Admission is not an independent evaluation")
        if "verdict_path" in trial:
            validate_result(trial["result"])
        if world:
            # World verdicts are measured, scored when a reference reward is declared.
            require(
                not trial["exception"] and identity.get("submission_sha256") == submissions[scenario]["sha256"],
                "Harbor failed or the host saw another submission",
            )
            if benchmarks[scenario].controls.reference_reward is None:
                require(trial["result"]["acceptance"] is not None, "Hosted evaluation is missing")
            else:
                require((trial["result"]["quality"] or {}).get("status") == "complete", "Hosted evaluation is unscored")
            continue
        require(trial_accepted(trial), "Harbor or independent acceptance failed")
        verifier = Path(trial["result_path"]).parent / "verifier"
        require(
            identity.get("submission_sha256")
            == submissions[scenario]["sha256"]
            == sha256(verifier / "evidence/submission.yaml"),
            "Container submission hash mismatch",
        )
        if "verdict_path" in trial:
            observation = read_json(verifier / "evidence/observation.json")
            cases = [row["name"] for row in observation.get("entries", [])]
            require(bool(cases), "Missing independent case observations")
        else:
            require(
                acceptance.get("cases") and all(row.get("passed") is True for row in acceptance["cases"]),
                "Missing independent case acceptance",
            )
            cases = [row["name"] for row in acceptance["cases"]]
        if expected_cases is not None:
            require(cases == sorted(expected_cases[scenario]), "Required live-case subset changed")


def trial_identity(trial: dict) -> dict:
    """Read native submission and mode identity independently of the evaluator's report format."""
    if "benchmark" in trial:
        metadata = trial["benchmark"]
        return {
            "scenario": metadata.get("name"),
            "mode": metadata.get("runtime_options", {}).get("mode"),
            "submission_sha256": metadata.get("submission_sha256"),
        }
    return trial["acceptance"] or {}


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


def cohort_grants(submissions: dict, benchmarks: dict) -> dict[tuple[str, str], tuple[dict, dict]]:
    """Reserve the occurrences of each selected benchmark's live observation plan."""
    grants = {}
    for scenario, submission in submissions.items():
        benchmark = benchmarks[scenario]
        if benchmark is None:
            raise ValueError("Live experiments require a selected versioned benchmark")
        options = {"mode": "live", "deadline_seconds": 600}
        if "cases" in submission:
            options["cases"] = submission["cases"]
        identity = freeze_identity(benchmark, options)
        plan = load_entrypoints(benchmark, identity).plan(Path(submission["path"]), options)
        for entry in plan["entries"]:
            config = entry["config"]
            grant = model_grant(benchmark, config)
            minimum = entry.get(
                "minimum_model_calls",
                min(1, grant["max_attempts"]) if "refinement" in config["execution"] else grant["max_attempts"],
            )
            require(type(minimum) is int and 0 <= minimum <= grant["max_attempts"], "Invalid planned model minimum")
            grant["minimum_attempts"] = minimum
            grants[scenario, entry["name"]] = (grant, config)
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
        reference = (
            {item.name: item for item in select_benchmarks(ROOT / "tasks", args.scenario)}
            if not args.submissions_manifest
            else {}
        )
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
                name: {"path": s.reference.source, "sha256": sha256(s.reference.source)}
                | ({"cases": selected_cases(s)} if selected_cases(s) is not None else {})
                for name, s in reference.items()
            }
        scenarios = tuple(submissions)
        selected = select_benchmarks(ROOT / "tasks", scenarios)
        benchmarks = {item.name: item for item in selected}
        controlled = {trial["task_name"] for trial in gate["oracle"]["trials"]}
        require(set(scenarios) <= controlled, "The control report does not cover every scenario")
        judge_calls = {}
        for name in scenarios:
            benchmark = benchmarks[name]
            if benchmark is None:
                raise ValueError("Live experiments require a selected versioned benchmark")
            judge_calls[name] = benchmark.budgets.judge_calls
        judge_total = sum(judge_calls.values())
        require(not judge_total or args.preflight_only or args.judge_model, "Hosted evaluation needs --judge-model")
        grants = cohort_grants(submissions, benchmarks)
        report["budget"]["cases"] = {f"{s}/{c}": grant["max_attempts"] for (s, c), (grant, _) in grants.items()}
        report["budget"]["judge"] = judge_calls
        needed = sum(report["budget"]["cases"].values()) + judge_total
        require(
            needed <= args.max_calls,
            f"The cohort needs up to {needed} model calls; --max-calls allows {args.max_calls}",
        )
        report["human_review"] = {s: benchmarks[s].config.get("human_review", False) for s in scenarios}
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
            selected,
            submissions={s: {"path": v["path"], "sha256": v["sha256"]} for s, v in submissions.items()}
            if args.submissions_manifest
            else None,
            cases={s: v["cases"] for s, v in submissions.items() if "cases" in v},
            judge_model=args.judge_model,
        )
        validate_packages(run.tasks, submissions, benchmarks)
        run.check("before-stub")
        progress(f"preflight: fixture stub replay and hosted compilation/admission of {', '.join(scenarios)}")
        started = time.monotonic()
        exit_code, stub_trials = run.harbor(
            "stub-replay", run.tasks, "oracle", verifier_env=["SAPI_LLM_MODE=stub"], admission=True
        )
        report["preflight"] = {
            "harbor_exit_code": exit_code,
            "trials": stub_trials,
            "hosted_scope": "Live-mode compilation and limits only; no candidate execution or acceptance",
        }
        require(
            exit_code == 0 and sorted(t["task_name"] for t in stub_trials) == sorted(scenarios),
            "Unpaid stub replay failed",
        )
        check_trials(stub_trials, submissions, benchmarks=benchmarks, mode="stub", admission=True)
        progress(f"preflight: passed ({round(time.monotonic() - started)}s)")
        if args.preflight_only:
            report["status"] = "passed"
            return
        report["not_run"] = list(report["budget"]["cases"])
        report["judge_model"] = args.judge_model
        report_path = run.output / "report.json"
        bridge_url = host.container_url(host.bridge_port)
        for number, ((scenario, name), (grant, _config)) in enumerate(grants.items(), 1):
            step = f"[{number}/{len(grants)}] {scenario}/{name}"
            progress(f"{step}: live, up to {grant['max_attempts']} model calls reserved")
            started = time.monotonic()
            run.check(f"before-{scenario}-{name}")
            wrapper_identity(args.wrapper_evidence, host.wrapper_url, host.wrapper_model, wrapper_files)
            label = f"{scenario}-{name}"
            with ledger.reserved(
                "runtime", f"{run.output.name}/{scenario}/{name}", grant["max_attempts"], report_path, args.max_calls
            ) as runtime_outcome:
                budget = {
                    **{key: value for key, value in grant.items() if key != "minimum_attempts"},
                    "model": host.wrapper_model,
                    "expires_at": time.time() + runtime_grant_seconds(benchmarks[scenario]),
                }
                with run.bridge(
                    budget,
                    label,
                    bindings=next(
                        item.source
                        for item in benchmarks[scenario].files
                        if item.destination == benchmarks[scenario].bindings
                    ),
                    reject_tool_use=True,
                ) as audit:
                    exit_code, trials = run.harbor(
                        "live-" + label,
                        run.tasks / scenario,
                        "oracle",
                        verifier_env=["SAPI_LLM_MODE=live", "SAPI_CASE_NAME=" + name, "SAPI_BRIDGE_URL=" + bridge_url],
                    )
                report["trials"].extend(trials)
                records = audit_records(audit)
                report["audit"].extend(records)
                require(exit_code == 0 and len(trials) == 1, "Live Harbor case failed")
                trial = trials[0]
                require(trial["task_name"] == scenario, "Live Harbor selected another benchmark")
                record_identity = trial_identity(trial)
                require(
                    record_identity.get("mode") == "live" and record_identity.get("scenario") == scenario,
                    "Live record identity differs",
                )
                require(
                    record_identity.get("submission_sha256") == submissions[scenario]["sha256"],
                    "Live submission identity differs",
                )
                exception = trial["exception"] or {}
                require(
                    not exception
                    or (judge_calls.get(scenario) and exception.get("exception_type") == "RewardFileNotFoundError"),
                    "Live Harbor case failed",
                )
                native = collect_native(trials, submissions, {scenario: {name}}, bridge_url, benchmarks)
                correlation = reconcile_dispatches(
                    native,
                    records,
                    budget["model"],
                    bindings=next(
                        item.source
                        for item in benchmarks[scenario].files
                        if item.destination == benchmarks[scenario].bindings
                    ),
                )
                calls, cap = len(correlation), grant["max_attempts"]
                require(grant["minimum_attempts"] <= calls <= cap, "Unexpected call count")
                report["correlation"].extend(correlation)
                run.check(f"before-judge-{scenario}-{name}")
                if not judge_calls.get(scenario):
                    check_trials(
                        trials, submissions, benchmarks=benchmarks, mode="live", expected_cases={scenario: {name}}
                    )
                    run.check(f"after-{scenario}-{name}")
                runtime_outcome.passed = True
            if judge_calls.get(scenario):
                from sapi_config_lab.coordinate.evaluation import reevaluate_benchmark

                with ledger.reserved(
                    "judge",
                    f"{run.output.name}/{scenario}/{name}/judge",
                    judge_calls[scenario],
                    report_path,
                    args.max_calls,
                ) as judge_outcome:
                    record = Path(trial["result_path"]).parent / "verifier"
                    result = reevaluate_benchmark(
                        record, record / "paid-evaluation", None, dispatch=True, reserved=lambda call: call()
                    )
                    trial["native_exception"] = trial["exception"]
                    if exception.get("exception_type") == "RewardFileNotFoundError":
                        trial["exception"] = None
                    trial["result"] = result
                    trial["verdict_path"] = str(record / "paid-evaluation/result.json")
                    write_json(Path(trial["verdict_path"]), result)
                    report_file = record / "paid-evaluation/evaluation/report.json"
                    trial["acceptance"] = json.loads(report_file.read_text()) if report_file.exists() else None
                    trial["evaluation_path"] = str(record / "paid-evaluation/evaluation/report.json")
                    check_trials(
                        trials, submissions, benchmarks=benchmarks, mode="live", expected_cases={scenario: {name}}
                    )
                    run.check(f"after-{scenario}-{name}")
                    judge_outcome.passed = True
            report["not_run"].remove(f"{scenario}/{name}")
            progress(f"{step}: passed ({round(time.monotonic() - started)}s)")
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
