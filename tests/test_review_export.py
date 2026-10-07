"""Hosted review discovery uses authoritative host records, including absent quality."""

import json
from pathlib import Path
import tempfile
import unittest

from sapi_config_lab.coordinate.runs import load_trials
from sapi_config_lab.evaluate.review_export import HOSTED_SCHEMA, export_trial, find_evaluations


class HostedReviewTests(unittest.TestCase):
    def test_host_report_overrides_container_copy_and_never_rewrites_rewards(self):
        for quality in (None, {"status": "complete", "score_0_10": 7.32, "normalized_reward": 0.732}):
            with self.subTest(quality=quality), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                trial = root / "jobs/oracle/trial"
                local = trial / "verifier/evaluation"
                local.mkdir(parents=True)
                (local / "evaluation.json").write_text('{"schema": "stale"}')
                reward = trial / "verifier/reward.json"
                reward.write_text('{"reward": 0.732}')
                (trial / "result.json").write_text(json.dumps({"task_name": "crm-lead-qualification"}))
                host = root / "environments/oracle/crm-lead-qualification"
                (host / "evaluation").mkdir(parents=True)
                source = host / "evaluation/report.json"
                source.write_text(
                    json.dumps(
                        {
                            "schema": HOSTED_SCHEMA,
                            "native_execution": True,
                            "terminal_completion": False,
                            "result": {"execution": False, "acceptance": True, "quality": quality},
                        }
                    )
                )
                (host / "evaluation/evaluation.json").write_text("{}")
                before = source.read_bytes(), reward.read_bytes()
                rows = load_trials(trial.parent, {"crm-lead-qualification": host})
                self.assertEqual(rows[0]["evaluation_path"], str(source))
                (root / "report.json").write_text(json.dumps({"oracle": {"trials": rows}}))
                self.assertEqual(find_evaluations(root / "jobs"), [(trial, source)])
                result = export_trial(source, trial=trial, force=False, rewards=True, dry_run=False)
                self.assertEqual(result["written"], ["analysis.md"])
                view = (trial / "analysis.md").read_text()
                self.assertIn("**Acceptance:** True", view)
                self.assertIn("**Terminal completion:** False", view)
                self.assertIn("Evaluator details", view)
                self.assertIn("not provided" if quality is None else "0.732", view)
                self.assertEqual((source.read_bytes(), reward.read_bytes()), before)
                rows[0].pop("evaluation_path")
                (root / "report.json").write_text(json.dumps({"oracle": {"trials": rows}}))
                self.assertEqual(find_evaluations(root / "jobs"), [(trial, source)])

    def test_fixture_detail_discovery_without_a_run_report_is_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            jobs = Path(directory)
            source = jobs / "oracle/trial/verifier/evaluation/evaluation.json"
            source.parent.mkdir(parents=True)
            source.write_text("{}")
            self.assertEqual(find_evaluations(jobs), [(source.parents[2], source)])
