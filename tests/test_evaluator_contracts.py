"""Independent evaluator verdicts reject malformed facts."""

import unittest

from sapi_config_lab.coordinate.evaluation import validate_result


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
            with self.subTest(result=result), self.assertRaises(ValueError):
                validate_result(result)
        self.assertEqual(validate_result(valid), valid)
        self.assertIsNone(validate_result({**valid, "execution": None})["execution"])
        self.assertEqual(
            validate_result({"execution": None, "acceptance": None, "quality": None}),
            {"execution": None, "acceptance": None, "quality": None},
        )
