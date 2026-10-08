"""Hosted terminal evidence survives worker loss; stage-owned time limits compose before dispatch."""

import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock

from sapi_config_lab.coordinate.benchmark_discovery import select_benchmarks
from sapi_config_lab.coordinate.packages import runtime_grant_seconds, verifier_bounds
from sapi_config_lab.coordinate.runs import Run, fingerprint
from sapi_config_lab.execute.host import HostConfig
from sapi_config_lab.execute.hosting import TrialHost
from sapi_config_lab.paths import workspace_root


class World:
    limit_seconds = 60

    def __init__(self):
        self.connection = {"base_url": "http://127.0.0.1:1", "access_token": "ephemeral"}
        self.identity = {"world": "test"}
        self.closed = False
        self.finalizations = 0

    def finalize(self):
        self.finalizations += 1
        return {"state": {"effect": "committed"}}

    def transport_evidence(self):
        return {"events": ["effect observed"]}

    def close(self):
        self.closed = True


class HostedLifetimeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.record = Path(self.temporary.name) / "trial"
        self.world = World()
        self.evaluate = Mock(return_value={})
        self.host = TrialHost(
            lambda: self.world,
            self.record,
            scenario="test",
            seed=0,
            artifact=None,
            llm_mode="stub",
            bridge_url=None,
            evaluate=self.evaluate,
            host=HostConfig(listen_host="127.0.0.1", container_host="127.0.0.1"),
        )
        self.addCleanup(self.host.close)

    def trial(self):
        return json.loads((self.record / "evidence/trial.json").read_text())

    def test_abort_retains_state_and_transport_without_inventing_native_outcome_or_acceptance(self):
        self.host.begin()
        self.host.close()
        trial = self.trial()
        self.assertEqual(trial["interruption"], "aborted")
        self.assertIsNone(trial["native_execution"])
        self.assertFalse(trial["terminal_completion"])
        self.assertIsNone(trial["submission"])
        self.assertEqual(
            json.loads((self.record / "evidence/environment-evidence.json").read_text())["state"],
            {"effect": "committed"},
        )
        self.assertTrue((self.record / "evidence/transport-evidence.json").exists())
        self.evaluate.assert_not_called()
        self.assertTrue(self.world.closed)
        before = fingerprint(self.record)
        self.host.close()
        self.assertEqual(fingerprint(self.record), before)

    def test_real_deadline_persists_without_finish_and_rejects_late_overwrite(self):
        self.world.limit_seconds = 0.02
        self.host.begin()
        self.host.timer.join(timeout=2)
        self.assertFalse(self.host.timer.is_alive())
        self.assertEqual(self.trial()["termination_reason"], "timeout")
        before = fingerprint(self.record)
        with self.assertRaisesRegex(ValueError, "already finalized"):
            self.host.finish({"status": "success", "output": {"final_answer": "late"}}, "hash")
        self.host.close()
        self.assertEqual(fingerprint(self.record), before)
        self.evaluate.assert_not_called()

    def test_finish_and_timeout_race_has_one_immutable_terminal_record(self):
        self.host.begin()
        barrier = threading.Barrier(2)
        errors = []

        def finish():
            barrier.wait()
            try:
                self.host.finish({"status": "success", "output": {"final_answer": "done"}}, "hash")
            except ValueError as error:
                errors.append(str(error))

        thread = threading.Thread(target=finish)
        thread.start()
        barrier.wait()
        self.host.expire()
        thread.join(timeout=2)
        self.assertFalse(thread.is_alive())
        self.assertEqual(self.world.finalizations, 1)
        trial = self.trial()
        self.assertEqual(self.evaluate.call_count, int(trial["terminal_completion"]))
        before = fingerprint(self.record)
        self.host.close()
        self.assertEqual(fingerprint(self.record), before)
        self.assertTrue(not errors or "already finalized" in errors[0])

    def test_environment_collection_failure_preserves_other_evidence_and_never_evaluates(self):
        self.world.finalize = Mock(side_effect=RuntimeError("unavailable"))
        self.host.begin()
        with self.assertRaisesRegex(RuntimeError, "collection incomplete"):
            self.host.finish({"status": "success", "output": {"final_answer": "done"}}, "hash")
        self.assertTrue((self.record / "evidence/transport-evidence.json").exists())
        self.assertTrue((self.record / "evidence/native-record.json").exists())
        self.assertEqual(self.trial()["evidence_errors"], [{"stage": "environment-evidence", "type": "RuntimeError"}])
        self.evaluate.assert_not_called()

    def test_evaluation_failure_leaves_evidence_complete_and_unchanged(self):
        self.evaluate.side_effect = RuntimeError("judge failed")
        self.host.begin()
        with self.assertRaisesRegex(RuntimeError, "judge failed"):
            self.host.finish({"status": "success", "output": {"final_answer": "done"}}, "hash")
        before = fingerprint(self.record / "evidence")
        self.host.close()
        self.assertEqual(fingerprint(self.record / "evidence"), before)
        self.assertEqual(self.trial()["evidence_errors"], [])


class NativeBudgetTests(unittest.TestCase):
    def test_multiple_attempts_cannot_reuse_one_native_world(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            selected = select_benchmarks(workspace_root() / "benchmarks", ("checkout-recovery",))
            run = Run(root / "run", {}, {}, "test", staging=root / "stage", benchmarks=selected)
            task = run.tasks / selected[0].name
            task.mkdir(parents=True)
            (task / "task.toml").write_text("")
            with self.assertRaisesRegex(ValueError, "exactly one attempt"):
                run.harbor("job", run.tasks, "oracle", attempts=2)

    def test_bridge_grant_uses_declared_native_phases(self):
        selected = select_benchmarks(workspace_root() / "benchmarks", ("checkout-recovery",))[0]
        self.assertGreaterEqual(runtime_grant_seconds(selected), selected.config["deadline_seconds"])
        self.assertGreaterEqual(verifier_bounds((selected,))[selected.name], selected.config["deadline_seconds"])
