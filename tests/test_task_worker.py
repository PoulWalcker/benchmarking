"""Semantic hooks surround execution; admission and missing data cannot dispatch it."""

import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from sapi_config_lab.contracts import RunBinding
from sapi_config_lab.coordinate import task_worker as worker
from sapi_config_lab.paths import workspace_root

ROOT = workspace_root()


class TaskWorkerTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.task = ROOT / "tasks/checkout-recovery"
        (self.root / "submission").mkdir()
        self.submission = self.root / "submission/config.yaml"
        shutil.copyfile(ROOT / "tasks/checkout-recovery/solution/config.yaml", self.submission)
        self.metadata = {
            "name": self.task.name,
            "operations": "operations.js",
            "bindings": "bindings.yaml",
            "budgets": {"runtime_model_calls": 4},
        }
        self.options = {"mode": "stub", "deadline_seconds": 120}
        self.output = self.root / "logs/verifier"

    def invoke(self, *, missing=False, malformed=False, reward="default", verdict="default"):
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
            if verdict != "default":
                return verdict
            result = {
                "execution": True,
                "acceptance": True,
                "quality": {"status": "complete", "score_0_10": 2.0, "normalized_reward": 0.2},
            }
            if reward != "default":
                result["harbor_reward"] = reward
            return result

        with patch.object(worker, "observe", side_effect=observe):
            self.assertEqual(
                worker.run_task(
                    self.task,
                    self.output,
                    self.submission,
                    self.options,
                    {"plan": plan, "prepare": prepare, "snapshot": snapshot, "evaluate": evaluate},
                ),
                0,
            )
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

    def test_admission_keeps_declared_deadline_and_rejects_missing_submission(self):
        root = self.task
        options = self.options | {"deadline_seconds": 119}
        self.assertFalse(worker.admit(self.metadata, root, self.submission, options)["passed"])
        self.submission.unlink()
        self.assertFalse(worker.admit(self.metadata, root, self.submission, self.options)["passed"])

    def test_declared_reward_is_projected_without_inventing_a_zero(self):
        self.invoke(reward=0.732)
        self.assertEqual((self.output / "reward.txt").read_text(), "0.732\n")
        (self.output / "reward.txt").unlink()
        self.invoke(reward=None)
        self.assertFalse((self.output / "reward.txt").exists())

    def test_malformed_verdict_never_writes_success_or_reward(self):
        for result in (
            None,
            {},
            {"execution": 1, "acceptance": True, "quality": None},
            {"execution": True, "acceptance": True, "quality": {}},
            {"execution": True, "acceptance": True, "quality": None, "harbor_reward": True},
        ):
            with self.subTest(result=result), self.assertRaises(ValueError):
                self.invoke(verdict=result)
            self.assertFalse((self.output / "result.json").exists())
            self.assertFalse((self.output / "reward.txt").exists())
