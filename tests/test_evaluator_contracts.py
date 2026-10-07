"""Hosted evaluator boundaries distinguish metadata, normalized verdicts and explicit re-scoring options."""

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from sapi_config_lab.coordinate.evaluation import hosted_evaluation, validate_result
from sapi_config_lab.coordinate.providers import ENVIRONMENTS, EVALUATORS, HostedEvaluator, ReevaluationOptions
from sapi_config_lab.coordinate.scenarios import SCENARIOS
from tests.support.pinned import AVAILABLE


class EvaluatorContractTests(unittest.TestCase):
    def test_result_shape_rejects_malformed_results_at_dispatch(self):
        valid = {"execution": True, "acceptance": False, "quality": None}
        bad = [
            None,
            {},
            {**valid, "execution": 1},
            {**valid, "acceptance": "yes"},
            {**valid, "quality": {}},
            {**valid, "quality": {"status": "complete", "score_0_10": float("nan"), "normalized_reward": 0}},
            {**valid, "quality": {"status": "complete", "score_0_10": 11, "normalized_reward": 1}},
            {**valid, "quality": {"status": "not_evaluated", "score_0_10": 0, "normalized_reward": 0}},
        ]
        for result in bad:
            with self.subTest(result=result), tempfile.TemporaryDirectory() as directory:
                record = Path(directory)
                (record / "evidence").mkdir()
                (record / "evidence/trial.json").write_text(json.dumps({"llm_mode": "stub", "submission_sha256": "a"}))
                evaluator = HostedEvaluator(
                    0,
                    lambda scenario, judge, value=result: lambda path: value,
                    lambda scenario, path, output, options, value=result: value,
                    (),
                    30,
                )
                with patch.dict(EVALUATORS, {"autowfbench": evaluator}):
                    with self.assertRaisesRegex(ValueError, "Evaluator|evaluator|Unscored|Complete"):
                        hosted_evaluation(("checkout-recovery",))("checkout-recovery", record)
                self.assertFalse((record / "evaluation/report.json").exists())
        self.assertEqual(validate_result(valid), valid)
        self.assertEqual(validate_result({**valid, "execution": None})["execution"], None)

    def test_options_are_small_and_immutable(self):
        from dataclasses import FrozenInstanceError

        options = ReevaluationOptions(judgement=Path("saved.json"))
        self.assertFalse(hasattr(options, "record"))
        self.assertFalse(hasattr(options, "cases"))
        with self.assertRaises(FrozenInstanceError):
            options.dispatch_judge = True

    @unittest.skipUnless(AVAILABLE, "Requires pinned upstream source")
    def test_environment_metadata_never_loads_or_validates_a_scoring_contract(self):
        with patch("sapi_config_lab.coordinate.providers.freeze_contract", side_effect=AssertionError("scorer loaded")):
            provider = ENVIRONMENTS["autowfbench"]
            for name in ("checkout-recovery", "crm-lead-qualification"):
                scenario = SCENARIOS[name]
                self.assertEqual(provider.limit_seconds(scenario), 120)
                self.assertTrue(provider.task(scenario))
