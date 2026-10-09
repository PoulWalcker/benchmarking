"""Checkout's positive Harbor package and opt-in fresh-world oracle/nop proof."""

from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch
import uuid

import yaml

from sapi_config_lab.benchmark import load_benchmark
from sapi_config_lab.coordinate.benchmark_discovery import select_benchmarks
from sapi_config_lab.coordinate.evaluation import main as evaluate_main
from sapi_config_lab.coordinate.evaluation import reevaluate_benchmark
from sapi_config_lab.coordinate.live import validate_packages
from sapi_config_lab.coordinate.packages import stage_tasks
from sapi_config_lab.coordinate.runs import Run, load_trials
from sapi_config_lab.harbor_integration.tasks import validate_config
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
        self.tasks = self.root / "tasks"
        stage_tasks(self.tasks, root=ROOT, benchmarks=select_benchmarks(ROOT / "tasks", ("checkout-recovery",)))
        self.task = self.tasks / "checkout-recovery"

    def test_manifest_and_native_contexts_keep_the_full_pin_private(self):
        benchmark = load_benchmark(ROOT / "tasks", DIRECTORY)
        public = {item.destination for item in benchmark.public}
        self.assertEqual(public, {"instruction.md", "authoring-notes.md", "bindings.yaml", "authoring-prompt.txt"})
        metadata = read(self.task / "tests/benchmark.json")
        expected = {item.destination for item in (*benchmark.public, *benchmark.trusted)}
        self.assertEqual(set(metadata["payload_files"]), expected)
        self.assertEqual(metadata["identity"]["benchmark_id"], "checkout-recovery")
        for relative, expected_hash in metadata["payload_files"].items():
            self.assertEqual(
                hashlib.sha256((self.task / "tests/payload" / relative).read_bytes()).hexdigest(), expected_hash
            )
        staged_public = self.task / "environment/payload"
        self.assertEqual(
            {p.relative_to(staged_public).as_posix() for p in staged_public.rglob("*") if p.is_file()}, public
        )
        pin = self.task / "tests/payload/provenance/autowfbench-source.json"
        self.assertEqual(pin.read_bytes(), (DIRECTORY / "provenance/autowfbench-source.json").read_bytes())
        self.assertEqual(
            (self.task / "solution/config.yaml").read_bytes(), (DIRECTORY / "solution/config.yaml").read_bytes()
        )
        config = validate_config((self.task / "task.toml").read_text())
        self.assertEqual(config.verifier.environment_mode, "separate")
        self.assertFalse((self.task / "environment/docker-compose.yaml").exists())
        compose = yaml.safe_load((self.task / "tests/docker-compose.yaml").read_text())
        self.assertEqual(set(compose["services"]), {"main", "simulator"})
        self.assertTrue(compose["networks"]["verification"]["internal"])
        self.assertEqual(compose["services"]["simulator"]["build"]["context"], "${CONTEXT_DIR}")
        for name in ("environment.json", "connection.json"):
            self.assertFalse((self.task / "tests" / name).exists())
        public_bytes = b"\n".join(p.read_bytes() for p in (self.task / "environment").rglob("*") if p.is_file())
        for private in (*PRIVATE_MARKERS, (DIRECTORY / "solution/config.yaml").read_bytes()):
            self.assertNotIn(private, public_bytes)

    def test_staged_worker_and_entrypoints_import_without_legacy_host_or_evaluator(self):
        core = self.task / "tests/core"
        excluded = (
            "sapi_config_lab.execute.hosting",
            "sapi_config_lab.execute.autowfbench",
            "sapi_config_lab.evaluate.autowfbench",
            "sapi_config_lab.coordinate.providers",
            "sapi_config_lab.coordinate.hosted_worker",
            "sapi_config_lab.coordinate.scenarios",
        )
        for module in excluded:
            self.assertFalse((core / (module.replace(".", "/") + ".py")).exists())
        script = (
            "import sys,importlib\n"
            f"sys.path[:0] = {[str(core), str(self.task / 'tests')]!r}\n"
            f"for name in {excluded!r}: sys.modules[name] = None\n"
            "importlib.import_module('sapi_config_lab.coordinate.benchmark_worker')\n"
            + (self.task / "tests/check_imports.py").read_text()
        )
        result = subprocess.run([sys.executable, "-I", "-c", script], cwd=self.root, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_versioned_task_bypasses_trialhost_and_legacy_connection_material(self):
        run = Run(self.root, {}, {}, "checkout-native-test", staging=self.root)
        self.assertFalse(hasattr(run, "hosted"))
        self.assertFalse((self.task / "tests/connection.json").exists())
        self.assertFalse((self.root / "records").exists())
        self.assertFalse((self.root / "hosted").exists())

    def test_native_live_staging_pins_the_judge_and_refuses_missing_metadata(self):
        scenario = load_benchmark(ROOT / "tasks", DIRECTORY)
        selection = {
            scenario.name: {
                "path": scenario.reference.source,
                "sha256": hashlib.sha256(scenario.reference.source.read_bytes()).hexdigest(),
            }
        }
        native = self.root / "native-live"
        stage_tasks(native, root=ROOT, benchmarks=(scenario,), judge_model="judge-pinned")
        task = native / scenario.name
        metadata = read(task / "tests/benchmark.json")
        self.assertEqual(metadata["options"]["judge_mode"], "codex")
        self.assertEqual(metadata["options"]["judge_model"], "judge-pinned")
        self.assertFalse((task / "tests/environment.json").exists())
        validate_packages(native, selection, {scenario.name: scenario})
        (task / "tests/benchmark.json").unlink()
        with self.assertRaises(OSError):
            validate_packages(native, selection, {scenario.name: scenario})

    def test_offline_evaluation_requires_recorded_identity_and_never_dispatches_implicitly(self):
        record = self.root / "record"
        (record / "evidence").mkdir(parents=True)
        (record / "evaluation").mkdir()
        archive_path = ROOT / "evidence/migration-01-baseline/historical/hosted-before-native-fields.tar.gz"
        with tarfile.open(archive_path) as archive:
            prefix = "hosted-before-native-fields/"
            for entry in archive.getmembers():
                if entry.isfile():
                    relative = entry.name.removeprefix(prefix)
                    target = record / relative
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(archive.extractfile(entry).read())
        metadata = (self.task / "tests/benchmark.json").read_text()
        (record / "benchmark.json").write_text(metadata)
        before = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in (record / "evidence").iterdir()}
        result = reevaluate_benchmark(record, self.root / "plain", None)
        self.assertIsNone(result["quality"]["normalized_reward"])
        self.assertFalse((self.root / "plain/evaluation/judge-dispatch.json").exists())
        replay = reevaluate_benchmark(record, self.root / "saved", record / "evaluation/judge-reply.json")
        self.assertEqual(replay["quality"]["normalized_reward"], 0.732)
        after = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in (record / "evidence").iterdir()}
        self.assertEqual(before, after)
        changed = json.loads(metadata)
        changed["identity"]["sha256"] = "0" * 64
        (record / "benchmark.json").write_text(json.dumps(changed))
        with self.assertRaisesRegex(ValueError, "identity differs"):
            reevaluate_benchmark(record, self.root / "changed", None)
        with self.assertRaisesRegex(ValueError, "Historical evaluator execution is deferred"):
            evaluate_main(["--record", str(record), "--output", str(self.root / "paid"), "--dispatch-judge"])

    def test_calibration_dispatch_is_stubbed_reserved_and_preserves_original_evidence(self):
        import copy
        import importlib

        from sapi_config_lab.benchmark_loading import freeze_identity, load_entrypoints
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
        metadata = read(self.task / "tests/benchmark.json")
        (record / "evidence/submission.yaml").write_bytes((DIRECTORY / "solution/config.yaml").read_bytes())
        native = {
            "schema": "sapi-lab-native-task/v1",
            "name": "checkout-recovery",
            "sources": source_manifest(),
            "options": metadata["options"],
            "options_sha256": digest(metadata["options"]),
            "submission_sha256": hashlib.sha256((record / "evidence/submission.yaml").read_bytes()).hexdigest(),
        }
        (record / "native-task.json").write_text(json.dumps(native))
        benchmark = load_benchmark(ROOT / "tasks", DIRECTORY)
        identity = freeze_identity(benchmark, metadata["options"])
        evaluator = load_entrypoints(benchmark, identity).evaluate
        scoring = importlib.import_module(evaluator.__module__.rsplit(".", 1)[0] + ".scoring")
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

    def test_oracle_and_nop_use_fresh_private_worlds_and_preserve_reference_reward(self):
        run = ROOT / "reports/migration-05" / ("docker-" + uuid.uuid4().hex[:10])
        run.mkdir(parents=True)
        stage_tasks(run / "tasks", root=ROOT, benchmarks=select_benchmarks(ROOT / "tasks", ("checkout-recovery",)))
        task = run / "tasks/checkout-recovery"
        before = {
            p.relative_to(task).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in task.rglob("*")
            if p.is_file()
        }
        reference = (DIRECTORY / "solution/config.yaml").read_bytes()
        observed, initial, worlds = [], [], []
        for agent in ("nop", "oracle"):
            command = [
                str(ROOT / ".venv/bin/harbor"),
                "run",
                "--path",
                str(task),
                "--agent",
                agent,
                "--jobs-dir",
                str(run / "jobs"),
                "--job-name",
                agent,
                "--n-concurrent",
                "1",
                "--max-retries",
                "0",
                "--no-delete",
                "--ek",
                "keep_containers=true",
            ]
            (run / (agent + "-command.json")).write_text(json.dumps(command))
            inspection = []
            try:
                with (run / (agent + ".log")).open("w") as log:
                    result = subprocess.run(
                        command,
                        env={**os.environ, "MIGRATION_CREDENTIAL_SENTINEL": PRIVATE_MARKERS[-1].decode()},
                        stdout=log,
                        stderr=subprocess.STDOUT,
                        timeout=2400,
                    )
                paths = list((run / "jobs" / agent).glob("*/result.json"))
                if paths:
                    inspection = self.containers(paths[0].parent.name)
                    (run / (agent + "-inspect.json")).write_text(json.dumps(inspection, indent=2))
                self.assertEqual(result.returncode, 0, (run / (agent + ".log")).read_text())
                self.assertEqual(len(paths), 1)
                trial = paths[0].parent
                recorded = read(paths[0])
                verdict = load_trials(run / "jobs" / agent)[0]
                self.assertIs(verdict["result"]["acceptance"], agent == "oracle")
                evidence = trial / "verifier/evidence"
                terminal = read(evidence / "trial.json")
                environment = read(evidence / "environment-evidence.json")
                observed.append(terminal["run_id"])
                initial.append(environment["initial"])
                self.assertEqual(environment["tool_calls"], 5 if agent == "oracle" else 0)
                self.assertEqual(terminal["terminal_completion"], agent == "oracle")
                self.assertFalse((task / "tests/connection.json").exists())
                self.assertFalse((run / "environments").exists())
                if agent == "oracle":
                    self.assertIsNone(recorded["exception_info"])
                    self.assertEqual(recorded["verifier_result"]["rewards"], {"reward": 0.732})
                    self.assertIs(terminal["native_execution"], True)
                    self.assertTrue(all(environment["checks"].values()))
                    self.assertEqual((trial / "verifier/evaluation/reward.txt").read_text(), "0.732\n")
                    with tarfile.open(
                        ROOT / "evidence/migration-01-baseline/historical/hosted-before-native-fields.tar.gz"
                    ) as archive:
                        baseline = json.load(archive.extractfile("hosted-before-native-fields/evidence/trial.json"))
                    self.assertEqual(terminal["submission"]["artifacts"], baseline["submission"]["artifacts"])
                else:
                    self.assertEqual(recorded["exception_info"]["exception_type"], "RewardFileNotFoundError")
                    self.assertIsNone((recorded.get("verifier_result") or {}).get("rewards"))
                    self.assertIsNone(verdict["result"]["quality"]["score_0_10"])
                    self.assertIsNone(verdict["result"]["quality"]["normalized_reward"])
                    self.assertFalse((trial / "verifier/reward.txt").exists())
                self.inspect_containers(run, agent, inspection, reference)
                worlds.extend(
                    item["Id"]
                    for item in inspection
                    if item["Config"]["Labels"]["com.docker.compose.service"] == "simulator"
                )
            finally:
                if not inspection:
                    for directory in (run / "jobs" / agent).glob("checkout-recovery*"):
                        if directory.is_dir():
                            inspection.extend(self.containers(directory.name))
                self.cleanup(inspection)
        self.assertEqual(len(set(observed)), 2)
        self.assertEqual(len(set(worlds)), 2)
        self.assertEqual(initial[0], initial[1])
        after = {
            p.relative_to(task).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in task.rglob("*")
            if p.is_file()
        }
        self.assertEqual(before, after)
        self.assertEqual((DIRECTORY / "solution/config.yaml").read_bytes(), reference)
        (run / "proof.json").write_text(
            json.dumps(
                {
                    "status": "passed",
                    "fresh_run_ids": observed,
                    "fresh_simulator_ids": worlds,
                    "same_initial_state": True,
                },
                indent=2,
            )
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
