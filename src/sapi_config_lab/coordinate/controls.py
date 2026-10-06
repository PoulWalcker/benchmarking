#!/usr/bin/env python3
"""One command: local tests, isolated n8n image, Harbor oracle/nop, saved report."""

from __future__ import annotations
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import subprocess
import sys
import uuid
from typing import Any

from sapi_config_lab.evidence import write_json
from sapi_config_lab.paths import workspace_root
from sapi_config_lab.coordinate.packages import stage_tasks
from sapi_config_lab.coordinate.scenarios import SCENARIOS, select_scenarios
from sapi_config_lab.execute.host import (
    build_image,
    checked_harbor,
    collect_jobs,
    harbor_run_args,
    image_id,
    run_logged,
    running_containers,
    staging_dir,
)
from sapi_config_lab.coordinate.provenance import host_environment, source_manifest

ROOT = workspace_root()
IMAGE = "sapi-config-lab-n8n:2.41.5"
TASKS = {"invoice-total", "ticket-routing", "competitor-report"}


def load_trials(job: Path) -> list[dict]:
    """One row per Harbor trial: its reward, exception and the verifier's report."""
    trials = []
    for path in sorted(job.glob("*/result.json")):
        trial = json.loads(path.read_text())
        report_path = path.parent / "verifier/evaluation/report.json"
        acceptance = json.loads(report_path.read_text()) if report_path.exists() else None
        trials.append(
            {
                "task_name": trial.get("task_name"),
                "rewards": (trial.get("verifier_result") or {}).get("rewards"),
                "exception": trial.get("exception_info"),
                "result_path": str(path),
                "acceptance": acceptance,
            }
        )
    return trials


def trial_accepted(trial: dict) -> bool:
    """Harbor rewarded the trial 1.0 without an exception and the verifier accepted it."""
    return (
        not trial["exception"]
        and trial["rewards"] == CONTROL_REWARDS["oracle"]
        and bool(trial["acceptance"])
        and trial["acceptance"].get("passed") is True
    )


def summarize_trials(job: Path) -> list[dict]:
    """load_trials for model-authored submissions, plus where a failed one stopped."""
    rows = []
    for trial in load_trials(job):
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


CONTROL_REWARDS = {"oracle": {"reward": 1.0}, "nop": {"reward": 0.0}}


def control_rewards_met(agent: str, trials: list[dict]) -> bool:
    """The oracle/nop reward gate, which is the lab's measuring instrument.

    A control run means exactly this: the reference solution scores 1.0, the
    empty agent scores 0.0, and neither raised. Nothing else may reach this
    comparison -- a rubric score in particular is a separate document and must
    never be read as a reward. The literal lives here so that the three
    experiments that run controls cannot drift apart on what "passed" means.

    Trial count, task names and verifier acceptance stay with each experiment:
    they differ per experiment, and a shared check could only guess at them.
    """
    return all(not row["exception"] and row["rewards"] == CONTROL_REWARDS[agent] for row in trials)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report-dir", type=Path)
    parser.add_argument("--scenario", action="append", dest="scenarios", choices=sorted(SCENARIOS))
    parser.add_argument(
        "--live", action="store_true", help="After passing stubs, run separate existing codex wrapper trials"
    )
    parser.add_argument("--skip-build", action="store_true", help="Reuse already built lab image (development only)")
    args = parser.parse_args()
    selected = select_scenarios(args.scenarios)
    if args.live and set(selected) != TASKS:
        parser.error("Expanded controls require a separately admitted generated/live series")
    output = (args.report_dir or ROOT / "reports" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")).resolve()
    output.mkdir(parents=True, exist_ok=True)
    if (output / "report.json").exists():
        parser.error("Choose a new report directory; existing report.json will not be overwritten")
    report: dict[str, Any] = {
        "schema": "sapi-lab-harbor/v1",
        "status": "failed",
        "mode": "stub",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "n8n_version": "2.41.5",
        "harbor_version": "0.21.0",
        "host_environment": host_environment(),
        "image": IMAGE,
        "checks": [],
        "scenarios": list(selected),
        "oracle": {},
        "nop": {},
    }
    original_sources = source_manifest()
    write_json(output / "source-manifest.json", original_sources)
    report["source_manifest"] = "source-manifest.json"
    staging = None
    try:
        harbor, _ = checked_harbor()
        report["docker_version"] = subprocess.check_output(
            ["docker", "version", "--format", "{{.Server.Version}}"], text=True
        ).strip()
        # Metadata only: never docker inspect environment or read existing volumes.
        before = running_containers()
        (output / "existing-containers.txt").write_text(before)
        print(f"Local regression tests; report directory: {output}", flush=True)
        rc = run_logged(
            [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v", "-p", "test_*.py"],
            output / "local-tests.log",
            timeout=300,
        )
        report["checks"].append({"name": "local_tests", "passed": rc == 0, "exit_code": rc})
        if rc:
            raise RuntimeError("Local tests failed; see local-tests.log")
        if not args.skip_build:
            print("Building isolated pinned n8n image", flush=True)
            build_image(IMAGE, output / "image-build.log")
        report["image_id"] = image_id(IMAGE)
        versions = subprocess.check_output(
            [
                "docker",
                "run",
                "--rm",
                IMAGE,
                "sh",
                "-c",
                "n8n --version && node --version && python3 --version && apk info -v",
            ],
            text=True,
        )
        (output / "runtime-versions.txt").write_text(versions)
        print("Real n8n HTTP transport and rejection probes", flush=True)
        probe_name = "sapi-lab-transport-" + uuid.uuid4().hex[:12]
        subprocess.check_call(
            [
                "docker",
                "create",
                "--name",
                probe_name,
                IMAGE,
                "python3",
                "-m",
                "sapi_config_lab.coordinate.transport",
                "--artifacts",
                "/probe",
            ]
        )
        try:
            rc = run_logged(["docker", "start", "--attach", probe_name], output / "transport.log", timeout=1800)
            subprocess.check_call(["docker", "cp", probe_name + ":/probe", str(output / "transport")])
        finally:
            subprocess.run(["docker", "rm", "--force", probe_name], capture_output=True, check=False)
        probe = json.loads((output / "transport/summary.json").read_text())
        report["transport"] = probe
        report["checks"].append({"name": "real_n8n_transport", "passed": rc == 0 and probe.get("passed") is True})
        staging = staging_dir("sapi-lab-harbor-")
        stage_tasks(staging / "tasks", scenarios=tuple(selected))
        shutil.copytree(staging / "tasks", output / "task-packages")
        for agent in ("oracle", "nop"):
            print(f"Harbor {agent}: {len(selected)} tasks through real n8n", flush=True)
            argv = harbor_run_args(harbor, staging / "tasks", staging / "jobs", agent, agent)
            rc = run_logged(argv, output / f"harbor-{agent}.log", timeout=2400)
            shutil.copytree(staging / "jobs" / agent, output / "jobs" / agent)
            trials = load_trials(output / "jobs" / agent)
            passed = (
                rc == 0
                and len(trials) == len(selected)
                and {x["task_name"] for x in trials} == set(selected)
                and control_rewards_met(agent, trials)
            )
            if agent == "oracle":
                passed = passed and all(x["acceptance"] and x["acceptance"].get("passed") for x in trials)
            report[agent] = {"passed": passed, "harbor_exit_code": rc, "command": argv, "trials": trials}
            report["checks"].append({"name": f"harbor_{agent}", "passed": passed})
        report["status"] = "passed" if all(item["passed"] for item in report["checks"]) else "failed"
    except Exception as error:
        report["error"] = f"{type(error).__name__}: {error}"
    finally:
        if staging and staging.exists():
            # Preserve a failed job before cleaning only this invocation's temp tree.
            collect_jobs(staging, output / "jobs")
            shutil.rmtree(staging)
        report["finished_at"] = datetime.now(timezone.utc).isoformat()
        report["source_unchanged"] = source_manifest() == original_sources
        if not report["source_unchanged"]:
            report["status"] = "failed"
            report["error"] = "Public sources changed during the experiment; choose a new report directory and rerun"
        write_json(output / "report.json", report)
    print(json.dumps({"status": report["status"], "report": str(output / "report.json")}), flush=True)
    if report["status"] != "passed":
        return 1
    if args.live:
        return subprocess.call(
            [sys.executable, "-m", "sapi_config_lab.coordinate.live", "--stub-report", str(output / "report.json")],
            cwd=ROOT,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
