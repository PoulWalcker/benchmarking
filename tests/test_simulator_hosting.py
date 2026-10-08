"""Simulator tasks run through the one Run: hosted per job, credentials never persisted."""

import contextlib
from dataclasses import replace
import io
import json
from pathlib import Path
import tempfile
from typing import ClassVar
import unittest
from unittest.mock import patch

from sapi_config_lab.coordinate.evaluation import HOSTED_REPORT, control_passed, hosted_evaluation, trial_accepted
from sapi_config_lab.coordinate.evaluation import main as evaluation_main
from sapi_config_lab.coordinate.providers import ENVIRONMENTS, EVALUATORS, HostedEvaluator
from sapi_config_lab.coordinate.runs import Hosting, Run, fingerprint
from sapi_config_lab.coordinate.scenarios import SCENARIOS


class FakeTrialHost:
    """Stands in for execute.hosting.TrialHost; records how it was configured."""

    made: ClassVar[list] = []

    def __init__(self, start, record, *, scenario, seed, artifact, llm_mode, bridge_url, evaluate, host):
        self.start, self.record, self.llm_mode, self.evaluate = start, record, llm_mode, evaluate
        self.token = "secret-" + record.name
        self.closed = False
        FakeTrialHost.made.append(self)

    @property
    def connection(self):
        return {"url": "http://host.docker.internal:1", "token": self.token}

    def credentials(self):
        return [self.token]

    def close(self):
        self.closed = True


def hosted_report(scenario, passed, reward):
    quality = {"status": "complete", "score_0_10": reward * 10, "normalized_reward": reward}
    return {
        "schema": HOSTED_REPORT,
        "scenario": scenario,
        "mode": "stub",
        "submission_sha256": "abc",
        "passed": passed,
        "result": {"execution": True, "acceptance": passed, "quality": quality},
    }


class HostingTests(unittest.TestCase):
    def setUp(self):
        FakeTrialHost.made = []
        self.root = Path(tempfile.mkdtemp())
        self.run = Run(self.root / "run", {}, {}, "t", staging=self.root / "staging")
        self.run.bounds = {"checkout-recovery": 720, "invoice-total": 5160}
        tasks = self.run.tasks
        for name, hosted in (("checkout-recovery", True), ("invoice-total", False)):
            (tasks / name / "tests").mkdir(parents=True)
            (tasks / name / "task.toml").write_text("")
            if hosted:
                environment = {"scenario": name, "seed": 0}
                (tasks / name / "tests/environment.json").write_text(json.dumps(environment))
        self.run.output.mkdir()
        self.pinned = fingerprint(tasks)
        patcher = patch("sapi_config_lab.coordinate.runs.TrialHost", FakeTrialHost)
        patcher.start()
        self.addCleanup(patcher.stop)

    def harbor(self, persisted: str = "") -> tuple[int, list[dict]]:
        def harbor_run(argv, log, **_):
            job_tasks = Path(argv[argv.index("--path") + 1])
            connection = json.loads((job_tasks / "checkout-recovery/tests/connection.json").read_text())
            self.assertEqual(connection["token"], "secret-checkout-recovery")
            self.assertFalse((job_tasks / "invoice-total/tests/connection.json").exists())
            trial = self.root / "staging/jobs/oracle/checkout__1"
            trial.mkdir(parents=True)
            (trial / "result.json").write_text(
                json.dumps({"task_name": "checkout-recovery", "verifier_result": {"rewards": {"reward": 0.732}}})
            )
            # The container's copy is ignored for a hosted trial; only the host's record counts.
            (trial / "verifier/evaluation").mkdir(parents=True)
            report = hosted_report("checkout-recovery", True, 1.0)
            (trial / "verifier/evaluation/report.json").write_text(json.dumps(report) + persisted)
            record = self.run.output / "environments/oracle/checkout-recovery/evaluation"
            record.mkdir(parents=True)
            (record / "report.json").write_text(json.dumps(hosted_report("checkout-recovery", True, 0.732)))
            return 0

        hosting = Hosting("stub", lambda scenario, record: {})
        with patch("sapi_config_lab.coordinate.runs.run_logged", side_effect=harbor_run):
            return self.run.harbor("oracle", self.run.tasks, "oracle", hosting=hosting)

    def test_hosted_trial_uses_the_host_record_and_never_changes_pinned_packages(self):
        exit_code, trials = self.harbor()
        self.assertEqual(exit_code, 0)
        self.assertEqual(fingerprint(self.run.tasks), self.pinned)
        self.assertFalse(
            (self.root / "staging/hosted/oracle").exists() and any((self.root / "staging/hosted/oracle").iterdir())
        )
        (host,) = FakeTrialHost.made
        self.assertTrue(host.closed)
        self.assertEqual(host.llm_mode, "stub")
        self.assertIs(host.start.func, ENVIRONMENTS["autowfbench"].start)
        self.assertEqual(host.start.args, (SCENARIOS["checkout-recovery"], 0, self.run.host))
        (trial,) = trials
        self.assertEqual(trial["result"]["quality"]["normalized_reward"], 0.732)
        self.assertTrue(trial_accepted(trial))
        self.assertTrue(control_passed("oracle", trial))
        trial["rewards"] = {"reward": 0.5}
        self.assertFalse(control_passed("oracle", trial))

    def test_a_leaked_credential_fails_the_job(self):
        with self.assertRaisesRegex(RuntimeError, "Ephemeral credentials"):
            self.harbor(persisted=" secret-checkout-recovery")
        self.assertTrue(FakeTrialHost.made[0].closed)

    def test_simulator_tasks_cannot_run_without_a_host(self):
        with self.assertRaisesRegex(RuntimeError, "host environment"):
            self.run.harbor("oracle", self.run.tasks, "oracle")


class HostedEvaluationTests(unittest.TestCase):
    """The core writes the report and dispatches reevaluation by the scenario's evaluator name."""

    def setUp(self):
        scenario = replace(SCENARIOS["checkout-recovery"], benchmark=None)
        legacy = patch.dict(SCENARIOS, {scenario.name: scenario})
        legacy.start()
        self.addCleanup(legacy.stop)
        self.result = {"execution": True, "acceptance": False, "quality": None}
        self.calls = []
        evaluator = HostedEvaluator(
            judge_calls=0,
            timeout_seconds=30,
            prepare=lambda scenario, judge: self.calls.append(("prepare", scenario.name, judge)) or self.evaluate,
            reevaluate=lambda scenario, record, output, args: (
                self.calls.append(("again", scenario.name)) or self.result
            ),
            modules=(),
        )
        patcher = patch.dict(EVALUATORS, {"autowfbench": evaluator})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.record = Path(tempfile.mkdtemp())
        (self.record / "evidence").mkdir()
        trial = {"scenario": "checkout-recovery", "llm_mode": "stub", "submission_sha256": "abc"}
        (self.record / "evidence/trial.json").write_text(json.dumps(trial))

    def evaluate(self, record):
        return self.result

    def test_each_hosted_scenario_is_frozen_up_front_and_reported_once(self):
        evaluate = hosted_evaluation(("checkout-recovery", "invoice-total"), "judge-x")
        self.assertEqual(self.calls, [("prepare", "checkout-recovery", "judge-x")])
        report = evaluate("checkout-recovery", self.record)
        self.assertEqual(json.loads((self.record / "evaluation/report.json").read_text()), report)
        self.assertEqual(
            report,
            {
                "schema": "sapi-lab-upstream-acceptance/v1",
                "scenario": "checkout-recovery",
                "mode": "stub",
                "submission_sha256": "abc",
                "passed": False,
                "result": self.result,
                "native_execution": None,
                "terminal_completion": None,
            },
        )

    def test_deterministic_evaluator_rejects_irrelevant_judge_options(self):
        output = self.record.parent / (self.record.name + "-unsupported")
        with self.assertRaisesRegex(ValueError, "does not support judge"):
            evaluation_main(
                ["--record", str(self.record), "--output", str(output), "--judgement", str(self.record / "unused.json")]
            )
        self.assertEqual(self.calls, [])

    def test_reevaluation_uses_the_scenario_the_trial_names(self):
        output = self.record.parent / (self.record.name + "-again")
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(evaluation_main(["--record", str(self.record), "--output", str(output)]), 1)
        self.assertEqual(self.calls, [("again", "checkout-recovery")])
        self.assertEqual(json.loads((output / "result.json").read_text()), self.result)


class ScoredControlTests(unittest.TestCase):
    """A declared reference reward makes the hosted oracle a scored control."""

    def trial(self, quality, reward=0.732):
        report = hosted_report("checkout-recovery", True, 0.732)
        report["result"]["quality"] = quality
        return {
            "task_name": "checkout-recovery",
            "exception": None,
            "rewards": {"reward": reward},
            "acceptance": report,
            "result": report["result"],
        }

    def test_the_oracle_must_reproduce_the_reference_reward_with_a_complete_score(self):
        self.assertEqual(SCENARIOS["checkout-recovery"].reference_reward, 0.732)
        complete = hosted_report("checkout-recovery", True, 0.732)["result"]["quality"]
        self.assertTrue(control_passed("oracle", self.trial(complete)))
        self.assertFalse(control_passed("oracle", self.trial(None)))
        self.assertFalse(control_passed("oracle", self.trial({**complete, "status": "unscored"})))
        self.assertFalse(control_passed("oracle", self.trial(complete, reward=0.5)))


class HarborRewardTests(unittest.TestCase):
    def test_the_worker_rewards_a_score_else_an_unscored_acceptance_and_never_invents_zero(self):
        from sapi_config_lab.coordinate.hosted_worker import harbor_reward

        scored = {"status": "complete", "score_0_10": 7.32, "normalized_reward": 0.732}
        self.assertEqual(harbor_reward({"acceptance": True, "quality": None}), "1")
        self.assertEqual(harbor_reward({"acceptance": False, "quality": None}), "0")
        self.assertIsNone(harbor_reward({"acceptance": None, "quality": None}))
        self.assertEqual(harbor_reward({"acceptance": True, "quality": scored}), "0.732")
        unscored = {"status": "unscored", "score_0_10": None, "normalized_reward": None}
        self.assertIsNone(harbor_reward({"acceptance": False, "quality": unscored}))

    def test_an_unscored_run_writes_no_reward_file(self):
        from sapi_config_lab.coordinate import hosted_worker

        unscored = {"status": "unscored", "score_0_10": None, "normalized_reward": None}
        for quality, expected in ((unscored, None), (None, "0\n")):
            report = {"result": {"execution": False, "acceptance": False, "quality": quality}}
            with (
                tempfile.TemporaryDirectory() as directory,
                patch.object(hosted_worker, "LOGS", Path(directory)),
                patch.object(hosted_worker, "run", return_value=report),
            ):
                self.assertEqual(hosted_worker.main(["run"]), 0)
                reward = Path(directory) / "reward.txt"
                self.assertEqual(reward.read_text() if reward.exists() else None, expected)


class VerifierResultTests(unittest.TestCase):
    def test_lifecycle_rows_without_a_case_kind_still_give_a_result(self):
        from sapi_config_lab.coordinate.evaluation import verifier_result

        report = {"passed": True, "cases": [{"name": "callback-and-cron", "passed": True, "native_executions": []}]}
        self.assertEqual(verifier_result(report, None), {"execution": None, "acceptance": True, "quality": None})


class AdmissionTests(unittest.TestCase):
    def test_admission_enforces_the_runtime_cap_and_original_deadline(self):
        import yaml

        from sapi_config_lab.coordinate import hosted_worker
        from sapi_config_lab.paths import workspace_root
        from sapi_config_lab.profile import read

        root = workspace_root() / "benchmarks/10-checkout-recovery"
        limits = {"scenario": "checkout-recovery", "runtime_model_calls": 0, "deadline_seconds": 120}
        with tempfile.TemporaryDirectory() as directory:
            submission, tests = Path(directory) / "config.yaml", Path(directory) / "tests"
            tests.mkdir()
            (tests / "bindings.yaml").write_bytes((root / "bindings.yaml").read_bytes())
            with (
                patch.object(hosted_worker, "SUBMISSION", submission),
                patch.object(hosted_worker, "TESTS", tests),
            ):
                config = read(root / "config.yaml")
                submission.write_text(yaml.safe_dump(config))
                self.assertTrue(hosted_worker.admit(limits)["passed"])
                config["execution"]["deadline_seconds"] = 600
                submission.write_text(yaml.safe_dump(config))
                self.assertEqual(hosted_worker.admit(limits)["error"], "Original deadline required")
                submission.write_text("workflow: [\n")
                self.assertEqual(hosted_worker.admit(limits)["error_type"], "Invalid")
                config["execution"]["deadline_seconds"] = 120
                config["workflow"]["steps"][0]["kind"] = "LLM"
                submission.write_text(yaml.safe_dump(config))
                self.assertEqual(hosted_worker.admit(limits)["error_type"], "Invalid")
                # The cap is checked once a definition compiles.
                with patch.object(hosted_worker, "default_backend"):
                    self.assertEqual(hosted_worker.admit(limits)["error"], "Runtime cap exceeded")


if __name__ == "__main__":
    unittest.main()
