"""Checkout's positive Harbor package and opt-in fresh-world oracle/nop proof."""

from dataclasses import replace
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
from unittest.mock import Mock, patch
import uuid

import yaml

from sapi_config_lab.benchmark import load_benchmark
from sapi_config_lab.coordinate.evaluation import hosted_evaluation, reevaluate_benchmark
from sapi_config_lab.coordinate.evaluation import main as evaluate_main
from sapi_config_lab.coordinate.live import validate_packages
from sapi_config_lab.coordinate.packages import stage_tasks
from sapi_config_lab.coordinate.providers import EVALUATORS
from sapi_config_lab.coordinate.runs import Run, load_trials
from sapi_config_lab.coordinate.scenarios import SCENARIOS
from sapi_config_lab.harbor_integration.tasks import validate_config
from sapi_config_lab.paths import workspace_root
from sapi_config_lab.pinned_source import PinnedSource

ROOT = workspace_root()
DIRECTORY = ROOT / "benchmarks/10-checkout-recovery"
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
        stage_tasks(self.tasks, scenarios=("checkout-recovery",))
        self.task = self.tasks / "checkout-recovery"

    def test_manifest_and_native_contexts_keep_the_full_pin_private(self):
        benchmark = load_benchmark(ROOT / "benchmarks", DIRECTORY)
        public = {item.destination for item in benchmark.public}
        self.assertEqual(public, {"instruction.md", "authoring-notes.md", "bindings.yaml"})
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
        self.assertEqual(pin.read_bytes(), (ROOT / "provenance/autowfbench-source.json").read_bytes())
        self.assertEqual((self.task / "solution/config.yaml").read_bytes(), (DIRECTORY / "config.yaml").read_bytes())
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
        for private in (*PRIVATE_MARKERS, (DIRECTORY / "config.yaml").read_bytes()):
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
        with patch("sapi_config_lab.coordinate.runs.TrialHost", side_effect=AssertionError("legacy TrialHost")):
            with run.hosted("oracle", self.tasks, self.root / "records", None) as (tasks, hosted):
                self.assertEqual(tasks, self.tasks)
                self.assertEqual(hosted, {})
        self.assertFalse((self.root / "records").exists())
        self.assertFalse((self.root / "hosted").exists())

    def test_live_compatibility_is_explicit_and_preserves_judge_identity_without_dispatch(self):
        scenario = SCENARIOS["checkout-recovery"]
        selection = {
            scenario.name: {"path": scenario.config, "sha256": hashlib.sha256(scenario.config.read_bytes()).hexdigest()}
        }
        legacy = self.root / "legacy"
        stage_tasks(legacy, scenarios=(scenario.name,), image="frozen:live", legacy_hosted=True)
        self.assertFalse((legacy / scenario.name / "tests/benchmark.json").exists())
        self.assertTrue((legacy / scenario.name / "tests/environment.json").exists())
        validate_packages(legacy, selection, "frozen:live", legacy_hosted=True)
        with self.assertRaises((OSError, ValueError)):
            validate_packages(legacy, selection, "frozen:live")
        (self.task / "tests/benchmark.json").unlink()
        with self.assertRaises(OSError):
            validate_packages(self.tasks, selection, "frozen:live")
        with self.assertRaises(OSError):
            validate_packages(self.tasks, selection, "frozen:live", legacy_hosted=True)
        prepare = Mock(return_value=lambda path: {"execution": True, "acceptance": True, "quality": None})
        evaluator = replace(EVALUATORS[scenario.evaluator], prepare=prepare)
        with patch.dict(EVALUATORS, {scenario.evaluator: evaluator}):
            hosted_evaluation((scenario.name,), "judge-pinned", legacy_hosted=True)
        prepare.assert_called_once_with(scenario, "judge-pinned")

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
        with self.assertRaisesRegex(ValueError, "experiment integration"):
            evaluate_main(["--record", str(record), "--output", str(self.root / "paid"), "--dispatch-judge"])


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
        self.assertEqual({mount["Destination"] for mount in simulator["Mounts"]}, {"/run/checkout"})
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
        stage_tasks(run / "tasks", scenarios=("checkout-recovery",))
        task = run / "tasks/checkout-recovery"
        before = {
            p.relative_to(task).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in task.rglob("*")
            if p.is_file()
        }
        reference = (DIRECTORY / "config.yaml").read_bytes()
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
        self.assertEqual((DIRECTORY / "config.yaml").read_bytes(), reference)
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
