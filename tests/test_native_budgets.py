"""Native world attempts and declared phase limits compose before dispatch."""

from pathlib import Path
import tempfile
import unittest

from sapi_config_lab.coordinate.live import runtime_grant_seconds
from sapi_config_lab.coordinate.native_tasks import select_tasks
from sapi_config_lab.coordinate.runs import Run
from sapi_config_lab.paths import workspace_root


class NativeBudgetTests(unittest.TestCase):
    def test_multiple_attempts_cannot_reuse_one_native_world(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            selected = select_tasks(workspace_root() / "tasks", ("checkout-recovery",))
            run = Run(root / "run", {}, {}, "test", native_tasks=selected)
            with self.assertRaisesRegex(ValueError, "one attempt"):
                run.harbor("job", selected[0], "oracle", attempts=2)

    def test_bridge_grant_uses_declared_native_phases(self):
        selected = select_tasks(workspace_root() / "tasks", ("checkout-recovery",))[0]
        self.assertGreaterEqual(runtime_grant_seconds(selected), 120)
