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

from sapi_config_lab.coordinate.benchmark_discovery import select_benchmarks
from sapi_config_lab.coordinate.evaluation import trial_accepted
from sapi_config_lab.coordinate.ledger import open_ledger, parse_ceilings
from sapi_config_lab.coordinate.packages import AUTHOR_AGENT, CATALOG_VARIANTS, scenario_catalog, selected_cases
from sapi_config_lab.coordinate.runs import Run, load_trials, progress, run_experiment, trial_seconds
from sapi_config_lab.evidence import sha256
from sapi_config_lab.execute.host import LAB_IMAGE, HostConfig, run_logged
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
        passed = agent.get("status") == "submitted" and trial_accepted(trial)
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
        choices=CATALOG_VARIANTS,
        default="full",
        help="Experiment arm: the full operation catalog (default) or only the operations the scenario's reference uses",
    )
    args = parser.parse_args(argv)
    try:
        benchmarks = select_benchmarks(ROOT / "benchmarks", args.scenario)
        scenarios = tuple(item.name for item in benchmarks)
        by_name = {item.name: item for item in benchmarks}
        series_ceilings = parse_ceilings(args.series_ceiling)
    except ValueError as error:
        parser.error(str(error))
    if not 1 <= args.attempts <= MAX_ATTEMPTS:
        parser.error(f"--attempts must be between 1 and {MAX_ATTEMPTS}")
    budgets = {item.name: item.budgets.authoring_attempts for item in benchmarks}
    capped = [s for s, budget in budgets.items() if budget is not None and args.attempts > budget]
    if capped:
        parser.error("--attempts exceeds the authoring budget of " + ", ".join(capped))
    for name in scenarios:
        benchmark = by_name[name]
        if args.catalog not in benchmark.config.get("authoring", {}).get("catalogs", ("full",)):
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
        progress(f"controls: unpaid control suite first; log {run.output / 'control.log'}")
        started = time.monotonic()
        control = run_logged(
            [sys.executable, "-m", "sapi_config_lab.coordinate.controls", "--report-dir", str(run.output / "control")]
            + [arg for scenario in scenarios for arg in ("--scenario", scenario)],
            run.output / "control.log",
            timeout=None,
        )
        if control or json.loads((run.output / "control/report.json").read_text()).get("status") != "passed":
            raise RuntimeError("Control suite failed; model generation was not started")
        progress(f"controls: passed ({round(time.monotonic() - started)}s)")
        run.check("after-controls")
        ledger = open_ledger(
            run.output, args.series_dir, {"authoring": ceiling}, args.stop_after_failure, series_ceilings
        )
        report["ledger"] = str(ledger.path)
        run.use_image(LAB_IMAGE)
        overlays: dict[str, dict] = {}
        progress(f"staging: {len(scenarios)} generation task packages")
        report["prompt_sha256"] = run.stage("generation", benchmarks, cases=overlays, catalog=args.catalog)
        if args.catalog == "scenario":
            shown = {s: scenario_catalog(by_name[s]) for s in scenarios}
            report["catalog"]["operations"] = {
                s: sorted(yaml.safe_load(text)["operations"]) for s, text in shown.items()
            }
            report["catalog"]["sha256"] = {s: hashlib.sha256(text.encode()).hexdigest() for s, text in shown.items()}
        report["fixture_overlay"] = sorted(overlays)
        report["private_cases_sha256"] = {
            s: sha256(run.tasks / s / "tests/cases.json") for s in scenarios if selected_cases(by_name[s]) is not None
        }
        report_path = run.output / "report.json"
        jobs = []
        for attempt in range(1, args.attempts + 1):
            run.check(f"before-attempt-{attempt}")
            step = f"[{attempt}/{args.attempts}]"
            progress(f"{step} authoring, then verifying: {', '.join(scenarios)}")
            job = f"generated-{attempt}"
            jobs.append(run.output / "jobs" / job)
            with ledger.reserved(
                "authoring", f"{run.output.name}/{job}", len(scenarios), report_path, ceiling
            ) as outcome:
                exit_code, _ = run.harbor(
                    job, run.tasks, AUTHOR_AGENT, agent_key="upstream=" + args.upstream, attempts="1"
                )
                rows = summarize_trials(jobs[-1])
                for row in rows:
                    verdict = "passed" if row["passed"] else f"failed at {row['failure_stage']}"
                    seconds = trial_seconds(row)
                    progress(f"{step} {row['scenario']}: {verdict}" + (f" ({seconds}s)" if seconds is not None else ""))
                report["harbor_exit_codes"].append(exit_code)
                report["trials"].extend(rows)
                outcome.passed = exit_code == 0 and len(rows) == len(scenarios) and all(r["passed"] for r in rows)
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
        run_experiment(output, report, body, prefix="sapi-yaml-generation")
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
