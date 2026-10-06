#!/usr/bin/env python3
"""Control suite, then independent model-authored YAML trials in Harbor/n8n."""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import sys

from sapi_config_lab.paths import workspace_root
from sapi_config_lab.coordinate.controls import summarize_trials
from sapi_config_lab.coordinate.packages import stage_tasks
from sapi_config_lab.coordinate.provenance import source_manifest
from sapi_config_lab.coordinate.scenarios import EXPANSION_SCENARIOS, EXTENSION_SCENARIOS, select_scenarios
from sapi_config_lab.execute.host import (
    collect_jobs,
    harbor_command,
    harbor_run_args,
    image_id,
    pin_base_image,
    run_logged,
    staging_dir,
)
from sapi_config_lab.evidence import write_json
from sapi_config_lab.coordinate.expansion import (
    ExpansionSeries,
    RefinementSeries,
    fresh_case_overlay,
    overlay_sha256,
    select_series_scenarios,
)

ROOT = workspace_root()


def finalize_generation(
    report: dict,
    output: Path,
    staging: Path | None,
    frozen: dict,
    scenarios: tuple[str, ...],
    attempts: int,
    series: ExpansionSeries | None = None,
) -> None:
    """Preserve partial evidence and always write a report after collection errors."""

    def collect(stage, operation):
        try:
            return operation()
        except Exception as error:
            report.setdefault("collection_errors", []).append(
                {"stage": stage, "error": f"{type(error).__name__}: {error}"}
            )
            return None

    if staging:

        def preserve():
            collect_jobs(staging, output / "jobs")
            return True

        if collect("preserve_jobs", preserve):
            collect("cleanup_owned_staging", lambda: shutil.rmtree(staging))
        else:
            report["retained_staging"] = str(staging)
    report["trials"] = collect("trial_summary", lambda: summarize_trials(output / "jobs/generated")) or []
    report["frozen_core_unchanged"] = collect("source_manifest", source_manifest) == frozen
    trials = report["trials"]
    report["passed_trials"] = sum(trial["passed"] for trial in trials)
    report["total_trials"] = len(trials)
    if series is not None:
        ledger = collect("series_ledger", lambda: json.loads(series.path.read_text()))
        if ledger is not None:

            def outcomes():
                own = [event for event in ledger["events"] if event["report"] == str(output / "report.json")]
                report["authoring_attempts_spent"] = sum(
                    event["count"] for event in own if event["phase"] == "authoring"
                )
                report["attempt_outcomes"] = own

            collect("series_outcomes", outcomes)
    complete = (
        Counter(trial["scenario"] for trial in trials) == Counter({name: attempts for name in scenarios})
        and not report.get("error")
        and not report.get("collection_errors")
        and report.get("harbor_exit_code") == 0
    )
    report["experiment_completed"] = complete
    report["status"] = (
        "passed"
        if complete and report["frozen_core_unchanged"] and all(trial["passed"] for trial in trials)
        else "failed"
    )
    report["finished_at"] = datetime.now(timezone.utc).isoformat()
    # Write the machine-readable report before optional Markdown rendering.
    write_json(output / "report.json", report)

    def summary():
        lines = [
            "# YAML generation experiment",
            "",
            f"Status: {report['status']}. Completed: {complete}.",
            f"Passed: {report['passed_trials']}/{len(trials)}. Frozen core unchanged: {report['frozen_core_unchanged']}.",
            "",
            "| Task | Passed | Failure stage | Artifacts |",
            "|---|---|---|---|",
        ]
        for trial in trials:
            path = Path(trial["result_path"]).parent.relative_to(output)
            lines.append(
                f"| {trial['scenario']} | {trial['passed']} | {trial['failure_stage'] or '—'} | [YAML]({path}/agent/submission.yaml) · [Verification]({path}/verifier/evaluation/report.json) |"
            )
        lines += ["", "## Limits", ""] + ["- " + item for item in report["limitations"]]
        (output / "SUMMARY.md").write_text("\n".join(lines) + "\n")

    collect("summary_markdown", summary)
    if report.get("collection_errors"):
        report["status"] = "failed"
        report["experiment_completed"] = False
        write_json(output / "report.json", report)
    print(
        json.dumps(
            {
                "status": report["status"],
                "passed": report["passed_trials"],
                "total": len(trials),
                "report": str(output / "report.json"),
            }
        ),
        flush=True,
    )


def main(argv: list[str] | None = None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--attempts", type=int, default=3, help="Independent calls per task, no feedback/repair")
    parser.add_argument("--report-dir", type=Path)
    parser.add_argument("--upstream", default="http://127.0.0.1:8765/run")
    parser.add_argument("--scenario", action="append", help="Explicit task selection; defaults to the original three")
    parser.add_argument("--series-dir", type=Path, help="Required durable attempt ledger for expansion")
    parser.add_argument(
        "--series-scenario",
        action="append",
        help="Freeze this ordered series subset; repeat per scenario, default all four",
    )
    args = parser.parse_args(argv)
    try:
        scenarios = tuple(select_scenarios(tuple(args.scenario) if args.scenario else None))
        if "daily-digest" in scenarios:
            parser.error("daily-digest authoring needs the retired lifecycle series runner; see docs/RETIRED.md")
    except ValueError as error:
        parser.error(str(error))
    expansion = any(name in EXPANSION_SCENARIOS for name in scenarios)
    refinement = any(name in EXTENSION_SCENARIOS for name in scenarios)
    if args.series_scenario and not expansion:
        parser.error("--series-scenario requires an expansion --scenario")
    series_scenarios = None
    if expansion:
        try:
            series_scenarios = select_series_scenarios(tuple(args.series_scenario) if args.series_scenario else None)
            if any(name not in series_scenarios for name in scenarios):
                raise ValueError("Task scenario is not selected for this expansion series")
        except ValueError as error:
            parser.error(str(error))
    if expansion and (len(scenarios) != 1 or args.attempts != 2):
        parser.error("Expansion runs admit one named scenario and exactly two one-shot attempts")
    if expansion and not args.series_dir:
        parser.error("Expansion requires --series-dir to enforce the shared attempt ceilings")
    if refinement and (scenarios != ("revise-answer",) or args.attempts != 3 or not args.series_dir):
        parser.error("Refinement requires only revise-answer, --attempts 3 and a fresh --series-dir")
    if not 1 <= args.attempts <= 10:
        parser.error("--attempts must be between 1 and 10")
    output = (
        args.report_dir or ROOT / "reports" / (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-generation")
    ).resolve()
    if output.exists():
        parser.error("Choose a new report directory; an existing directory is never overwritten")
    output.mkdir(parents=True)
    frozen = source_manifest()
    write_json(output / "frozen-core.json", frozen)
    report = {
        "schema": "sapi-lab-generation/v1",
        "status": "failed",
        "experiment_completed": False,
        "attempts_per_task": args.attempts,
        "scenarios": list(scenarios),
        "authoring_attempt_ceiling": len(scenarios) * args.attempts,
        "runtime_llm_mode": "stub",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "trials": [],
        "limitations": [
            "Explicitly selected task families with a fixed specialized catalog; not held-out generalization.",
            "Single-shot YAML output; no tools, retries or repair requested.",
            "Wrapper tool restrictions are prompt-only, audited from CLI stderr, not a filesystem sandbox.",
            "Runtime model steps are deterministic stubs; only YAML generation calls the live model.",
            "Verifier checks exact task role/source obligations while permitting renamed step IDs.",
            "The wrapper does not provide reliable token/cost breakdowns; costs remain unknown.",
        ],
    }
    staging = None
    series = None
    reservation = None
    try:
        if expansion or refinement:
            series = (
                RefinementSeries(args.series_dir)
                if refinement
                else ExpansionSeries(args.series_dir, scenarios=series_scenarios)
            )
            report["series_ledger"] = str(series.path)
            report["series_scenarios"] = list(series.scenarios)
            report["series_ceilings"] = dict(series.ceilings)
        print(f"Control suite first; reports: {output}", flush=True)
        control = run_logged(
            [sys.executable, "-m", "sapi_config_lab.coordinate.controls", "--report-dir", str(output / "control")]
            + [arg for scenario in scenarios for arg in ("--scenario", scenario)],
            output / "control.log",
            timeout=3600,
        )
        baseline = json.loads((output / "control/report.json").read_text())
        if control or baseline.get("status") != "passed":
            raise RuntimeError("Control suite failed; model generation was not started")
        if source_manifest() != frozen:
            raise RuntimeError("Frozen compiler/catalog/verifier changed during controls")
        harbor = harbor_command()
        image = image_id("sapi-config-lab-n8n:2.41.5")
        report["base_image_id"] = image
        frozen_image = pin_base_image(image, "sapi-config-lab-generation")
        report["frozen_image"] = frozen_image
        staging = staging_dir("sapi-yaml-generation-")
        report["prompt_sha256"] = stage_tasks(
            staging / "tasks", mode="generation", image=frozen_image, scenarios=scenarios
        )
        if expansion or refinement:
            # Freeze every author-visible byte before producing fresh evaluation inputs.
            write_json(output / "frozen-prompts.json", report["prompt_sha256"])
            private_cases = (
                {scenarios[0]: EXTENSION_SCENARIOS[scenarios[0]].cases()}
                if refinement
                else fresh_case_overlay(scenarios[0])
            )
            write_json(staging / "tasks" / scenarios[0] / "tests/cases.json", private_cases)
            report["private_cases_sha256"] = {scenarios[0]: overlay_sha256(private_cases)}
        shutil.copytree(staging / "tasks", output / "task-packages")

        def authoring_run(job: str, attempts: int) -> list[str]:
            return harbor_run_args(
                harbor,
                staging / "tasks",
                staging / "jobs",
                job,
                "sapi_config_lab.author.agent:WrapperYamlAgent",
                agent_key="upstream=" + args.upstream,
                attempts=str(attempts),
            )

        argv = authoring_run("generated", args.attempts)
        report["command"] = argv
        write_json(output / "experiment.json", report)
        print(
            f"Generating {len(scenarios) * args.attempts} independent YAML submissions; no feedback or repairs",
            flush=True,
        )
        if expansion or refinement:
            assert series is not None
            report["commands"] = []
            for attempt in range(1, args.attempts + 1):
                if source_manifest() != frozen:
                    raise RuntimeError("Frozen sources changed before authoring")
                reservation = series.reserve(scenarios[0], "authoring", str(attempt), output / "report.json")
                job = "generated-attempt-" + str(attempt)
                command = authoring_run(job, 1)
                report["commands"].append(command)
                exit_code = run_logged(command, output / (job + ".log"), timeout=1200)
                # Stable evidence location for selection, including failed attempts.
                for path in (staging / "jobs" / job).glob("*/result.json"):
                    shutil.copytree(path.parent, output / "jobs/generated" / path.parent.name)
                trials = summarize_trials(output / "jobs/generated")
                passed = exit_code == 0 and len(trials) == attempt and all(t["passed"] for t in trials)
                passed = passed and source_manifest() == frozen
                series.finish(reservation, passed)
                reservation = None
                report["harbor_exit_code"] = exit_code
                if not passed:
                    raise RuntimeError("Authoring/stub gate failed; remaining paid attempts were not started")
        else:
            report["harbor_exit_code"] = run_logged(
                argv, output / "harbor-generation.log", timeout=1200 * args.attempts
            )
    except Exception as error:
        if reservation is not None and series is not None:
            series.finish(reservation, False)
        report["error"] = f"{type(error).__name__}: {error}"
    finally:
        finalize_generation(report, output, staging, frozen, scenarios, args.attempts, series)
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
