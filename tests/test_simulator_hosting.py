"""Declared score gates and native result projection; no provider registry."""

import unittest

from sapi_config_lab.coordinate.evaluation import HOSTED_REPORT, control_passed
from sapi_config_lab.coordinate.native_tasks import policy, select_tasks
from sapi_config_lab.paths import workspace_root


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


class ScoredControlTests(unittest.TestCase):
    """A declared reference reward makes the hosted oracle a scored control."""

    def setUp(self):
        self.benchmark = select_tasks(workspace_root() / "tasks", ("checkout-recovery",))[0]

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
        self.assertEqual(policy(self.benchmark).get("reference_reward"), 0.732)
        complete = hosted_report("checkout-recovery", True, 0.732)["result"]["quality"]
        self.assertTrue(
            control_passed(
                "oracle", self.trial(complete), reference_reward=policy(self.benchmark).get("reference_reward")
            )
        )
        self.assertFalse(
            control_passed("oracle", self.trial(None), reference_reward=policy(self.benchmark).get("reference_reward"))
        )
        self.assertFalse(
            control_passed(
                "oracle",
                self.trial({**complete, "status": "unscored"}),
                reference_reward=policy(self.benchmark).get("reference_reward"),
            )
        )
        self.assertFalse(
            control_passed(
                "oracle",
                self.trial(complete, reward=0.5),
                reference_reward=policy(self.benchmark).get("reference_reward"),
            )
        )

    def test_unscored_nop_projection_uses_declared_reference_scoring_policy(self):
        unscored = {"status": "unscored", "score_0_10": None, "normalized_reward": None}
        trial = self.trial(unscored, reward=0.0)
        trial["result"]["acceptance"] = False
        trial["result"]["execution"] = None
        invoice = select_tasks(workspace_root() / "tasks", ("invoice-total",))[0]
        self.assertTrue(control_passed("nop", trial, reference_reward=policy(invoice).get("reference_reward")))
        self.assertFalse(control_passed("nop", trial, reference_reward=policy(self.benchmark).get("reference_reward")))
        trial["exception"] = {"exception_type": "RuntimeError"}
        self.assertFalse(control_passed("nop", trial, reference_reward=policy(invoice).get("reference_reward")))
        trial["exception"] = None
        trial["rewards"] = {"reward": 1.0}
        self.assertFalse(control_passed("nop", trial, reference_reward=policy(invoice).get("reference_reward")))


class VerifierResultTests(unittest.TestCase):
    def test_lifecycle_rows_without_a_case_kind_still_give_a_result(self):
        from sapi_config_lab.coordinate.evaluation import verifier_result

        report = {"passed": True, "cases": [{"name": "callback-and-cron", "passed": True, "native_executions": []}]}
        self.assertEqual(verifier_result(report, None), {"execution": None, "acceptance": True, "quality": None})
