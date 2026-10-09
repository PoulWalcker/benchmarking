"""Native world attempts and declared phase limits compose before dispatch."""

from pathlib import Path
import tempfile
import unittest

from sapi_config_lab.coordinate.benchmark_discovery import select_benchmarks
from sapi_config_lab.coordinate.packages import runtime_grant_seconds, verifier_bounds
from sapi_config_lab.coordinate.runs import Run
from sapi_config_lab.paths import workspace_root


class NativeBudgetTests(unittest.TestCase):
    def test_multiple_attempts_cannot_reuse_one_native_world(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            selected = select_benchmarks(workspace_root() / "tasks", ("checkout-recovery",))
            run = Run(root / "run", {}, {}, "test", staging=root / "stage", benchmarks=selected)
            task = run.tasks / selected[0].name
            task.mkdir(parents=True)
            (task / "task.toml").write_text("")
            with self.assertRaisesRegex(ValueError, "exactly one attempt"):
                run.harbor("job", run.tasks, "oracle", attempts=2)

    def test_bridge_grant_uses_declared_native_phases(self):
        selected = select_benchmarks(workspace_root() / "tasks", ("checkout-recovery",))[0]
        self.assertGreaterEqual(runtime_grant_seconds(selected), selected.config["deadline_seconds"])
        self.assertGreaterEqual(verifier_bounds((selected,))[selected.name], selected.config["deadline_seconds"])
