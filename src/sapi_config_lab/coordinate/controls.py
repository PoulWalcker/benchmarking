#!/usr/bin/env python3
"""Unpaid controls: local tests, pinned n8n image, transport probes, Harbor oracle and nop."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys
import uuid
from typing import Any

from sapi_config_lab.coordinate.runs import Run, run_experiment
from sapi_config_lab.coordinate.scenarios import SCENARIOS, select_scenarios
from sapi_config_lab.execute.host import LAB_IMAGE, build_image, run_logged
from sapi_config_lab.coordinate.provenance import host_environment
from sapi_config_lab.paths import workspace_root

ROOT = workspace_root()


def trial_accepted(trial: dict) -> bool:
    """Harbor rewarded the trial 1.0 without an exception and the verifier accepted it."""
    return (
        not trial["exception"]
        and trial["rewards"] == CONTROL_REWARDS["oracle"]
        and bool(trial["acceptance"])
        and trial["acceptance"].get("passed") is True
    )


CONTROL_REWARDS = {"oracle": {"reward": 1.0}, "nop": {"reward": 0.0}}


def control_rewards_met(agent: str, trials: list[dict]) -> bool:
    """Oracle scores 1.0, nop scores 0.0, neither raised; a rubric score is never a reward."""
    return all(not row["exception"] and row["rewards"] == CONTROL_REWARDS[agent] for row in trials)


def transport_probe(run: Run) -> dict:
    """Run the HTTP transport probes inside the lab image and copy their summary out."""
    name = "sapi-lab-transport-" + uuid.uuid4().hex[:12]
    subprocess.check_call(
        ["docker", "create", "--name", name, LAB_IMAGE, "python3", "-m", "sapi_config_lab.coordinate.transport"]
        + ["--artifacts", "/probe"]
    )
    try:
        exit_code = run_logged(["docker", "start", "--attach", name], run.output / "transport.log", timeout=1800)
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
    output = args.report_dir or ROOT / "reports" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    report: dict[str, Any] = {
        "schema": "sapi-lab-harbor/v1",
        "mode": "stub",
        "n8n_version": "2.41.5",
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
        print(f"Local regression tests; report directory: {run.output}", flush=True)
        exit_code = run_logged(
            [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v", "-p", "test_*.py"],
            run.output / "local-tests.log",
            timeout=300,
        )
        check("local_tests", exit_code == 0, exit_code=exit_code)
        if not args.skip_build:
            print("Building isolated pinned n8n image", flush=True)
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
        print("Real n8n HTTP transport and rejection probes", flush=True)
        report["transport"] = transport_probe(run)
        check("real_n8n_transport", report["transport"]["exit_code"] == 0 and report["transport"].get("passed") is True)
        run.stage("oracle", selected)
        for agent in ("oracle", "nop"):
            print(f"Harbor {agent}: {len(selected)} tasks through real n8n", flush=True)
            exit_code, trials = run.harbor(agent, run.tasks, agent, timeout=2400)
            passed = (
                exit_code == 0
                and sorted(t["task_name"] for t in trials) == sorted(selected)
                and control_rewards_met(agent, trials)
                and (agent == "nop" or all(t["acceptance"] and t["acceptance"].get("passed") for t in trials))
            )
            report[agent] = {"passed": passed, "harbor_exit_code": exit_code, "trials": trials}
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
