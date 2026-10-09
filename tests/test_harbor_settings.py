"""Declared Harbor phase limits contain the selected observation plan."""

import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from sapi_config_lab.benchmark_loading import freeze_identity, load_entrypoints
from sapi_config_lab.coordinate.benchmark_discovery import select_benchmarks
from sapi_config_lab.coordinate.packages import (
    AUTHORED_DEADLINE_SECONDS,
    VERIFIER_OVERHEAD_SECONDS,
    stage_tasks,
    verifier_bounds,
)
from sapi_config_lab.coordinate.runs import Run
from sapi_config_lab.execute.n8n import execution_ceiling
from sapi_config_lab.harbor_integration.tasks import validate_config
from sapi_config_lab.paths import workspace_root
from sapi_config_lab.profile import read
from tests.support.invoice import cases, fixture
from verification.contracts import Rejected
from verification.verify import plan

ROOT = workspace_root()


class NativeSettingsTests(unittest.TestCase):
    def setUp(self):
        self.selected = select_benchmarks(ROOT / "tasks", ("invoice-total",))
        self.benchmark = self.selected[0]

    def test_sequential_observations_fit_declared_native_verifier_phase(self):
        options = {"mode": "stub", "deadline_seconds": 30}
        issued = load_entrypoints(self.benchmark, freeze_identity(self.benchmark, options)).plan(
            self.benchmark.reference.source, options
        )
        self.assertEqual(len(issued["entries"]), 14)
        expected = (
            max(14 * execution_ceiling(30, bound=False), execution_ceiling(600, bound=False))
            + VERIFIER_OVERHEAD_SECONDS
        )
        self.assertEqual(verifier_bounds(self.selected)[self.benchmark.name], expected)
        self.assertIn(f"deadline_seconds: {AUTHORED_DEADLINE_SECONDS}", (ROOT / "generation/FORMAT.md").read_text())

    def test_declared_phase_is_preserved_and_insufficient_phase_refuses_before_writing(self):
        settings = validate_config((self.benchmark.directory / self.benchmark.harbor_task).read_text())
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "valid"
            stage_tasks(target, root=ROOT, benchmarks=self.selected)
            actual = validate_config((target / "invoice-total/task.toml").read_text())
            self.assertEqual(actual.verifier.timeout_sec, settings.verifier.timeout_sec)
            self.assertEqual(actual.agent.timeout_sec, settings.agent.timeout_sec)
            settings.verifier.timeout_sec = verifier_bounds(self.selected)[self.benchmark.name] - 1
            with patch("sapi_config_lab.coordinate.packages.validate_config", return_value=settings):
                with self.assertRaisesRegex(ValueError, "exceeds the native verifier phase"):
                    stage_tasks(Path(directory) / "invalid", root=ROOT, benchmarks=self.selected)
            self.assertFalse((Path(directory) / "invalid").exists())

    def test_long_replay_deadline_refuses_staging(self):
        with tempfile.TemporaryDirectory() as directory:
            config = read(self.benchmark.reference.source)
            config["execution"]["deadline_seconds"] = 3600
            path = Path(directory) / "slow.yaml"
            path.write_text(json.dumps(config))
            with self.assertRaisesRegex(ValueError, "exceeds the native verifier phase"):
                stage_tasks(
                    Path(directory) / "tasks",
                    root=ROOT,
                    benchmarks=self.selected,
                    mode="replay",
                    submissions={
                        self.benchmark.name: {"path": path, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
                    },
                )

    def test_each_package_records_its_declared_deadline(self):
        for mode, deadline in (("oracle", 30), ("generation", AUTHORED_DEADLINE_SECONDS)):
            with tempfile.TemporaryDirectory() as directory:
                target = Path(directory) / "tasks"
                stage_tasks(target, root=ROOT, benchmarks=self.selected, mode=mode)
                metadata = json.loads((target / "invoice-total/tests/benchmark.json").read_text())
                self.assertEqual(metadata["options"]["deadline_seconds"], deadline)

    def test_independent_plan_rejects_longer_or_malformed_deadlines(self):
        with tempfile.TemporaryDirectory() as directory:
            for deadline, accepted in ((120, True), (60, True), (121, False), (True, False), ("120", False)):
                config = read(self.benchmark.reference.source)
                config["execution"]["deadline_seconds"] = deadline
                path = Path(directory) / "config.yaml"
                path.write_text(json.dumps(config))
                if accepted:
                    self.assertTrue(
                        plan("invoice-total", path, cases(), deadline_budget=120, fixture=fixture())["entries"]
                    )
                else:
                    with self.assertRaises(Rejected) as raised:
                        plan("invoice-total", path, cases(), deadline_budget=120, fixture=fixture())
                    self.assertEqual(raised.exception.code, "deadline_exceeds_budget")

    def test_native_job_dispatch_has_no_duplicate_outer_watchdog(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run = Run(root, {}, {}, "native", staging=root / "staging", benchmarks=self.selected)
            (run.tasks / "invoice-total/tests").mkdir(parents=True)
            (run.tasks / "invoice-total/tests/benchmark.json").write_text("{}")
            run.bounds = {"invoice-total": 5160}
            with (
                patch.object(run, "check"),
                patch("sapi_config_lab.coordinate.runs.run_job", return_value=0) as command,
            ):
                run.harbor("oracle", run.tasks, "oracle")
            self.assertNotIn("timeout", command.call_args.kwargs)
            self.assertNotIn("harbor_timeouts", run.report)

    def test_command_running_agents_cannot_share_trusted_verifier_environment(self):
        with tempfile.TemporaryDirectory() as directory:
            run = Run(Path(directory), {}, {}, "native", staging=Path(directory))
            with self.assertRaisesRegex(RuntimeError, "shared verifier environment"):
                run.harbor("job", run.tasks, "terminus-2")
