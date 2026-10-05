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
import tempfile
import uuid
from typing import Any

from sapi_config_lab.paths import workspace_root
from sapi_config_lab.experiments.tasks import stage_tasks
from sapi_config_lab.scenarios import SCENARIOS, select_scenarios
from sapi_config_lab.experiments.host import harbor_command
from sapi_config_lab.experiments.provenance import host_environment, source_manifest

ROOT = workspace_root()
IMAGE = "sapi-config-lab-n8n:2.41.5"
TASKS = {"invoice-total", "ticket-routing", "competitor-report"}


def write_json(path, data):
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")


def command(args, log, timeout=2400):
    with log.open("w") as stream:
        completed = subprocess.run(args, cwd=ROOT, stdout=stream, stderr=subprocess.STDOUT, timeout=timeout)
    return completed.returncode


def load_trials(job):
    trials = []
    for path in sorted(job.glob("*/result.json")):
        trial = json.loads(path.read_text())
        report_path = path.parent / "verifier/report.json"
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
        harbor = harbor_command()
        actual = subprocess.check_output([*harbor, "--version"], text=True).strip()
        if actual != "0.21.0":
            raise RuntimeError(f"Expected Harbor 0.21.0, got {actual}")
        report["docker_version"] = subprocess.check_output(
            ["docker", "version", "--format", "{{.Server.Version}}"], text=True
        ).strip()
        # Metadata only: never docker inspect environment or read existing volumes.
        before = subprocess.check_output(["docker", "ps", "--format", "{{.ID}} {{.Names}} {{.Image}}"], text=True)
        (output / "existing-containers.txt").write_text(before)
        print(f"Local regression tests; report directory: {output}", flush=True)
        rc = command(
            [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v", "-p", "test_*.py"],
            output / "local-tests.log",
            300,
        )
        report["checks"].append({"name": "local_tests", "passed": rc == 0, "exit_code": rc})
        if rc:
            raise RuntimeError("Local tests failed; see local-tests.log")
        if not args.skip_build:
            print("Building isolated pinned n8n image", flush=True)
            rc = command(
                ["docker", "build", "-f", "infra/Dockerfile", "-t", IMAGE, "."], output / "image-build.log", 1200
            )
            if rc:
                raise RuntimeError("Image build failed; see image-build.log")
        report["image_id"] = subprocess.check_output(
            ["docker", "image", "inspect", IMAGE, "--format", "{{.Id}}"], text=True
        ).strip()
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
                "sapi_config_lab.experiments.transport",
                "--artifacts",
                "/probe",
            ]
        )
        try:
            rc = command(["docker", "start", "--attach", probe_name], output / "transport.log", 1800)
            subprocess.check_call(["docker", "cp", probe_name + ":/probe", str(output / "transport")])
        finally:
            subprocess.run(["docker", "rm", "--force", probe_name], capture_output=True, check=False)
        probe = json.loads((output / "transport/summary.json").read_text())
        report["transport"] = probe
        report["checks"].append({"name": "real_n8n_transport", "passed": rc == 0 and probe.get("passed") is True})
        # macOS Desktop mounts may be blocked for the Docker VM. Stage only lab tasks
        # and logs under /private/tmp; never mount the user's existing n8n folders.
        staging = Path(
            tempfile.mkdtemp(prefix="sapi-lab-harbor-", dir="/private/tmp" if Path("/private/tmp").exists() else None)
        )
        stage_tasks(staging / "tasks", scenarios=tuple(selected))
        shutil.copytree(staging / "tasks", output / "task-packages")
        for agent in ("oracle", "nop"):
            print(f"Harbor {agent}: {len(selected)} tasks through real n8n", flush=True)
            argv = [
                *harbor,
                "run",
                "--path",
                str(staging / "tasks"),
                "--agent",
                agent,
                "--n-concurrent",
                "1",
                "--max-retries",
                "0",
                "--jobs-dir",
                str(staging / "jobs"),
                "--job-name",
                agent,
                "--force-build",
            ]
            rc = command(argv, output / f"harbor-{agent}.log")
            shutil.copytree(staging / "jobs" / agent, output / "jobs" / agent)
            trials = load_trials(output / "jobs" / agent)
            expected = 1.0 if agent == "oracle" else 0.0
            passed = (
                rc == 0
                and len(trials) == len(selected)
                and {x["task_name"] for x in trials} == set(selected)
                and all(x["rewards"] == {"reward": expected} and not x["exception"] for x in trials)
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
            if (staging / "jobs").exists():
                shutil.copytree(staging / "jobs", output / "jobs", dirs_exist_ok=True)
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
            [sys.executable, "-m", "sapi_config_lab.experiments.live", "--stub-report", str(output / "report.json")],
            cwd=ROOT,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
