"""Semantic hooks surround execution; admission and missing data cannot dispatch it."""

import json
from pathlib import Path
import shutil
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from sapi_config_lab.contracts import RunBinding
from sapi_config_lab.coordinate import benchmark_worker as worker
from sapi_config_lab.coordinate.benchmark_discovery import select_benchmarks
from sapi_config_lab.coordinate.packages import stage_tasks
from sapi_config_lab.paths import workspace_root

ROOT = workspace_root()


class BenchmarkWorkerTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        stage_tasks(
            self.root / "tasks", root=ROOT, benchmarks=select_benchmarks(ROOT / "benchmarks", ("checkout-recovery",))
        )
        shutil.copytree(self.root / "tasks/checkout-recovery/tests", self.root / "tests")
        (self.root / "submission").mkdir()
        self.submission = self.root / "submission/config.yaml"
        shutil.copyfile(ROOT / "benchmarks/10-checkout-recovery/config.yaml", self.submission)
        self.metadata = json.loads((self.root / "tests/benchmark.json").read_text())
        self.output = self.root / "logs/verifier"

    def invoke(self, *, missing=False, malformed=False, admission=False, reward="default"):
        calls = []
        if missing:
            self.submission.unlink()

        def plan(submission, options):
            calls.append("plan")
            if malformed:
                raise ValueError("invalid candidate")
            submission.read_text()
            return {"mode": "stub", "entries": []}

        def prepare(context):
            calls.append("prepare")
            return RunBinding(deadline_at=1234, operation_token="scoped", operation_url="http://world/tools")

        def observe(plan, submission, evidence, **kwargs):
            calls.append("observe")
            self.assertEqual(kwargs["runner"].keywords["deadline_at"], 1234)
            self.assertEqual(kwargs["runner"].keywords["operation_url"], "http://world/tools")
            directory = evidence / "cases/workflow"
            directory.mkdir(parents=True, exist_ok=True)
            (directory / "case.json").write_text(json.dumps({"status": "success", "output": {}}))

        def snapshot(context):
            calls.append("snapshot")
            self.record = context["record"]
            return {}

        def evaluate(evidence, options):
            calls.append("evaluate")
            result = {"execution": True, "acceptance": True, "quality": {"normalized_reward": 0.2}}
            if reward != "default":
                result["harbor_reward"] = reward
            return result

        module = SimpleNamespace(plan=plan, prepare=prepare, snapshot=snapshot, evaluate=evaluate)
        with (
            patch.object(worker, "Path", side_effect=lambda p: self.root / str(p).lstrip("/")),
            patch.object(worker.importlib, "import_module", return_value=module),
            patch.object(worker, "observe", side_effect=observe),
            patch.dict(worker.os.environ, {"SAPI_HOSTED_ADMISSION": "1" if admission else "0"}),
        ):
            self.assertEqual(worker.main(), 0)
        return calls

    def test_window_precedes_planning_and_snapshot_reads_actual_native_record(self):
        self.assertEqual(self.invoke(), ["prepare", "plan", "observe", "snapshot", "evaluate"])
        self.assertEqual(self.record["status"], "success")
        self.assertEqual((self.output / "reward.txt").read_text(), "1\n")

    def test_missing_and_invalid_submissions_keep_distinct_observations(self):
        self.assertEqual(self.invoke(malformed=True, reward=None), ["prepare", "plan", "snapshot", "evaluate"])
        self.assertEqual(self.record["status"], "error")
        self.assertEqual(self.record["error"], {"type": "ValueError"})
        self.assertFalse((self.output / "reward.txt").exists())
        self.assertEqual(self.invoke(missing=True, reward=None), ["prepare", "plan", "snapshot", "evaluate"])
        self.assertEqual(self.record["status"], "missing_submission")

    def test_admission_compiles_without_world_execution_snapshot_or_evaluation(self):
        self.assertEqual(self.invoke(admission=True), [])
        report = json.loads((self.output / "evaluation/report.json").read_text())
        self.assertTrue(report["passed"])
        self.assertEqual(report["schema"], "sapi-lab-admission/v1")
        self.assertFalse((self.output / "evidence").exists())

    def test_admission_keeps_declared_deadline_and_rejects_missing_submission(self):
        root = self.root / "tests/payload"
        options = self.metadata["options"] | {"deadline_seconds": 119}
        self.assertFalse(worker.admit(self.metadata, root, self.submission, options)["passed"])
        self.submission.unlink()
        self.assertFalse(worker.admit(self.metadata, root, self.submission, self.metadata["options"])["passed"])

    def test_declared_reward_is_projected_without_inventing_a_zero(self):
        self.invoke(reward=0.732)
        self.assertEqual((self.output / "reward.txt").read_text(), "0.732\n")
        (self.output / "reward.txt").unlink()
        self.invoke(reward=None)
        self.assertFalse((self.output / "reward.txt").exists())
