"""A failed authoring never becomes a dispatched native candidate in summaries."""

from pathlib import Path
import json
import tempfile
import unittest
from sapi_config_lab.experiments.benchmark_series import collect_series


class SeriesTests(unittest.TestCase):
    def test_planned_candidate_without_native_directory_is_not_counted(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            data = {
                "report.json": {
                    "mode": "live",
                    "status": "failed",
                    "authoring": [{"eligible": False}],
                    "candidate_evaluation": {"attempted": 1, "evaluation_missing": 1},
                    "actual_model_attempts": 1,
                },
                "plan.json": {"seed": 0},
                "task-contract.json": {"package": {"definition": {"id": "crm", "version": "1"}, "hashes": {}}},
                "source-manifest.json": {},
            }
            for name, value in data.items():
                (p / name).write_text(json.dumps(value))
            (p / "authoring-1").mkdir()
            (p / "authoring-1/dispatch.json").write_text("{}")
            (p / "authoring-1/authoring.json").write_text(json.dumps({"eligible": False, "duration_seconds": 180}))
            result = collect_series([p / "report.json"])
            self.assertEqual(result["independent_live_candidates"], 0)
            self.assertIsNone(result["runs"][0]["candidate_evaluation"])
            self.assertEqual(result["runs"][0]["stage_summary"]["stages"]["authoring"]["failed"], 1)
            self.assertEqual(result["runs"][0]["stage_summary"]["stages"]["runtime"]["attempted"], 0)


if __name__ == "__main__":
    unittest.main()
