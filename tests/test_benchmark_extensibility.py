"""Directory-only extension through public discovery, Harbor and offline evaluation."""

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from types import ModuleType
import unittest
import uuid

from sapi_config_lab.benchmark import discover_benchmarks, load_benchmark
from sapi_config_lab.benchmark_loading import freeze_identity, load_entrypoints
from sapi_config_lab.contracts import CompileOptions
from sapi_config_lab.coordinate.backend import N8nBackend
from sapi_config_lab.paths import workspace_root
from sapi_config_lab.profile import read_bindings

ROOT = workspace_root()
FIXTURE = ROOT / "tests/support/extensibility"


def hashes(directory):
    return {
        path.relative_to(directory).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(directory.rglob("*"))
        if path.is_file()
    }


def sources():
    paths = subprocess.check_output(["git", "ls-files", "-z"], cwd=ROOT).decode().split("\0")
    return {
        relative: hashlib.sha256((ROOT / relative).read_bytes()).hexdigest()
        for relative in paths
        if relative and (ROOT / relative).is_file()
    } | {
        "tests/test_benchmark_extensibility.py": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        **{"tests/support/extensibility/" + relative: digest for relative, digest in hashes(FIXTURE).items()},
    }


class DirectoryExtensionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.package = self.root / "99-beacon-calibration"
        shutil.copytree(FIXTURE, self.package)

    def selected(self):
        descriptor = load_benchmark(self.root, self.package)
        return load_entrypoints(descriptor, freeze_identity(descriptor, {}))

    def manifest(self, change):
        path = self.package / "scenario.json"
        value = json.loads(path.read_text())
        change(value)
        path.write_text(json.dumps(value))

    def test_new_schema_and_independent_world_expectation_reject_corruption(self):
        selected = self.selected()
        plan = selected.plan(self.package / "config.yaml", {})
        self.assertEqual(plan["entries"][0]["config"]["workflow"]["steps"][0]["uses"], "beacon.sample")
        N8nBackend(operation_source=(self.package / "operations.js").read_text()).compile(
            plan["entries"][0]["config"],
            read_bindings(self.package / "bindings.yaml"),
            CompileOptions(operation_url="http://beacon:8000/tools", bound_deadline=True),
        )
        evidence = self.root / "evidence"
        native = evidence / "cases/calibration/case.json"
        native.parent.mkdir(parents=True)
        expected = {"calibration": 18, "station": "north", "seal": "private-unique-seal"}
        record = {"status": "success", "output": expected}
        native.write_text(json.dumps(record))
        (evidence / "beacon-completion.json").write_text(json.dumps(record))
        world = {
            "initial": {"bearing": 350, "offset": 28, "station": "north", "seal": expected["seal"]},
            "calls": [
                {
                    "request": {"operation": "beacon.sample", "arguments": {"station": "north"}},
                    "response": {"angle": 18, "station": "north", "seal": expected["seal"]},
                }
            ],
        }
        (evidence / "beacon-world.json").write_text(json.dumps(world))
        digest = hashlib.sha256(native.read_bytes()).hexdigest()
        (evidence / "observation.json").write_text(
            json.dumps({"entries": [{"name": "calibration", "files": {"case.json": digest}}]})
        )
        options = {"evaluation": str(self.root / "evaluation")}
        self.assertTrue(selected.evaluate(evidence, options)["acceptance"])
        world["initial"]["offset"] += 1
        (evidence / "beacon-world.json").write_text(json.dumps(world))
        self.assertFalse(selected.evaluate(evidence, options)["acceptance"])
        world["initial"]["offset"] -= 1
        (evidence / "beacon-world.json").write_text(json.dumps(world))
        record["output"]["calibration"] += 1
        (evidence / "beacon-completion.json").write_text(json.dumps(record))
        self.assertFalse(selected.evaluate(evidence, options)["acceptance"])

    def test_duplicate_ids_path_escapes_and_missing_private_dependencies_fail(self):
        shutil.copytree(self.package, self.root / "98-duplicate")
        with self.assertRaisesRegex(ValueError, "Duplicate benchmark id"):
            discover_benchmarks(self.root)
        self.manifest(lambda meta: meta["trusted"].append("../private.py"))
        with self.assertRaisesRegex(ValueError, "path"):
            load_benchmark(self.root, self.package)
        self.manifest(lambda meta: meta["trusted"].remove("../private.py"))
        self.manifest(
            lambda meta: meta.update(
                dependencies={"missing": {"path": "_shared/missing", "public": [], "trusted": ["private.py"]}}
            )
        )
        with self.assertRaisesRegex(ValueError, "Missing.*dependency"):
            load_benchmark(self.root, self.package)

    def test_cross_package_module_cache_collision_fails_closed(self):
        first = self.selected()
        second_path = self.root / "98-second"
        shutil.copytree(self.package, second_path)
        meta = json.loads((second_path / "scenario.json").read_text())
        meta["id"] = "second-beacon"
        (second_path / "scenario.json").write_text(json.dumps(meta))
        descriptor = load_benchmark(self.root, second_path)
        second = load_entrypoints(descriptor, freeze_identity(descriptor, {}))
        self.assertNotEqual(first.package, second.package)
        name = first.evaluate.__module__
        original = sys.modules[name]
        sys.modules[name] = sys.modules[second.evaluate.__module__]
        try:
            with self.assertRaisesRegex(ValueError, "Conflicting loaded module identity"):
                self.selected()
        finally:
            sys.modules[name] = original
        sys.modules[name] = ModuleType(name)
        try:
            with self.assertRaisesRegex(ValueError, "Conflicting loaded module identity"):
                self.selected()
        finally:
            sys.modules[name] = original


@unittest.skipUnless(os.environ.get("SAPI_RUN_DOCKER_TESTS") == "1", "opt-in real pinned Harbor Docker proof")
class DockerDirectoryExtensionTests(unittest.TestCase):
    def test_directory_only_public_flow_oracle_nop_and_offline_evaluation(self):
        run = ROOT / "reports/migration-20" / ("extension-" + uuid.uuid4().hex[:10])
        run.mkdir(parents=True)
        baseline = sources()
        (run / "source-before.json").write_text(json.dumps(baseline, indent=2))
        directory = ROOT / "benchmarks/99-beacon-calibration"
        self.assertFalse(directory.exists())
        shutil.copytree(FIXTURE, directory)
        self.addCleanup(shutil.rmtree, directory)
        commands = []

        def command(arguments, name, timeout=120, expected_exit=0):
            commands.append(arguments)
            (run / "commands.json").write_text(json.dumps(commands, indent=2))
            with (run / (name + ".log")).open("w") as log:
                result = subprocess.run(
                    arguments,
                    cwd=ROOT,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    timeout=timeout,
                    env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
                )
            self.assertEqual(result.returncode, expected_exit, (run / (name + ".log")).read_text())

        cli = [str(ROOT / ".venv/bin/sapi-lab")]
        command([*cli, "benchmarks"], "discover")
        rows = json.loads((run / "discover.log").read_text())
        self.assertIn("beacon-calibration", {row["id"] for row in rows})
        command([*cli, "package-tasks", str(run / "tasks"), "--scenario", "beacon-calibration"], "package")
        task = run / "tasks/beacon-calibration"
        package_hashes = hashes(task)
        self.assertNotIn("beacon-private-tool", (task / "environment/Dockerfile").read_text())
        seals = []
        for agent in ("oracle", "nop"):
            command(
                [
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
                ],
                agent,
                timeout=2400,
            )
            results = list((run / "jobs" / agent).glob("*/result.json"))
            self.assertEqual(len(results), 1)
            native = json.loads(results[0].read_text())
            self.assertIsNone(native["exception_info"])
            self.assertEqual(native["verifier_result"]["rewards"], {"reward": int(agent == "oracle")})
            record = results[0].parent / "verifier"
            verdict = json.loads((record / "result.json").read_text())
            self.assertIs(verdict["acceptance"], agent == "oracle")
            self.assertIsNone(verdict["quality"])
            world = json.loads((record / "evidence/beacon-world.json").read_text())
            seals.append(world["initial"]["seal"])
            self.assertEqual(len(world["calls"]), int(agent == "oracle"))
            raw = hashes(record)
            command(
                [*cli, "evaluate", "--record", str(record), "--output", str(run / (agent + "-reevaluation"))],
                agent + "-reevaluation",
                expected_exit=int(agent == "nop"),
            )
            self.assertEqual(raw, hashes(record))
            self.assertEqual(verdict, json.loads((run / (agent + "-reevaluation/result.json")).read_text()))
            if agent == "oracle":
                self.assertTrue((record / "evidence/cases/calibration/workflow.json").is_file())
                corrupt = run / "corrupt"
                shutil.copytree(record, corrupt)
                completion = corrupt / "evidence/beacon-completion.json"
                value = json.loads(completion.read_text())
                value["output"]["calibration"] = (value["output"]["calibration"] + 1) % 360
                completion.write_text(json.dumps(value))
                command(
                    [*cli, "evaluate", "--record", str(corrupt), "--output", str(run / "corruption-evaluation")],
                    "corruption",
                    expected_exit=1,
                )
                self.assertFalse(json.loads((run / "corruption-evaluation/result.json").read_text())["acceptance"])
            self.assertEqual(package_hashes, hashes(task))
        self.assertEqual(len(set(seals)), 2)
        after = sources()
        (run / "source-after.json").write_text(json.dumps(after, indent=2))
        self.assertEqual(baseline, after)
        (run / "proof.json").write_text(
            json.dumps(
                {
                    "oracle": True,
                    "nop": False,
                    "corruption": False,
                    "offline_evidence_unchanged": True,
                    "source_unchanged": True,
                    "new_operations": ["beacon.sample", "beacon.complete"],
                },
                indent=2,
            )
        )
