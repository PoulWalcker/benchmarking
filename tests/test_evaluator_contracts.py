"""Descriptor metadata stays separate from normalized evaluator verdicts."""

from dataclasses import asdict
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from sapi_config_lab.benchmark import discover_benchmarks, load_benchmark
from sapi_config_lab.benchmark_loading import freeze_identity
from sapi_config_lab.coordinate.evaluation import reevaluate_benchmark, validate_result
from sapi_config_lab.paths import workspace_root


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
        root = workspace_root() / "tasks"
        benchmark = load_benchmark(root, root / "checkout-recovery")
        identity = freeze_identity(benchmark, {})
        for result in bad:
            with self.subTest(result=result), tempfile.TemporaryDirectory() as directory:
                record = Path(directory)
                evidence = record / "evidence"
                evidence.mkdir()
                raw = b'{"llm_mode":"stub","submission_sha256":"a"}'
                (evidence / "trial.json").write_bytes(raw)
                (record / "benchmark.json").write_text(
                    json.dumps({"name": benchmark.name, "options": {}, "identity": asdict(identity), "core_files": {}})
                )
                evaluate = Mock(return_value=result)
                with patch(
                    "sapi_config_lab.coordinate.evaluation.load_entrypoints",
                    return_value=SimpleNamespace(evaluate=evaluate),
                ):
                    with self.assertRaisesRegex(ValueError, "Evaluator|evaluator|Unscored|Complete"):
                        reevaluate_benchmark(record, record / "derived", None)
                evaluate.assert_called_once()
                self.assertFalse((record / "evaluation/report.json").exists())
                self.assertEqual((evidence / "trial.json").read_bytes(), raw)
        self.assertEqual(validate_result(valid), valid)
        self.assertIsNone(validate_result({**valid, "execution": None})["execution"])
        self.assertEqual(
            validate_result({"execution": None, "acceptance": None, "quality": None}),
            {"execution": None, "acceptance": None, "quality": None},
        )

    def test_environment_metadata_never_loads_or_validates_a_scoring_contract(self):
        with (
            patch("sapi_config_lab.benchmark_loading.freeze_identity", side_effect=AssertionError("scorer loaded")),
            patch("importlib.import_module", side_effect=AssertionError("benchmark code imported")),
        ):
            benchmarks = discover_benchmarks(workspace_root() / "tasks")
            benchmark = next(item for item in benchmarks if item.name == "checkout-recovery")
            self.assertEqual(benchmark.config["deadline_seconds"], 120)
            instruction = next(item.source for item in benchmark.public if item.destination == "instruction.md")
            self.assertTrue(instruction.read_text())
            self.assertIn("prepare", benchmark.entrypoints)
