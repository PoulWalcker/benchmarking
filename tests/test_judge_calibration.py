"""Predefined semantic smoke controls are not themselves live judge evidence."""

import copy
import unittest

from sapi_config_lab.evaluate.judge_calibration import calibration_fixture, compare_calibration
from sapi_config_lab.evaluate.task_evaluation import evaluate
from tests import test_task_evaluation as fixtures


@unittest.skipUnless(fixtures.AVAILABLE, "Requires pinned source and benchmark extra")
class CalibrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures.PinnedEvaluationTests.setUpClass()
        cls.factory = fixtures.PinnedEvaluationTests(methodName="runTest")
        cls.contract = cls.factory.contract

    def test_bad_prose_does_not_rewrite_good_business_state_or_official_rubric(self):
        run = self.factory.run_log()
        before = copy.deepcopy(run)
        fixture = calibration_fixture(self.contract, run, "contentless")
        self.assertEqual(run, before)
        self.assertEqual(fixture["run_log"]["checks"], before["checks"])
        self.assertEqual(fixture["run_log"]["snapshots"], before["snapshots"])
        self.assertTrue(fixture["counterfactual_candidate_text"])
        self.assertEqual(fixture["measurement_status"], "not_judged")
        self.assertEqual(fixture["run_log"]["submission"]["final_answer"], "")
        # Fixed responses here check bookkeeping, not semantic model quality.
        reply = self.factory.reply(fixture["run_log"], answer="no")
        result = evaluate(self.contract, fixture["run_log"], reply)
        self.assertEqual(result["deterministic_points"], 6)
        self.assertTrue(compare_calibration(fixture, result)["passed"])

    def test_misleading_claims_preserve_failed_environment_and_expose_wrong_judgement(self):
        fixture = calibration_fixture(self.contract, self.factory.run_log("none"), "misleading-success")
        self.assertFalse(fixture["run_log"]["checks"]["eur_fixed"])
        result = evaluate(self.contract, fixture["run_log"], self.factory.reply(fixture["run_log"], answer="yes"))
        comparison = compare_calibration(fixture, result)
        self.assertEqual(comparison["status"], "measured")
        self.assertFalse(comparison["passed"])
        self.assertFalse(comparison["checks"]["honesty"])
        self.assertFalse(result["execution_pass"])
        # Failed calibration never changes the original evaluator's numeric score.
        self.assertEqual(result["score_0_10"], 5)

    def test_missing_or_demo_results_are_never_live_calibration_passes(self):
        fixture = calibration_fixture(self.contract, self.factory.run_log(), "supported-good")
        self.assertIsNone(compare_calibration(fixture, None)["passed"])
        result = evaluate(self.contract, fixture["run_log"], self.factory.reply(fixture["run_log"]))
        result["evaluation_mode"] = "demo"
        self.assertEqual(compare_calibration(fixture, result)["status"], "simulated_only")
        self.assertIsNone(compare_calibration(fixture, result)["passed"])

    def test_control_condition_and_result_identity_are_frozen(self):
        with self.assertRaises(ValueError):
            calibration_fixture(self.contract, self.factory.run_log(), "misleading-success")
        with self.assertRaises(ValueError):
            calibration_fixture(self.contract, self.factory.run_log("no_actions"), "misleading-success")
        fixture = calibration_fixture(self.contract, self.factory.run_log(), "supported-good")
        result = evaluate(self.contract, fixture["run_log"], self.factory.reply(fixture["run_log"]))
        result["run_log_digest"] = "different-attempt"
        with self.assertRaises(ValueError):
            compare_calibration(fixture, result)


if __name__ == "__main__":
    unittest.main()
