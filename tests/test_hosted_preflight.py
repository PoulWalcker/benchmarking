"""Admission compiles explicit benchmark inputs without starting its world."""

import copy
from dataclasses import asdict
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import yaml

from sapi_config_lab.benchmark import load_benchmark, python_module
from sapi_config_lab.compile.n8n import compile_n8n
from sapi_config_lab.coordinate import benchmark_worker
from sapi_config_lab.coordinate.live import check_trials
from sapi_config_lab.paths import workspace_root
from sapi_config_lab.profile import Unsupported, read, read_bindings
from tests.support.invoice import OPERATION_SOURCE


def checkout():
    root = workspace_root() / "benchmarks"
    return load_benchmark(root, root / "10-checkout-recovery")


def model_config(benchmark, operation):
    config = copy.deepcopy(read(benchmark.reference.source))
    binding = read_bindings(benchmark.directory / benchmark.bindings)[operation]
    config["workflow"]["kind"] = "Gantt"
    config["workflow"]["steps"] = [
        {
            "id": "plan",
            "kind": "LLM",
            "uses": operation,
            "with": {key: [] if key == "evidence" else "task" for key in binding["inputs"]},
        }
    ]
    config["workflow"]["dependencies"] = []
    config["workflow"]["output"] = {"ref": "steps.plan"}
    return config


def metadata(benchmark):
    return {
        "name": benchmark.name,
        "operations": benchmark.operations,
        "bindings": benchmark.bindings,
        "budgets": asdict(benchmark.budgets),
    }


class HostedPreflightTests(unittest.TestCase):
    def test_advertised_model_operations_fail_clearly_in_stub_mode_and_compile_live(self):
        benchmark = checkout()
        bindings = read_bindings(benchmark.directory / benchmark.bindings)
        source = (benchmark.directory / benchmark.operations).read_text()
        for operation in ("incident.plan", "incident.summarize"):
            config = model_config(benchmark, operation)
            with self.subTest(operation=operation):
                with self.assertRaisesRegex(Unsupported, "No local implementation.*" + operation):
                    compile_n8n(config, bindings, operation_source=source)
                document, _ = compile_n8n(
                    config, bindings, operation_source=source, llm_mode="live", bridge_url="http://localhost:1"
                )
                self.assertTrue(any(node["type"].endswith(".httpRequest") for node in document["nodes"]))
                with self.assertRaises(TypeError):
                    compile_n8n(config, bindings)

    def test_hosted_model_candidate_admits_without_running_a_stub_or_environment(self):
        benchmark = checkout()
        with tempfile.TemporaryDirectory() as directory:
            submission = Path(directory) / "config.yaml"
            submission.write_text(yaml.safe_dump(model_config(benchmark, "incident.plan")))
            report = benchmark_worker.admit(
                metadata(benchmark), benchmark.directory, submission, {"deadline_seconds": 120}
            )
            self.assertTrue(report["passed"], report)
            trial = {"task_name": benchmark.name, "acceptance": report, "exception": None, "rewards": {"reward": 1.0}}
            selected = {benchmark.name: {"sha256": report["submission_sha256"]}}
            benchmarks = {benchmark.name: benchmark}
            check_trials([trial], selected, benchmarks=benchmarks, mode="stub", admission=True)
            with self.assertRaises(ValueError):
                check_trials([trial], selected, benchmarks=benchmarks, mode="live")
            trial["acceptance"]["submission_sha256"] = "changed"
            with self.assertRaises(ValueError):
                check_trials([trial], selected, benchmarks=benchmarks, mode="stub", admission=True)

    def test_admission_rejects_exceeded_model_budget_and_changed_deadline(self):
        benchmark = checkout()
        with tempfile.TemporaryDirectory() as directory:
            submission = Path(directory) / "config.yaml"
            config = model_config(benchmark, "incident.plan")
            submission.write_text(yaml.safe_dump(config))
            limited = metadata(benchmark)
            limited["budgets"]["runtime_model_calls"] = 0
            report = benchmark_worker.admit(limited, benchmark.directory, submission, {"deadline_seconds": 120})
            self.assertFalse(report["passed"])
            self.assertIn("Runtime cap exceeded", report["error"])
            config["execution"]["deadline_seconds"] = 121
            submission.write_text(yaml.safe_dump(config))
            report = benchmark_worker.admit(
                metadata(benchmark), benchmark.directory, submission, {"deadline_seconds": 120}
            )
            self.assertFalse(report["passed"])
            self.assertIn("Original deadline required", report["error"])


class PreflightExecutionTests(unittest.TestCase):
    def test_hosted_admission_never_starts_a_world(self):
        benchmark = checkout()
        hooks = SimpleNamespace(**{role: Mock() for role in benchmark.entrypoints})
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            submission = root / "config.yaml"
            submission.write_text(yaml.safe_dump(model_config(benchmark, "incident.plan")))
            declaration = {
                **metadata(benchmark),
                "identity": {},
                "core_files": {},
                "payload_files": {},
                "options": {"admission": True, "deadline_seconds": 120},
                "entrypoints": {
                    role: {"module": python_module(entry.path), "symbol": role}
                    for role, entry in benchmark.entrypoints.items()
                },
            }
            manifest = root / "benchmark.json"
            manifest.write_text(json.dumps(declaration))
            paths = {
                "/tests/benchmark.json": manifest,
                "/tests/payload": benchmark.directory,
                "/logs/verifier": root / "verifier",
                "/submission/config.yaml": submission,
            }
            with (
                patch.object(benchmark_worker, "Path", side_effect=lambda path: paths.get(str(path), Path(path))),
                patch.object(benchmark_worker.importlib, "import_module", return_value=hooks),
                patch.object(benchmark_worker, "observe") as observe,
                patch.dict(os.environ, {"SAPI_LLM_MODE": "stub", "SAPI_EXPECTED_SUBMISSION_SHA256": ""}),
            ):
                self.assertEqual(benchmark_worker.main(), 0)
            for role in benchmark.entrypoints:
                getattr(hooks, role).assert_not_called()
            observe.assert_not_called()
            self.assertEqual((root / "verifier/reward.txt").read_text(), "1\n")
            self.assertTrue(json.loads((root / "verifier/evaluation/report.json").read_text())["passed"])

    def test_local_capability_discovery_matches_the_executable_table(self):
        process = subprocess.run(
            ["node", "-e", OPERATION_SOURCE + "\nconsole.log(JSON.stringify(Object.keys(operations)))"],
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        )
        self.assertEqual(
            set(re.findall(r"^  '([^']+)':", OPERATION_SOURCE, re.MULTILINE)), set(json.loads(process.stdout))
        )
