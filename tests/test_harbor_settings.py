"""Declared Harbor phase limits contain the selected observation plan."""

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from sapi_config_lab.coordinate.native_tasks import select_tasks
from sapi_config_lab.coordinate.runs import Run
from sapi_config_lab.paths import workspace_root
from sapi_config_lab.profile import read
from tests.support.invoice import cases, fixture
from verification.contracts import Rejected
from verification.verify import plan

ROOT = workspace_root()


class NativeSettingsTests(unittest.TestCase):
    def setUp(self):
        self.selected = select_tasks(ROOT / "tasks", ("invoice-total",))
        self.benchmark = self.selected[0]

    def test_independent_plan_rejects_longer_or_malformed_deadlines(self):
        with tempfile.TemporaryDirectory() as directory:
            for deadline, accepted in ((120, True), (60, True), (121, False), (True, False), ("120", False)):
                config = read(self.benchmark / "solution/config.yaml")
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
            run = Run(root, {}, {}, "native", native_tasks=self.selected)
            with (
                patch.object(run, "check"),
                patch("sapi_config_lab.coordinate.runs.run_job", return_value=0) as command,
            ):
                run.harbor("oracle", run.native_tasks[0], "oracle")
            self.assertNotIn("timeout", command.call_args.kwargs)
            self.assertNotIn("harbor_timeouts", run.report)

    def test_command_running_agents_cannot_share_trusted_verifier_environment(self):
        with tempfile.TemporaryDirectory() as directory:
            run = Run(Path(directory), {}, {}, "native", native_tasks=self.selected)
            with self.assertRaisesRegex(RuntimeError, "shared verifier environment"):
                run.harbor("job", run.native_tasks[0], "terminus-2")
