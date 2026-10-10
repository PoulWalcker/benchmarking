#!/usr/bin/env python3
"""Unpaid controls: local tests, pinned n8n image, transport probes, Harbor oracle and nop."""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
import json
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Any

from sapi_config_lab.coordinate.evaluation import control_passed
from sapi_config_lab.coordinate.native_tasks import invoke, policy, select_tasks
from sapi_config_lab.coordinate.provenance import host_environment
from sapi_config_lab.coordinate.runs import Run, progress, run_experiment
from sapi_config_lab.execute.host import LAB_IMAGE, run_logged
from sapi_config_lab.execute.n8n import PINNED_N8N_VERSION
from sapi_config_lab.paths import workspace_root

ROOT = workspace_root()
LOCAL_TESTS_SECONDS = 300


def transport_probe(run: Run, *, skip_build: bool = False) -> dict:
    """Dispatch the independent transport task; Harbor owns its image and lifecycle."""
    if skip_build:
        run.use_image(LAB_IMAGE)
    task = run.output / "transport-task"
    shutil.copytree(ROOT / "tests/support/native-transport", task)
    environment = task / "environment"
    shutil.copyfile(ROOT / "infra/Dockerfile", environment / "Dockerfile")
    shutil.copyfile(ROOT / ".dockerignore", environment / ".dockerignore")
    for name in ("src", "generation", "tasks"):
        shutil.copytree(
            ROOT / name, environment / name, ignore=shutil.ignore_patterns("__pycache__", "evaluation", "cases.json")
        )
    if skip_build:
        text = (
            (task / "task.toml")
            .read_text()
            .replace("[environment]\n", f'[environment]\ndocker_image = "{run.image}"\n')
        )
        (task / "task.toml").write_text(text)
        (environment / "docker-compose.yaml").write_text(f"services:\n  main:\n    image: {run.image}\n")
    exit_code = run.transport(task, skip_build=skip_build)
    job = run.output / "jobs/transport"
    summaries = list(job.glob("*/verifier/transport/summary.json"))
    probe = json.loads(summaries[0].read_text()) if len(summaries) == 1 else {"passed": False}
    results = list(job.glob("*/result.json"))
    native = json.loads(results[0].read_text()) if len(results) == 1 else {}
    verifier = native.get("verifier_result") or {}
    probe["native_completed"] = native.get("exception_info") is None and verifier.get("rewards") == {"reward": 1.0}
    probe["passed"] = probe.get("passed") is True and probe["native_completed"]
    probe.update(exit_code=exit_code, job_path=str(job.relative_to(run.output)))
    if summaries:
        probe["evidence_path"] = str(summaries[0].parent.relative_to(run.output))
        probe["runtime_versions_path"] = str(
            (summaries[0].parent.parent / "runtime-versions.txt").relative_to(run.output)
        )
    return probe


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report-dir", type=Path)
    parser.add_argument("--scenario", action="append", dest="scenarios")
    parser.add_argument("--skip-build", action="store_true", help="Reuse already built lab image (development only)")
    args = parser.parse_args(argv)
    tasks = select_tasks(ROOT / "tasks", args.scenarios)
    selected = tuple(task.name for task in tasks)
    policies = {task.name: policy(task) for task in tasks}
    output = args.report_dir or ROOT / "reports" / datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    report: dict[str, Any] = {
        "schema": "sapi-lab-harbor/v1",
        "mode": "stub",
        "n8n_version": PINNED_N8N_VERSION,
        "host_environment": host_environment(),
        "image": LAB_IMAGE,
        "checks": [],
        "scenarios": list(selected),
        "oracle": {},
        "nop": {},
    }

    def check(name: str, passed: bool, **details: Any) -> None:
        report["checks"].append({"name": name, "passed": passed, **details})
        if not passed:
            raise RuntimeError(f"Control check failed: {name}")

    def body(run: Run) -> None:
        report["docker_version"] = subprocess.check_output(
            ["docker", "version", "--format", "{{.Server.Version}}"], text=True
        ).strip()
        progress(f"local tests; report directory {run.output}")
        with run.step("local tests", log="local-tests"):
            exit_code = run_logged(
                [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v", "-p", "test_*.py"],
                run.output / "local-tests.log",
                stage="local tests",
                timeout=LOCAL_TESTS_SECONDS,
            )
            check("local_tests", exit_code == 0, exit_code=exit_code)
        for task in tasks:
            with run.step("plan " + task.name):
                invoke(
                    task,
                    {"action": "plan", "submission": str(task / "solution/config.yaml"), "options": {"mode": "stub"}},
                    run.sources,
                )
        progress("transport: Harbor builds and runs real n8n HTTP transport and rejection probes")
        with run.step("transport", log="transport"):
            report["transport"] = transport_probe(run, skip_build=args.skip_build)
            check(
                "real_n8n_transport",
                report["transport"]["exit_code"] == 0 and report["transport"].get("passed") is True,
            )
        if not args.skip_build:
            run.use_image(LAB_IMAGE)
        progress(f"native images: verifying sources and {len(selected)} checked-in tasks")
        run.use_native_tasks(tasks, build=not args.skip_build)
        for agent in ("oracle", "nop"):
            with run.step(f"harbor_{agent}"):
                progress(f"{agent}: {len(selected)} Harbor tasks through real n8n")
                trials, exit_codes = [], []
                for task in tasks:
                    code, rows = run.harbor(agent + "-" + task.name, task, agent)
                    exit_codes.append(code)
                    trials.extend(rows)
                exit_code = next((code for code in exit_codes if code), 0)
                passed = (
                    exit_code == 0
                    and sorted(t["task_name"] for t in trials) == sorted(selected)
                    and all(
                        control_passed(
                            agent, trial, reference_reward=policies[trial["task_name"]].get("reference_reward")
                        )
                        for trial in trials
                    )
                )
                report[agent] = {"passed": passed, "harbor_exit_code": exit_code, "trials": trials}
                progress(f"{agent}: {'passed' if passed else 'failed'}")
                failed_task = next((task.name for task, code in zip(tasks, exit_codes, strict=True) if code), None)
                if not passed and failed_task is None:
                    failed_task = next(
                        (
                            trial["task_name"]
                            for trial in trials
                            if not control_passed(
                                agent, trial, reference_reward=policies[trial["task_name"]].get("reference_reward")
                            )
                        ),
                        selected[0],
                    )
                with run.step(f"harbor_{agent}", log=agent + "-" + (failed_task or selected[0])):
                    check(f"harbor_{agent}", passed)
        report["status"] = "passed"

    try:
        run_experiment(output, report, body, prefix="sapi-lab-harbor")
    except ValueError as error:
        parser.error(str(error))
    print(json.dumps({"status": report["status"], "report": str(Path(output).resolve() / "report.json")}), flush=True)
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
