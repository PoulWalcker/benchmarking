"""Checkout's positive Harbor package and opt-in fresh-world oracle/nop proof."""

from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tarfile
import tempfile
import unittest
from unittest.mock import patch
import uuid

import yaml

from sapi_config_lab.coordinate.evaluation import main as evaluate_main
from sapi_config_lab.paths import workspace_root
from sapi_config_lab.pinned_source import PinnedSource

ROOT = workspace_root()
DIRECTORY = ROOT / "tasks/checkout-recovery"
PRIVATE_MARKERS = (
    b"Checkout-owned frozen task contracts",
    b"sapi-lab-task-contract/v1",
    b"Evaluator-only counterfactual narrative controls",
    b"MIGRATION_CHECKOUT_CREDENTIAL_05_9d031e",
)


def read(path):
    return json.loads(path.read_text())


def inspect_public(archive, reference):
    """Inspect every file across full public image layers or a stopped filesystem."""
    count = 0
    secrets = (*PRIVATE_MARKERS, reference)
    for member in archive:
        relative = member.name.removeprefix("./")
        if relative.startswith(("tests/", "solution/", "app/lab/")) or "/vendor/autowfbench/" in relative:
            raise AssertionError(f"Private tree in public image: {relative}")
        if not member.isfile():
            continue
        count += 1
        stream, tail = archive.extractfile(member), b""
        while chunk := stream.read(1024 * 1024):
            content = tail + chunk
            if any(secret in content for secret in secrets):
                raise AssertionError(f"Private bytes in public image: {relative}")
            tail = content[-max(map(len, secrets)) :]
    return count


class CheckoutHarborPackageTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def test_calibration_dispatch_is_stubbed_reserved_and_preserves_original_evidence(self):
        import copy
        import importlib

        from sapi_config_lab.coordinate.native_evaluation import evaluate_record
        from sapi_config_lab.coordinate.provenance import source_manifest
        from sapi_config_lab.evidence import digest

        record = self.root / "calibration-record"
        archive_path = ROOT / "evidence/migration-01-baseline/historical/hosted-before-native-fields.tar.gz"
        with tarfile.open(archive_path) as archive:
            for entry in archive.getmembers():
                if entry.isfile():
                    target = record / entry.name.removeprefix("hosted-before-native-fields/")
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(archive.extractfile(entry).read())
        options = {
            "mode": "stub",
            "native_mode": "control",
            "deadline_seconds": 120,
            "judge_mode": "demo",
            "judge_model": None,
            "selected_case": None,
        }
        (record / "evidence/submission.yaml").write_bytes((DIRECTORY / "solution/config.yaml").read_bytes())
        native = {
            "schema": "sapi-lab-native-task/v1",
            "name": "checkout-recovery",
            "sources": source_manifest(),
            "options": options,
            "options_sha256": digest(options),
            "submission_sha256": hashlib.sha256((record / "evidence/submission.yaml").read_bytes()).hexdigest(),
        }
        (record / "native-task.json").write_text(json.dumps(native))
        from tests.support.checkout_evaluation import SCORING

        evaluator = importlib.import_module("checkout_task.evaluation.evaluator").evaluate
        scoring = SCORING
        reply = read(record / "evaluation/judge-reply.json")
        before = {
            path.relative_to(record).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in record.rglob("*")
            if path.is_file()
        }
        output = self.root / "calibrated"

        def judge(contract, run_log, artifacts):
            ledger = read(output / "ledger.json")
            self.assertEqual(ledger["events"][0]["status"], "unknown")
            saved = copy.deepcopy(reply)
            saved["judgement"]["run_id"] = run_log["run_id"]
            for criterion in saved["judgement"]["criteria"]:
                criterion["evidence_refs"] = [
                    "candidate-calibration" if reference == "candidate-final" else reference
                    for reference in criterion["evidence_refs"]
                ]
            saved["provenance"].update(
                mode="codex",
                model=contract.judge_model,
                run_log_digest=digest(run_log),
                response_digest=digest(saved["judgement"]),
            )
            return saved

        def invoke(task, request, sources):
            return evaluate_record(task, evaluator, request)

        with (
            patch.object(scoring, "judge", side_effect=judge) as dispatch,
            patch("sapi_config_lab.coordinate.native_evaluation.invoke", side_effect=invoke),
        ):
            evaluate_main(
                [
                    "--record",
                    str(record),
                    "--output",
                    str(output),
                    "--calibration",
                    "supported-good",
                    "--judge-model",
                    "stub-judge",
                    "--dispatch-judge",
                ]
            )
        dispatch.assert_called_once()
        self.assertEqual(
            read(output / "evaluation/evaluation.json")["status"],
            "complete",
            read(output / "evaluation/evaluation.json"),
        )
        self.assertTrue((output / "comparison.json").is_file())
        self.assertTrue((output / "fixture.json").is_file())
        self.assertEqual(read(output / "evaluation/task-contract.json")["judge"]["model"], "stub-judge")
        after = {
            path.relative_to(record).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in record.rglob("*")
            if path.is_file()
        }
        self.assertEqual(before, after)
        native["options_sha256"] = "0" * 64
        (record / "native-task.json").write_text(json.dumps(native))
        refused = self.root / "refused"
        with patch.object(scoring, "judge") as dispatch, self.assertRaisesRegex(ValueError, "identity differs"):
            evaluate_main(
                [
                    "--record",
                    str(record),
                    "--output",
                    str(refused),
                    "--calibration",
                    "supported-good",
                    "--judge-model",
                    "stub-judge",
                    "--dispatch-judge",
                ]
            )
        dispatch.assert_not_called()
        self.assertFalse((refused / "ledger.json").exists())


@unittest.skipUnless(
    os.environ.get("SAPI_RUN_DOCKER_TESTS") == "1", "Set SAPI_RUN_DOCKER_TESTS=1 for real Harbor checkout"
)
class DockerCheckoutHarborTests(unittest.TestCase):
    def docker(self, *arguments):
        return subprocess.check_output(["docker", *arguments], text=True, timeout=180)

    def containers(self, trial):
        rows = self.docker("ps", "-a", "--format", "{{.ID}} {{.Names}}").splitlines()
        ids = [row.split()[0] for row in rows if trial.lower() in row.lower()]
        return json.loads(self.docker("inspect", *ids)) if ids else []

    def cleanup(self, inspection):
        if not inspection:
            return
        projects = {item["Config"]["Labels"]["com.docker.compose.project"] for item in inspection}
        self.docker("rm", "-f", *(item["Id"] for item in inspection))
        for project in projects:
            networks = self.docker(
                "network", "ls", "-q", "--filter", "label=com.docker.compose.project=" + project
            ).split()
            if networks:
                self.docker("network", "rm", *networks)
            volumes = self.docker(
                "volume", "ls", "-q", "--filter", "label=com.docker.compose.project=" + project
            ).split()
            for volume in volumes:
                info = json.loads(self.docker("volume", "inspect", volume))[0]
                if info["Labels"].get("com.docker.compose.volume") == "checkout-credentials":
                    self.docker("volume", "rm", volume)

    def inspect_containers(self, run, agent, inspection, reference):
        self.assertEqual(len(inspection), 3)
        public = next(item for item in inspection if "__verifier__" not in item["Name"])
        private = [item for item in inspection if "__verifier__" in item["Name"]]
        verifier = next(item for item in private if item["Config"]["Labels"]["com.docker.compose.service"] == "main")
        simulator = next(
            item for item in private if item["Config"]["Labels"]["com.docker.compose.service"] == "simulator"
        )
        self.assertNotEqual(public["Image"], verifier["Image"])
        self.assertFalse(public["State"]["Running"])
        public_networks = set(public["NetworkSettings"]["Networks"])
        for item in private:
            self.assertFalse(public_networks & set(item["NetworkSettings"]["Networks"]))
            self.assertFalse(any(item["NetworkSettings"]["Ports"].values()))
        self.assertEqual(
            {mount["Destination"] for mount in public["Mounts"]}, {"/logs/agent", "/logs/verifier", "/logs/artifacts"}
        )
        self.assertEqual({mount["Destination"] for mount in verifier["Mounts"]}, {"/logs/verifier", "/run/checkout"})
        self.assertEqual({mount["Destination"] for mount in simulator["Mounts"]}, {"/run/checkout", "/logs/verifier"})
        public_metadata = json.dumps({key: public[key] for key in ("Config", "Mounts", "HostConfig")}).encode()
        for secret in (*PRIVATE_MARKERS, reference):
            self.assertNotIn(secret, public_metadata)
        for forbidden in ("/tests/payload", "credentials.json", "AWB_", "AUTOWFBENCH_ROOT"):
            self.assertNotIn(forbidden.encode(), public_metadata)
        finished, started = public["State"]["FinishedAt"], verifier["State"]["StartedAt"]
        phase = {
            "author_finished": finished,
            "verifier_started": started,
            "available": not finished.startswith("0001-"),
        }
        if phase["available"]:
            self.assertLessEqual(datetime.fromisoformat(finished), datetime.fromisoformat(started))
        (run / (agent + "-phase.json")).write_text(json.dumps(phase, indent=2))
        copied = run / (agent + "-private-source")
        self.docker("cp", verifier["Id"] + ":/tests/payload/vendor/autowfbench", str(copied))
        identity = PinnedSource(DIRECTORY / "provenance/autowfbench-source.json", copied).verify()
        (run / (agent + "-source-proof.json")).write_text(json.dumps(identity, indent=2))
        if agent == "nop":
            image_tar = run / "public-image.tar"
            self.docker("image", "save", "-o", str(image_tar), public["Image"])
            layers = []
            with tarfile.open(image_tar) as archive:
                manifest = json.load(archive.extractfile("manifest.json"))[0]
                for name in manifest["Layers"]:
                    with tarfile.open(fileobj=archive.extractfile(name), mode="r|*") as layer:
                        count = inspect_public(layer, reference)
                    layers.append({"path": name, "files": count})
            filesystem = run / "public-filesystem.tar"
            self.docker("export", "-o", str(filesystem), public["Id"])
            with tarfile.open(filesystem) as archive:
                count = inspect_public(archive, reference)
            (run / "public-inspection.json").write_text(
                json.dumps({"image": public["Image"], "layers": layers, "filesystem_files": count}, indent=2)
            )


@unittest.skipUnless(os.environ.get("SAPI_RUN_DOCKER_TESTS") == "1", "Set SAPI_RUN_DOCKER_TESTS=1 for fault matrix")
class DockerCheckoutFaultTests(DockerCheckoutHarborTests):
    # Normal oracle/nop is exercised by the parent class once, not once per fault.
    test_oracle_and_nop_use_fresh_private_worlds_and_preserve_reference_reward = None

    def test_native_harbor_fault_matrix(self):
        import shutil
        import signal
        import time

        run = ROOT / "reports/migration-06" / ("faults-" + uuid.uuid4().hex[:10])
        run.mkdir(parents=True)
        template = run / "template/checkout-recovery"
        shutil.copytree(ROOT / "tasks/checkout-recovery", template)
        from tests.test_checkout_isolation import DOCKER_CANDIDATE_PROBE, DOCKER_REDIRECT_PROBE

        shutil.copyfile(ROOT / "tests/checkout_fault_probe.py", template / "tests/fault_probe.py")
        (template / "tests/isolation_preflight.py").write_text(DOCKER_CANDIDATE_PROBE + "\n" + DOCKER_REDIRECT_PROBE)
        with (template / "tests/Dockerfile").open("a") as handle:
            handle.write("\nCOPY fault_probe.py isolation_preflight.py /tests/\n")
        matrix = []
        modes = (
            "deadline",
            "worker-death",
            "hard-timeout",
            "simulator-death",
            "cancellation",
            "finish-race",
            "terminal-race",
            "evaluator-failure",
            "isolation-freshness",
            "fresh-retry",
        )
        selected = set(os.environ.get("SAPI_CHECKOUT_FAULTS", ",".join(modes)).split(","))
        self.assertTrue(selected <= set(modes))
        for mode in modes:
            if mode not in selected:
                continue
            with self.subTest(fault=mode):
                task = run / mode / "checkout-recovery"
                shutil.copytree(template, task)
                probe_mode = "isolation-freshness" if mode == "fresh-retry" else mode
                (task / "tests/test.sh").write_text(f"#!/bin/sh\nexec python3 /tests/fault_probe.py {probe_mode}\n")
                compose_path = task / "tests/docker-compose.yaml"
                compose = yaml.safe_load(compose_path.read_text())
                compose["services"]["simulator"]["command"] = [
                    "python3",
                    "/tests/fault_probe.py",
                    "simulator",
                    probe_mode,
                ]
                compose_path.write_text(yaml.safe_dump(compose))
                if mode == "hard-timeout":
                    config = task / "task.toml"
                    config.write_text(config.read_text().replace("timeout_sec = 1800", "timeout_sec = 8"))
                command = [
                    str(ROOT / ".venv/bin/harbor"),
                    "run",
                    "--path",
                    str(task),
                    "--agent",
                    "oracle",
                    "--jobs-dir",
                    str(run / "jobs"),
                    "--job-name",
                    mode,
                    "--n-concurrent",
                    "1",
                    "--max-retries",
                    "0",
                ]
                (run / (mode + "-command.json")).write_text(json.dumps(command))
                inspection = []
                process = None
                try:
                    with (run / (mode + ".log")).open("w") as log:
                        process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT)
                        limit = time.monotonic() + 600
                        injected = False
                        while process.poll() is None:
                            self.assertLess(time.monotonic(), limit, f"Fault timed out: {mode}")
                            phases = list((run / "jobs" / mode).glob("*/verifier/phase.json"))
                            if phases and not injected:
                                trial = phases[0].parent.parent
                                inspection = self.containers(trial.name)
                                (run / (mode + "-inspect.json")).write_text(json.dumps(inspection, indent=2))
                                if mode == "simulator-death":
                                    simulator = next(
                                        item
                                        for item in inspection
                                        if item["Config"]["Labels"]["com.docker.compose.service"] == "simulator"
                                    )
                                    self.docker("kill", simulator["Id"])
                                if mode in {"simulator-death", "worker-death"}:
                                    (phases[0].parent / "release").touch()
                                if mode == "cancellation":
                                    process.send_signal(signal.SIGINT)
                                injected = True
                            time.sleep(0.1)
                        process.wait(timeout=30)
                    trials = [path for path in (run / "jobs" / mode).iterdir() if path.is_dir()]
                    self.assertEqual(len(trials), 1)
                    trial = trials[0]
                    verifier = trial / "verifier"
                    remaining = self.containers(trial.name)
                    self.assertEqual(remaining, [], "Harbor must clean services after faults")
                    evidence = verifier / "evidence"
                    journals = sorted((verifier / "world").glob("*.json"))
                    rows = [read(path) for path in journals]
                    self.assertTrue(rows, "Incremental world observations must survive")
                    states = [
                        event["state"]
                        for row in rows
                        for event in row.get("events", [])
                        if event.get("kind") == "tool_dispatch"
                    ]
                    self.assertIn("reserved", states)
                    self.assertIn("received", states)
                    result = read(trial / "result.json") if (trial / "result.json").exists() else None
                    self.assertFalse((verifier / "reward.txt").exists(), "Fault cannot manufacture reward")
                    if mode in {"worker-death", "hard-timeout", "cancellation"}:
                        self.assertFalse((evidence / "trial.json").exists(), "Killed worker has no final record")
                    elif mode == "evaluator-failure":
                        before = read(verifier / "before-evaluator.json")
                        self.assertEqual(
                            before,
                            {
                                p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                                for p in evidence.iterdir()
                                if p.is_file()
                            },
                        )
                        self.assertTrue(read(evidence / "trial.json")["terminal_completion"])
                        from sapi_config_lab.evaluate.records import NOT_EVALUATED, trial_result

                        self.assertEqual(trial_result({"verdict_path": str(verifier / "result.json")}), NOT_EVALUATED)
                    else:
                        terminal = read(evidence / "trial.json")
                        self.assertFalse(terminal["terminal_completion"])
                        self.assertIsNone(terminal["native_execution"])
                        self.assertTrue(read(verifier / "probe-proof.json")["immutable_after_late_success"])
                        if mode == "simulator-death":
                            self.assertEqual(terminal["termination_reason"], "unknown")
                            self.assertTrue(terminal["evidence_errors"])
                            self.assertFalse((evidence / "environment-evidence.json").exists())
                        if mode in {"deadline", "terminal-race"}:
                            self.assertEqual(terminal["termination_reason"], "timeout")
                        if mode in {"finish-race", "terminal-race"}:
                            self.assertEqual(len(list((verifier / "world").glob("*-terminal-window.json"))), 1)
                            self.assertEqual(len(list((verifier / "world").glob("*-snapshot.json"))), 1)
                        if mode == "finish-race":
                            self.assertLess(terminal["duration_seconds"], 0.5)
                    matrix.append(
                        {
                            "fault": mode,
                            "trial": str(trial),
                            "returncode": process.returncode,
                            "exception": None if result is None else result.get("exception_info"),
                            "partial_rows": len(rows),
                            "managed_services_remaining": 0,
                            "evidence_hashes": {
                                p.relative_to(verifier).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                                for p in verifier.rglob("*")
                                if p.is_file()
                            },
                        }
                    )
                    (run / "matrix.json").write_text(json.dumps(matrix, indent=2))
                finally:
                    if process is not None and process.poll() is None:
                        process.send_signal(signal.SIGINT)
                        try:
                            process.wait(timeout=30)
                        except subprocess.TimeoutExpired:
                            process.kill()
                            process.wait(timeout=10)
                    remaining = []
                    for trial in (run / "jobs" / mode).glob("checkout-recovery*"):
                        remaining.extend(self.containers(trial.name))
                    self.cleanup(remaining)
        if not {"isolation-freshness", "fresh-retry"} <= selected:
            return
        first = read(Path(matrix[-2]["trial"]) / "verifier/isolation.json")
        second = read(Path(matrix[-1]["trial"]) / "verifier/isolation.json")
        for key in ("admin_token", "tool_token"):
            self.assertNotEqual(first["credential_hashes"][key], second["credential_hashes"][key])

        fresh = [read(Path(row["trial"]) / "verifier/freshness.json") for row in matrix[-2:]]
        self.assertEqual(fresh[0]["initial_sha256"], fresh[1]["initial_sha256"])
        self.assertNotEqual(fresh[0]["run_id"], fresh[1]["run_id"])
