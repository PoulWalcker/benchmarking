#!/usr/bin/env python3
"""Fresh controls, then model-authored YAML trials in Harbor/n8n, one reserved attempt at a time."""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
import sys
import time
from typing import Any

import yaml

from sapi_config_lab.coordinate.evaluation import ADMISSION_REPORT, NOT_EVALUATED, trial_accepted
from sapi_config_lab.coordinate.ledger import open_ledger, parse_ceilings
from sapi_config_lab.coordinate.live import validate_control
from sapi_config_lab.coordinate.native_tasks import invoke, policy, select_tasks
from sapi_config_lab.coordinate.progress import detail, stage
from sapi_config_lab.coordinate.runs import Run, load_trials, progress, run_experiment, trial_seconds
from sapi_config_lab.evidence import sha256, write_json
from sapi_config_lab.execute.host import LAB_IMAGE, HostConfig, run_logged
from sapi_config_lab.harbor_integration.runner import AUTHOR_AGENT
from sapi_config_lab.paths import workspace_root

ROOT = workspace_root()
MAX_ATTEMPTS = 10


def summarize_trials(*jobs: Path) -> list[dict]:
    """load_trials for model-authored submissions, plus where a failed one stopped."""
    rows = []
    for trial in [row for job in jobs for row in load_trials(job)]:
        directory = Path(trial["result_path"]).parent
        agent_path = directory / "agent/generation.json"
        agent = json.loads(agent_path.read_text()) if agent_path.exists() else {}
        acceptance = trial["acceptance"]
        admitted = (
            (acceptance or {}).get("schema") == ADMISSION_REPORT
            and acceptance.get("passed") is True
            and acceptance.get("scenario") == trial["task_name"]
            and acceptance.get("mode") == "stub"
            and acceptance.get("submission_sha256") == agent.get("submission_sha256")
            and trial["result"] == NOT_EVALUATED
            and not trial["exception"]
            and trial["rewards"] == {"reward": 1.0}
        )
        passed = agent.get("status") == "submitted" and (admitted or trial_accepted(trial))
        stage = None
        if not passed:
            if agent.get("status") != "submitted":
                stage = "generation_or_transport"
            elif not acceptance:
                stage = "test_infrastructure"
            elif not acceptance.get("cases"):
                stage = "yaml_parsing_or_definition"
            else:
                stage = "acceptance"
                for case in acceptance["cases"]:
                    if case.get("passed"):
                        continue
                    case_path = directory / "verifier/evidence/cases" / Path(case["artifacts"]).name / "case.json"
                    if case_path.exists():
                        status = json.loads(case_path.read_text()).get("status")
                        if status == "compile_error":
                            stage = "compilation"
                        elif status in {"error", "import_error"} and case.get("kind") == "positive":
                            stage = "import_or_execution"
                    break
        rows.append(
            {
                "scenario": trial["task_name"],
                "passed": passed,
                "admitted": admitted,
                "failure_stage": stage,
                "generation": agent,
                "rewards": trial["rewards"],
                "exception": trial["exception"],
                "acceptance": acceptance,
                "result": trial["result"],
                "result_path": trial["result_path"],
            }
        )
    return rows


def write_summary(report: dict, output: Path) -> None:
    lines = [
        "# YAML generation experiment",
        "",
        f"Status: {report['status']}. Completed: {report['experiment_completed']}.",
        f"Operation catalog: {report['catalog']['variant']}.",
        f"Passed: {report['passed_trials']}/{report['total_trials']}.",
        "",
        "| Task | Passed | Failure stage | Artifacts |",
        "|---|---|---|---|",
    ]
    for trial in report["trials"]:
        path = Path(trial["result_path"]).parent.relative_to(output)
        lines.append(
            f"| {trial['scenario']} | {trial['passed']} | {trial['failure_stage'] or '—'} "
            f"| [YAML]({path}/agent/submission.yaml) · [Verification]({path}/verifier/evaluation/report.json) |"
        )
    lines += ["", "## Limits", ""] + ["- " + item for item in report["limitations"]]
    (output / "SUMMARY.md").write_text("\n".join(lines) + "\n")


def main(argv: list[str] | None = None) -> int:
    host = HostConfig.from_environment()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--attempts", type=int, default=3, help="Independent calls per task, no feedback/repair")
    parser.add_argument("--report-dir", type=Path)
    parser.add_argument("--upstream", default=host.wrapper_url, help="Model wrapper URL (SAPI_WRAPPER_URL)")
    parser.add_argument("--scenario", action="append", help="Explicit selection; defaults to the default scenarios")
    parser.add_argument("--series-dir", type=Path, help="Reserve in a ledger shared with other runs")
    parser.add_argument("--series-ceiling", action="append", help="PHASE=N, fixed when a series ledger is created")
    parser.add_argument("--stop-after-failure", action="store_true", help="A failed attempt blocks later ones")
    parser.add_argument(
        "--catalog",
        choices=("full", "scenario"),
        default="full",
        help="Experiment arm: the full operation catalog (default) or only the operations the scenario's reference uses",
    )
    args = parser.parse_args(argv)
    try:
        tasks = select_tasks(ROOT / "tasks", args.scenario)
        scenarios = tuple(item.name for item in tasks)
        policies = {item.name: policy(item) for item in tasks}
        series_ceilings = parse_ceilings(args.series_ceiling)
    except ValueError as error:
        parser.error(str(error))
    if not 1 <= args.attempts <= MAX_ATTEMPTS:
        parser.error(f"--attempts must be between 1 and {MAX_ATTEMPTS}")
    budgets = {name: value.get("authoring_attempts") for name, value in policies.items()}
    capped = [s for s, budget in budgets.items() if budget is not None and args.attempts > budget]
    if capped:
        parser.error("--attempts exceeds the authoring budget of " + ", ".join(capped))
    for name in scenarios:
        if args.catalog not in policies[name].get("catalogs", ("full",)):
            parser.error("--catalog scenario applies to fixture scenarios only")
    output = args.report_dir or ROOT / "reports" / (datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-generation")
    ceiling = len(scenarios) * args.attempts
    report: dict[str, Any] = {
        "schema": "sapi-lab-generation/v2",
        "experiment_completed": False,
        "attempts_per_task": args.attempts,
        "scenarios": list(scenarios),
        "authoring_attempt_ceiling": ceiling,
        "runtime_llm_mode": "stub",
        "catalog": {"variant": args.catalog},
        "trials": [],
        "harbor_exit_codes": [],
        "limitations": [
            "Explicitly selected task families with a fixed specialized catalog; not held-out generalization.",
            "Single-shot YAML output; no tools, retries or repair requested.",
            "Wrapper tool restrictions are prompt-only, audited from CLI stderr, not a filesystem sandbox.",
            "Runtime model steps are deterministic stubs; only YAML generation calls the live model.",
            "The wrapper does not provide reliable token/cost breakdowns; costs remain unknown.",
        ],
    }

    def body(run: Run) -> None:
        started = time.monotonic()
        with stage("controls"), run.step("controls", log="control"):
            # The child's own progress lines go to control.log; its last line is shown as nested context.
            with detail("unpaid control suite child process; its stages are in control.log"):
                control = run_logged(
                    [
                        sys.executable,
                        "-m",
                        "sapi_config_lab.coordinate.controls",
                        "--report-dir",
                        str(run.output / "control"),
                    ]
                    + [arg for scenario in scenarios for arg in ("--scenario", scenario)],
                    run.output / "control.log",
                    stage="controls",
                    timeout=None,
                )
            try:
                child = json.loads((run.output / "control/report.json").read_text())
            except OSError, ValueError:
                if not control:
                    raise
                child = {}
            if control and not isinstance(child, dict):
                child = {}
            if control or child.get("status") != "passed":
                cause = (
                    f"control suite failed at {child['failure_stage']}"
                    if child.get("failure_stage")
                    else "Control suite failed"
                )
                raise RuntimeError(cause + "; model generation was not started")
        progress(f"controls: passed ({round(time.monotonic() - started)}s)")
        with stage("native images"):
            run.check("after-controls")
            identity = run.use_image(LAB_IMAGE)
            run.use_native_tasks(tasks)
            validate_control(run.output / "control/report.json", run.sources, identity, native_images=run.native_images)
        report["prompt_sha256"], report["private_cases_sha256"], report["private_cases_paths"] = {}, {}, {}
        prompts = {}
        with stage("prompts"):
            for task in tasks:
                with run.step("prompt " + task.name), detail("prompt " + task.name):
                    run.check("before-prompt-" + task.name)
                    material = invoke(task, {"action": "prompt", "catalog": args.catalog}, run.sources)
                    inputs = run.output / "inputs" / task.name
                    inputs.mkdir(parents=True)
                    prompt = inputs / "prompt.txt"
                    prompt.write_bytes(material["prompt"].encode())
                    prompts[task.name] = prompt
                    report["prompt_sha256"][task.name] = sha256(prompt)
                    if "cases" in material:
                        cases = inputs / "cases.json"
                        write_json(cases, {task.name: material["cases"]})
                        report["private_cases_sha256"][task.name] = sha256(cases)
                        report["private_cases_paths"][task.name] = str(cases.relative_to(run.output))
                    if args.catalog == "scenario":
                        text = material["catalog"]
                        report["catalog"].setdefault("operations", {})[task.name] = sorted(
                            yaml.safe_load(text)["operations"]
                        )
                        report["catalog"].setdefault("sha256", {})[task.name] = hashlib.sha256(
                            text.encode()
                        ).hexdigest()
            run.pin("authoring inputs", run.output / "inputs")
            run.check("before-authoring-ledger")
            ledger = open_ledger(
                run.output, args.series_dir, {"authoring": ceiling}, args.stop_after_failure, series_ceilings
            )
        report["ledger"] = str(ledger.path)
        report["fixture_overlay"] = []
        report_path = run.output / "report.json"
        jobs = []
        for attempt in range(1, args.attempts + 1):
            with stage(f"authoring {attempt}/{args.attempts}"):
                run.check(f"before-attempt-{attempt}")
                step = f"[{attempt}/{args.attempts}]"
                progress(f"{step} authoring, then verifying: {', '.join(scenarios)}")
                for task in tasks:
                    job = f"generated-{attempt}-{task.name}"
                    jobs.append(run.output / "jobs" / job)
                    run.check("before-reservation-" + job)
                    with (
                        ledger.reserved("authoring", f"{run.output.name}/{job}", 1, report_path, ceiling) as outcome,
                        detail("1 authoring call reserved"),
                    ):
                        exit_code, _ = run.harbor(
                            job,
                            task,
                            AUTHOR_AGENT,
                            agent_keys=[
                                "upstream=" + args.upstream,
                                "prompt_path=" + str(prompts[task.name]),
                                "prompt_sha256=" + report["prompt_sha256"][task.name],
                            ],
                            attempts="1",
                            admission=True,
                            verifier_env=["SAPI_NATIVE_DEADLINE_SECONDS=120", "SAPI_LLM_MODE=stub"],
                        )
                        rows = summarize_trials(jobs[-1])
                        for row in rows:
                            verdict = "passed" if row["passed"] else f"failed at {row['failure_stage']}"
                            seconds = trial_seconds(row)
                            progress(
                                f"{step} {row['scenario']}: {verdict}"
                                + (f" ({seconds}s)" if seconds is not None else "")
                            )
                        report["harbor_exit_codes"].append(exit_code)
                        report["trials"].extend(rows)
                        outcome.passed = (
                            exit_code == 0 and len(rows) == 1 and rows[0]["scenario"] == task.name and rows[0]["passed"]
                        )
        trials = report["trials"]
        report["passed_trials"] = sum(t["passed"] for t in trials)
        report["total_trials"] = len(trials)
        report["authoring_attempts_spent"] = ledger.spent("authoring", report_path.resolve())
        report["experiment_completed"] = Counter(t["scenario"] for t in trials) == Counter(
            dict.fromkeys(scenarios, args.attempts)
        )
        if report["experiment_completed"] and all(t["passed"] for t in trials):
            report["status"] = "passed"

    try:
        run_experiment(
            output,
            report,
            body,
            prefix="sapi-yaml-generation",
            command="sapi-lab generate",
            stages=["controls", "native images", "prompts"]
            + [f"authoring {attempt}/{args.attempts}" for attempt in range(1, args.attempts + 1)],
        )
    except ValueError as error:
        parser.error(str(error))
    report.setdefault("passed_trials", sum(t["passed"] for t in report["trials"]))
    report.setdefault("total_trials", len(report["trials"]))
    try:
        write_summary(report, Path(output).resolve())
    except (OSError, KeyError, ValueError) as error:
        print(f"SUMMARY.md not written: {error}", file=sys.stderr)
    print(json.dumps({"status": report["status"], "passed": report["passed_trials"], "total": report["total_trials"]}))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
