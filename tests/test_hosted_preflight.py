"""Admission compiles explicit benchmark inputs without starting its world."""

import copy
import json
import os
from pathlib import Path
import re
import runpy
import subprocess
import sys
import tempfile
from types import ModuleType
import unittest
from unittest.mock import Mock, patch

import yaml

from sapi_config_lab.compile.n8n import compile_n8n
from sapi_config_lab.coordinate import native_record, task_worker
from sapi_config_lab.coordinate.backend import N8nBackend
from sapi_config_lab.coordinate.live import check_trials
from sapi_config_lab.coordinate.native_tasks import policy
from sapi_config_lab.evaluate.records import load_trials
from sapi_config_lab.evidence import sha256
from sapi_config_lab.paths import workspace_root
from sapi_config_lab.profile import Unsupported, read, read_bindings
from tests.support.invoice import OPERATION_SOURCE
from tests.support.native import SimulatedN8n


def checkout():
    return workspace_root() / "tasks/checkout-recovery"


def model_config(benchmark, operation):
    config = copy.deepcopy(read(benchmark / "solution/config.yaml"))
    binding = read_bindings(benchmark / "bindings.yaml")[operation]
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
        "operations": "operations.js",
        "bindings": "bindings.yaml",
        "budgets": {"runtime_model_calls": policy(benchmark).get("runtime_model_calls")},
    }


def native_main(task, submission, output):
    """Execute the real trusted task composition against an in-process test engine."""
    with patch.dict(sys.modules), patch.object(sys, "path", [str(task / "tests"), *sys.path]):
        for name in list(sys.modules):
            if name == "payload" or name.startswith("payload."):
                del sys.modules[name]
        package = ModuleType("payload")
        package.__path__ = [str(task)]
        sys.modules["payload"] = package
        namespace = runpy.run_path(str(task / "tests/main.py"))
        globals_ = namespace["main"].__globals__
        globals_.update(ROOT=task, OUT=output, SUBMISSION=submission)
        manifest = output.parent / "source-manifest.json"
        manifest.write_text(json.dumps({"test": "frozen"}))
        engine = SimulatedN8n((task / "operations.js").read_text())
        with (
            patch.object(
                native_record,
                "Path",
                side_effect=lambda value: manifest if value == "/opt/source-manifest.json" else Path(value),
            ),
            patch.object(N8nBackend, "execute", side_effect=engine.execute) as execute,
            patch.dict(
                os.environ,
                {
                    "SAPI_NATIVE_MODE": "admission",
                    "SAPI_HOSTED_ADMISSION": "1",
                    "SAPI_LLM_MODE": "stub",
                    "SAPI_EXPECTED_SUBMISSION_SHA256": sha256(submission),
                },
            ),
        ):
            if task.name == "checkout-recovery":
                dispatch = Mock(side_effect=AssertionError("world started"))
                globals_["run_task"] = dispatch
            assert namespace["main"]() == 0
            if task.name == "checkout-recovery":
                dispatch.assert_not_called()
            return execute.call_count


class HostedPreflightTests(unittest.TestCase):
    def test_advertised_model_operations_fail_clearly_in_stub_mode_and_compile_live(self):
        benchmark = checkout()
        bindings = read_bindings(benchmark / "bindings.yaml")
        source = (benchmark / "operations.js").read_text()
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
            report = task_worker.admit(metadata(benchmark), benchmark, submission, {"deadline_seconds": 120})
            self.assertTrue(report["passed"], report)
            trial = {
                "task_name": benchmark.name,
                "acceptance": report,
                "exception": None,
                "rewards": {"reward": 1.0},
                "result": {"execution": None, "acceptance": None, "quality": None},
            }
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
            report = task_worker.admit(limited, benchmark, submission, {"deadline_seconds": 120})
            self.assertFalse(report["passed"])
            self.assertIn("Runtime cap exceeded", report["error"])
            config["execution"]["deadline_seconds"] = 121
            submission.write_text(yaml.safe_dump(config))
            report = task_worker.admit(metadata(benchmark), benchmark, submission, {"deadline_seconds": 120})
            self.assertFalse(report["passed"])
            self.assertIn("Original deadline required", report["error"])


class PreflightExecutionTests(unittest.TestCase):
    def test_hosted_admission_never_starts_a_world(self):
        benchmark = checkout()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            submission = root / "config.yaml"
            submission.write_text(yaml.safe_dump(model_config(benchmark, "incident.plan")))
            self.assertEqual(native_main(benchmark, submission, root / "verifier"), 0)
            self.assertEqual((root / "verifier/reward.txt").read_text(), "1\n")
            self.assertTrue(json.loads((root / "verifier/evaluation/report.json").read_text())["passed"])
            self.assertEqual(
                json.loads((root / "verifier/result.json").read_text()),
                {"execution": None, "acceptance": None, "quality": None},
            )

    def test_mixed_preflight_replays_fixture_and_only_admits_world(self):
        root = workspace_root() / "tasks"
        invoice = root / "invoice-total"
        world = checkout()
        benchmarks = {item.name: item for item in (invoice, world)}
        for incorrect in (False, True):
            with self.subTest(incorrect=incorrect), tempfile.TemporaryDirectory() as directory:
                job = Path(directory) / "jobs/preflight"
                trials, selected = [], {}
                for benchmark in (invoice, world):
                    trial = job / benchmark.name
                    trial.mkdir(parents=True)
                    submission = trial / "config.yaml"
                    config = read(benchmark / "solution/config.yaml")
                    if benchmark is invoice and incorrect:
                        config["workflow"]["output"] = {
                            "literal": {"total_minor": 999, "invoice_count": 999, "currency": "USD"}
                        }
                    submission.write_text(yaml.safe_dump(config))
                    options = {"mode": "stub", "deadline_seconds": config["execution"]["deadline_seconds"]}
                    self.assertTrue(task_worker.admit(metadata(benchmark), benchmark, submission, options)["passed"])
                    calls = native_main(benchmark, submission, trial / "verifier")
                    if benchmark is invoice:
                        self.assertGreater(calls, 0, "Fixture preflight skipped independent replay")
                        verdict = json.loads((trial / "verifier/result.json").read_text())
                        self.assertIs(verdict["execution"], True)
                        self.assertIs(verdict["acceptance"], not incorrect)
                        # The gate must not depend on the benchmark's legacy report format.
                        (trial / "verifier/evaluation/report.json").unlink()
                    else:
                        self.assertEqual(calls, 0)
                        verdict = json.loads((trial / "verifier/result.json").read_text())
                        self.assertEqual(verdict, {"execution": None, "acceptance": None, "quality": None})
                    reward = float((trial / "verifier/reward.txt").read_text())
                    (trial / "result.json").write_text(
                        json.dumps(
                            {
                                "task_name": benchmark.name,
                                "verifier_result": {"rewards": {"reward": reward}},
                                "exception_info": None,
                            }
                        )
                    )
                    selected[benchmark.name] = {"sha256": sha256(submission)}
                trials = load_trials(job)
                if incorrect:
                    with self.assertRaises(ValueError):
                        check_trials(trials, selected, benchmarks=benchmarks, mode="stub", admission=True)
                else:
                    check_trials(trials, selected, benchmarks=benchmarks, mode="stub", admission=True)

    def test_fixture_admission_report_cannot_pass_preflight(self):
        root = workspace_root() / "tasks"
        invoice = root / "invoice-total"
        report = task_worker.admit(
            metadata(invoice), invoice, (invoice / "solution/config.yaml"), {"deadline_seconds": 30}
        )
        trial = {"task_name": invoice.name, "acceptance": report, "exception": None, "rewards": {"reward": 1.0}}
        with self.assertRaises(ValueError):
            check_trials(
                [trial],
                {invoice.name: {"sha256": report["submission_sha256"]}},
                benchmarks={invoice.name: invoice},
                mode="stub",
                admission=True,
            )

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
