"""Hosted terminal evidence survives worker loss; stage-owned time limits compose before dispatch."""

from dataclasses import replace
from io import BytesIO
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from sapi_config_lab.coordinate import hosted_worker
from sapi_config_lab.coordinate.packages import runtime_grant_seconds, verifier_bounds
from sapi_config_lab.coordinate.providers import ENVIRONMENTS, EVALUATORS
from sapi_config_lab.coordinate.runs import Run, fingerprint
from sapi_config_lab.coordinate.scenarios import SCENARIOS
from sapi_config_lab.execute.host import HostConfig
from sapi_config_lab.execute.hosting import TrialHost


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


class ComposedBudgetTests(unittest.TestCase):
    def test_longer_environment_and_evaluator_limits_reach_grant_and_harbor_budget(self):
        scenario = replace(SCENARIOS["checkout-recovery"], benchmark=None)
        provider = replace(ENVIRONMENTS[scenario.environment], limit_seconds=lambda scenario: 1200)
        evaluator = replace(EVALUATORS[scenario.evaluator], timeout_seconds=900)
        with (
            patch.dict(SCENARIOS, {scenario.name: scenario}),
            patch.dict(ENVIRONMENTS, {scenario.environment: provider}),
            patch.dict(EVALUATORS, {scenario.evaluator: evaluator}),
        ):
            self.assertEqual(verifier_bounds((scenario.name,))[scenario.name], 1200 + 900 + 480 + 120)
            self.assertGreaterEqual(
                runtime_grant_seconds(scenario),
                1200 + scenario.harbor["build_timeout_sec"] + scenario.harbor["agent_timeout_sec"],
            )

    def test_finish_rpc_includes_declared_evaluation_duration(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "connection.json").write_text(
                json.dumps({"url": "http://unused", "token": "secret", "evaluation_seconds": 900})
            )
            responses = [BytesIO(b"{}"), BytesIO(b'{"result":{}}')]
            with (
                patch.object(hosted_worker, "TESTS", root),
                patch.object(hosted_worker, "LOGS", root),
                patch.object(hosted_worker, "SUBMISSION", root / "missing"),
                patch.object(hosted_worker, "urlopen", side_effect=responses) as post,
            ):
                hosted_worker.run()
            self.assertEqual([call.kwargs["timeout"] for call in post.call_args_list], [240, 1140])

    def test_multiple_attempts_cannot_reuse_one_hosted_environment(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run = Run(root / "run", {}, {}, "test", staging=root / "stage")
            task = run.tasks / "checkout-recovery"
            task.mkdir(parents=True)
            (task / "task.toml").write_text("")
            scenario = replace(SCENARIOS["checkout-recovery"], benchmark=None)
            with patch.dict(SCENARIOS, {scenario.name: scenario}):
                with self.assertRaisesRegex(ValueError, "exactly one attempt"):
                    run.harbor("job", run.tasks, "oracle", attempts=2)
