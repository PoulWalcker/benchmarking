#!/usr/bin/env python3
"""Unpaid controls: local tests, pinned n8n image, transport probes, Harbor oracle and nop."""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
import json
from pathlib import Path
import subprocess
import sys
from typing import Any
import uuid

from sapi_config_lab.coordinate.evaluation import control_passed, hosted_evaluation
from sapi_config_lab.coordinate.packages import job_seconds, verifier_bounds
from sapi_config_lab.coordinate.provenance import host_environment
from sapi_config_lab.coordinate.runs import Hosting, Run, progress, run_experiment
from sapi_config_lab.coordinate.scenarios import SCENARIOS, select_scenarios
from sapi_config_lab.execute.host import BUILD_TIMEOUT_SECONDS, LAB_IMAGE, build_image, run_logged
from sapi_config_lab.execute.n8n import PINNED_N8N_VERSION
from sapi_config_lab.paths import workspace_root

ROOT = workspace_root()
LOCAL_TESTS_SECONDS = 300
TRANSPORT_SECONDS = 1800


def suite_seconds(scenarios: tuple[str, ...]) -> int:
    """Outer limit of the whole suite: its fixed steps plus the oracle and nop jobs."""
    jobs = 2 * job_seconds(verifier_bounds(scenarios))
    return LOCAL_TESTS_SECONDS + BUILD_TIMEOUT_SECONDS + TRANSPORT_SECONDS + jobs


def simulated_hosting(scenarios: tuple[str, ...]) -> Hosting:
    """Stub runtime calls and the simulated judge: a hosted control costs nothing."""
    return Hosting("stub", hosted_evaluation(scenarios))


def transport_probe(run: Run) -> dict:
    """Run the HTTP transport probes inside the lab image and copy their summary out."""
    name = "sapi-lab-transport-" + uuid.uuid4().hex[:12]
    subprocess.check_call(
        [
            *("docker", "create", "--name", name, LAB_IMAGE),
            *("python3", "-m", "sapi_config_lab.coordinate.transport", "--artifacts", "/probe"),
        ]
    )
    try:
        exit_code = run_logged(
            ["docker", "start", "--attach", name], run.output / "transport.log", timeout=TRANSPORT_SECONDS
        )
        subprocess.check_call(["docker", "cp", name + ":/probe", str(run.output / "transport")])
    finally:
        subprocess.run(["docker", "rm", "--force", name], capture_output=True, check=False)
    probe = json.loads((run.output / "transport/summary.json").read_text())
    probe["exit_code"] = exit_code
    return probe


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report-dir", type=Path)
    parser.add_argument("--scenario", action="append", dest="scenarios", choices=sorted(SCENARIOS))
    parser.add_argument("--skip-build", action="store_true", help="Reuse already built lab image (development only)")
    args = parser.parse_args(argv)
    selected = tuple(select_scenarios(args.scenarios))
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
        exit_code = run_logged(
            [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v", "-p", "test_*.py"],
            run.output / "local-tests.log",
            timeout=LOCAL_TESTS_SECONDS,
        )
        check("local_tests", exit_code == 0, exit_code=exit_code)
        if not args.skip_build:
            progress("image: building the isolated pinned n8n image")
            build_image(LAB_IMAGE, run.output / "image-build.log")
        run.use_image(LAB_IMAGE)
        (run.output / "runtime-versions.txt").write_text(
            subprocess.check_output(
                [
                    "docker",
                    "run",
                    "--rm",
                    LAB_IMAGE,
                    "sh",
                    "-c",
                    "n8n --version && node --version && python3 --version && apk info -v",
                ],
                text=True,
            )
        )
        progress("transport: real n8n HTTP transport and rejection probes")
        report["transport"] = transport_probe(run)
        check("real_n8n_transport", report["transport"]["exit_code"] == 0 and report["transport"].get("passed") is True)
        progress(f"staging: {len(selected)} oracle task packages")
        run.stage("oracle", selected)
        hosting = simulated_hosting(selected)
        for agent in ("oracle", "nop"):
            progress(f"{agent}: {len(selected)} Harbor tasks through real n8n")
            exit_code, trials = run.harbor(agent, run.tasks, agent, hosting=hosting)
            passed = (
                exit_code == 0
                and sorted(t["task_name"] for t in trials) == sorted(selected)
                and all(control_passed(agent, trial) for trial in trials)
            )
            report[agent] = {"passed": passed, "harbor_exit_code": exit_code, "trials": trials}
            progress(f"{agent}: {'passed' if passed else 'failed'}")
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
