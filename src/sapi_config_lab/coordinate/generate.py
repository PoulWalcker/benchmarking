#!/usr/bin/env python3
"""Fresh controls, then model-authored YAML trials in Harbor/n8n, one reserved attempt at a time."""

from __future__ import annotations

import argparse
from collections import Counter
import copy
from datetime import datetime, timezone
import json
from pathlib import Path
import secrets
import sys
from typing import Any

from sapi_config_lab.coordinate.controls import trial_accepted
from sapi_config_lab.coordinate.ledger import open_ledger, parse_ceilings
from sapi_config_lab.coordinate.runs import Run, load_trials, run_experiment
from sapi_config_lab.coordinate.scenarios import SCENARIOS, select_scenarios
from sapi_config_lab.evidence import sha256
from sapi_config_lab.execute.agency import WRAPPER_UPSTREAM
from sapi_config_lab.execute.host import LAB_IMAGE, run_logged
from sapi_config_lab.paths import workspace_root

ROOT = workspace_root()
AUTHOR = "sapi_config_lab.author.agent:WrapperYamlAgent"


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
                "result_path": trial["result_path"],
            }
        )
    return rows


def fresh_case_overlay(scenario: str) -> dict:
    """Fresh values for the evaluator-only fixtures, staged after the prompt is frozen."""
    cases = copy.deepcopy(SCENARIOS[scenario].cases())
    token = secrets.token_hex(6).upper()
    increment = secrets.randbelow(400) + 31

    # Stable replacements preserve duplicate-ID controls and sibling metamorphisms.
    def transform(value, *, field="", positive=False):
        if isinstance(value, list):
            return [transform(item, field=field, positive=positive) for item in value]
        if isinstance(value, dict):
            return {key: transform(item, field=key, positive=positive) for key, item in value.items()}
        if isinstance(value, str):
            if field in {"id", "required_order_id"}:
                return token + "-" + value
            if field in {"text", "product_material", "marketing_material"} and value:
                return "Fixture " + token + ": " + value
        if field == "amount_minor" and positive and isinstance(value, int) and 0 < value < 1_000_000:
            return value + increment
        return value

    for kind in ("positive", "negative"):
        for case in cases[kind]:
            original = case["inputs"]
            case["inputs"] = transform(original, positive=kind == "positive")
            if "required_order_id" in original:
                # Keep the order named in the ticket aligned with the projected request.
                case["inputs"]["ticket"]["text"] = case["inputs"]["ticket"]["text"].replace(
                    original["required_order_id"], case["inputs"]["required_order_id"]
                )
    return cases


def write_summary(report: dict, output: Path) -> None:
    lines = [
        "# YAML generation experiment",
        "",
        f"Status: {report['status']}. Completed: {report['experiment_completed']}.",
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
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--attempts", type=int, default=3, help="Independent calls per task, no feedback/repair")
    parser.add_argument("--report-dir", type=Path)
    parser.add_argument("--upstream", default=WRAPPER_UPSTREAM)
    parser.add_argument("--scenario", action="append", help="Explicit task selection; defaults to the original three")
    parser.add_argument("--series-dir", type=Path, help="Reserve in a ledger shared with other runs")
    parser.add_argument("--series-ceiling", action="append", help="PHASE=N, fixed when a series ledger is created")
    parser.add_argument("--stop-after-failure", action="store_true", help="A failed attempt blocks later ones")
    args = parser.parse_args(argv)
    try:
        scenarios = tuple(select_scenarios(tuple(args.scenario) if args.scenario else None))
        series_ceilings = parse_ceilings(args.series_ceiling)
    except ValueError as error:
        parser.error(str(error))
    if "daily-digest" in scenarios:
        parser.error("daily-digest authoring needs the retired lifecycle series runner; see docs/RETIRED.md")
    if not 1 <= args.attempts <= 10:
        parser.error("--attempts must be between 1 and 10")
    output = args.report_dir or ROOT / "reports" / (
        datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-generation"
    )
    ceiling = len(scenarios) * args.attempts
    report: dict[str, Any] = {
        "schema": "sapi-lab-generation/v2",
        "experiment_completed": False,
        "attempts_per_task": args.attempts,
        "scenarios": list(scenarios),
        "authoring_attempt_ceiling": ceiling,
        "runtime_llm_mode": "stub",
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
        print(f"Control suite first; reports: {run.output}", flush=True)
        control = run_logged(
            [sys.executable, "-m", "sapi_config_lab.coordinate.controls", "--report-dir", str(run.output / "control")]
            + [arg for scenario in scenarios for arg in ("--scenario", scenario)],
            run.output / "control.log",
            timeout=3600,
        )
        if control or json.loads((run.output / "control/report.json").read_text()).get("status") != "passed":
            raise RuntimeError("Control suite failed; model generation was not started")
        run.check("after-controls")
        ledger = open_ledger(
            run.output, args.series_dir, {"authoring": ceiling}, args.stop_after_failure, series_ceilings
        )
        report["ledger"] = str(ledger.path)
        run.use_image(LAB_IMAGE)
        overlays = {s: fresh_case_overlay(s) for s in scenarios if SCENARIOS[s].fixture_overlay == "fresh"}
        report["prompt_sha256"] = run.stage("generation", scenarios, cases=overlays)
        report["fixture_overlay"] = sorted(overlays)
        report["private_cases_sha256"] = {s: sha256(run.tasks / s / "tests/cases.json") for s in scenarios}
        report_path = run.output / "report.json"
        jobs = []
        for attempt in range(1, args.attempts + 1):
            run.check(f"before-attempt-{attempt}")
            job = f"generated-{attempt}"
            jobs.append(run.output / "jobs" / job)
            with ledger.reserved(
                "authoring", f"{run.output.name}/{job}", len(scenarios), report_path, ceiling
            ) as outcome:
                exit_code, _ = run.harbor(
                    job, run.tasks, AUTHOR, timeout=1200, agent_key="upstream=" + args.upstream, attempts="1"
                )
                rows = summarize_trials(jobs[-1])
                report["harbor_exit_codes"].append(exit_code)
                report["trials"].extend(rows)
                outcome.passed = exit_code == 0 and len(rows) == len(scenarios) and all(r["passed"] for r in rows)
        trials = report["trials"]
        report["passed_trials"] = sum(t["passed"] for t in trials)
        report["total_trials"] = len(trials)
        report["authoring_attempts_spent"] = ledger.spent("authoring", report_path.resolve())
        report["experiment_completed"] = Counter(t["scenario"] for t in trials) == Counter(
            {s: args.attempts for s in scenarios}
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
    except Exception as error:
        print(f"SUMMARY.md not written: {error}", file=sys.stderr)
    print(json.dumps({"status": report["status"], "passed": report["passed_trials"], "total": report["total_trials"]}))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
