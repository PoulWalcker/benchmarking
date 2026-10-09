"""Native trial readers use normalized verdicts without legacy business reports."""

import json
from pathlib import Path
import tempfile
import unittest

from sapi_config_lab.coordinate.benchmark_discovery import select_benchmarks
from sapi_config_lab.coordinate.evaluation import control_passed
from sapi_config_lab.coordinate.live import check_trials
from sapi_config_lab.evaluate.records import NOT_EVALUATED, load_trials, read_report, trial_accepted, trial_result
from sapi_config_lab.evidence import sha256
from sapi_config_lab.paths import workspace_root


class NormalizedRecordTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.job = Path(temporary.name) / "jobs/oracle"
        self.verifier = self.job / "trial/verifier"
        (self.verifier / "evaluation").mkdir(parents=True)
        self.native = {
            "task_name": "invoice-total",
            "verifier_result": {"rewards": {"reward": 1.0}},
            "exception_info": None,
        }
        (self.verifier.parent / "result.json").write_text(json.dumps(self.native))
        (self.verifier / "benchmark.json").write_text("{}")
        self.benchmark = select_benchmarks(workspace_root() / "tasks", ("invoice-total",))[0]

    def test_normalized_only_verdict_reaches_actual_control_reader(self):
        verdict = {"execution": True, "acceptance": True, "quality": None}
        (self.verifier / "result.json").write_text(json.dumps(verdict))
        trial = load_trials(self.job)[0]
        self.assertEqual(trial["result"], verdict)
        self.assertTrue(trial_accepted(trial))
        self.assertTrue(control_passed("oracle", trial, self.benchmark))
        self.assertEqual(trial["rewards"], self.native["verifier_result"]["rewards"])
        self.assertEqual(trial["exception"], self.native["exception_info"])

    def test_malformed_verdict_never_falls_back_to_favorable_legacy_report(self):
        (self.verifier / "evaluation/report.json").write_text(json.dumps({"passed": True}))
        for verdict in (
            None,
            {},
            {"execution": 1, "acceptance": True, "quality": None},
            {"execution": True, "acceptance": True, "quality": {}},
            {"execution": True, "acceptance": True, "quality": None, "harbor_reward": float("nan")},
        ):
            with self.subTest(verdict=verdict):
                (self.verifier / "result.json").write_text(json.dumps(verdict))
                with self.assertRaises(ValueError):
                    load_trials(self.job)

    def test_missing_new_verdict_cannot_reuse_legacy_success(self):
        (self.verifier / "evaluation/report.json").write_text(json.dumps({"passed": True}))
        trial = load_trials(self.job)[0]
        self.assertEqual(trial["result"], NOT_EVALUATED)
        self.assertFalse(trial_accepted(trial))
        self.assertFalse(control_passed("oracle", trial, self.benchmark))

    def test_admission_is_not_evaluated_acceptance(self):
        (self.verifier / "evaluation/report.json").write_text(
            json.dumps({"schema": "sapi-lab-admission/v1", "passed": True})
        )
        trial = load_trials(self.job)[0]
        self.assertEqual(trial["result"], NOT_EVALUATED)
        self.assertFalse(trial_accepted(trial))
        self.assertFalse(control_passed("oracle", trial, self.benchmark))

    def test_recorded_reward_and_exception_remain_separate_from_verdict(self):
        result = {"execution": True, "acceptance": True, "quality": None}
        (self.verifier / "result.json").write_text(json.dumps(result))
        self.native["verifier_result"]["rewards"] = {"reward": 0.732}
        self.native["exception_info"] = {"exception_type": "RecordedFailure", "exception_message": "original"}
        path = self.verifier.parent / "result.json"
        path.write_text(json.dumps(self.native))
        original = path.read_bytes()
        trial = load_trials(self.job)[0]
        self.assertEqual(trial["rewards"], {"reward": 0.732})
        self.assertEqual(trial["exception"], self.native["exception_info"])
        self.assertEqual(trial["result"], result)
        self.assertFalse(trial_accepted(trial))
        self.assertEqual(path.read_bytes(), original)

    def test_saved_report_and_direct_reader_use_authoritative_verdict(self):
        verdict = {"execution": True, "acceptance": False, "quality": None}
        (self.verifier / "result.json").write_text(json.dumps(verdict))
        (self.verifier / "evaluation/report.json").write_text(json.dumps({"passed": True}))
        self.assertEqual(trial_result({"result_path": str(self.verifier.parent / "result.json")}), verdict)
        trial = load_trials(self.job)[0]
        trial["result"] = {"execution": True, "acceptance": True, "quality": None}
        report = self.job.parent.parent / "report.json"
        report.write_text(json.dumps({"trials": [trial]}))
        self.assertEqual(read_report(report)["trials"][0]["result"], verdict)

    def test_normalized_live_gate_checks_metadata_submission_and_observed_cases(self):
        evidence = self.verifier / "evidence"
        evidence.mkdir()
        (evidence / "submission.yaml").write_bytes(self.benchmark.reference.source.read_bytes())
        selected = {self.benchmark.name: {"sha256": sha256(evidence / "submission.yaml")}}
        metadata = {
            "name": self.benchmark.name,
            "runtime_options": {"mode": "live"},
            "submission_sha256": selected[self.benchmark.name]["sha256"],
        }
        (self.verifier / "benchmark.json").write_text(json.dumps(metadata))
        (self.verifier / "result.json").write_text(json.dumps({"execution": True, "acceptance": True, "quality": None}))
        (evidence / "observation.json").write_text(json.dumps({"entries": [{"name": "selected-case"}]}))
        trial = load_trials(self.job)[0]
        arguments = {
            "benchmarks": {self.benchmark.name: self.benchmark.directory},
            "mode": "live",
            "expected_cases": {self.benchmark.name: {"selected-case"}},
        }
        check_trials([trial], selected, **arguments)
        for field, replacement in (
            ("name", "different"),
            ("submission_sha256", "different"),
            ("runtime_options", {"mode": "stub"}),
        ):
            changed = {**trial, "benchmark": {**metadata, field: replacement}}
            with self.subTest(field=field), self.assertRaises(ValueError):
                check_trials([changed], selected, **arguments)
        (evidence / "observation.json").write_text(json.dumps({"entries": [{"name": "other-case"}]}))
        with self.assertRaisesRegex(ValueError, "subset changed"):
            check_trials([trial], selected, **arguments)

    def test_cached_historical_admission_is_unevaluated_without_rewriting_report(self):
        trial = {
            "task_name": self.benchmark.name,
            "result_path": str(self.verifier.parent / "result.json"),
            "acceptance": {"schema": "sapi-lab-admission/v1", "passed": True},
            "result": {"execution": None, "acceptance": True, "quality": None},
            "rewards": {"reward": 1.0},
            "exception": None,
        }
        (self.verifier / "benchmark.json").unlink()
        report = self.job.parent.parent / "report.json"
        report.write_text(json.dumps({"trials": [trial]}))
        original = report.read_bytes()
        self.assertEqual(read_report(report)["trials"][0]["result"], NOT_EVALUATED)
        self.assertFalse(control_passed("oracle", trial, self.benchmark))
        self.assertEqual(report.read_bytes(), original)

    def test_admission_cannot_carry_evaluated_facts(self):
        (self.verifier / "evaluation/report.json").write_text(
            json.dumps({"schema": "sapi-lab-admission/v1", "passed": True})
        )
        (self.verifier / "result.json").write_text(json.dumps({"execution": True, "acceptance": True, "quality": None}))
        with self.assertRaisesRegex(ValueError, "Admission"):
            load_trials(self.job)
