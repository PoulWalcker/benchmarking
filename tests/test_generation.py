"""Checks that the experiment does not substitute references or forgive failures."""

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from sapi_config_lab.coordinate.generate import summarize_trials
from sapi_config_lab.evaluate.records import NOT_EVALUATED
from sapi_config_lab.harbor_integration.yaml_agent import audit_stderr
from tests.test_selection import generation_run, save


class GenerationTests(unittest.TestCase):
    def test_acceptance_requires_independent_evaluation_of_the_submission(self):
        for kind in ("admitted", "accepted", "rejected", "not-submitted"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                scenario = "checkout-recovery" if kind == "admitted" else "invoice-total"
                generation_run(root, {scenario: [("one", "2026-10-10T00:00:00Z", kind != "rejected")]})
                trial = root / "jobs/generated-1/one"
                report_path = trial / "verifier/evaluation/report.json"
                report = json.loads(report_path.read_text())
                save(trial / "verifier/native-task.json", {"submission_sha256": report["submission_sha256"]})
                verdict = {**NOT_EVALUATED, "execution": True, "acceptance": kind != "rejected"}
                if kind == "admitted":
                    report["schema"] = "sapi-lab-admission/v1"
                    verdict = dict(NOT_EVALUATED)
                elif kind == "not-submitted":
                    save(trial / "agent/generation.json", {"status": "generation_error"})
                    verdict["acceptance"] = False
                save(report_path, report)
                save(trial / "verifier/result.json", verdict)
                row = summarize_trials(root / "jobs/generated-1")[0]
                self.assertIs(row["accepted"], {"accepted": True, "rejected": False}.get(kind))
                self.assertIs(row["admitted"], kind == "admitted")
                self.assertIs(row["passed"], kind in {"accepted", "admitted"})
                self.assertEqual(row["result"], verdict)

    def test_unavailable_acceptance_stays_null_without_changing_the_stub_gate(self):
        for fault in (
            "reward-only",
            "legacy",
            "missing-verdict",
            "null",
            "not-run-quality",
            "identity",
            "missing-identity",
            "missing-hash",
            "exception",
            "partial",
        ):
            with self.subTest(fault=fault), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                generation_run(root, {"invoice-total": [("one", "2026-10-10T00:00:00Z", True)]})
                trial = root / "jobs/generated-1/one"
                report_path = trial / "verifier/evaluation/report.json"
                report = json.loads(report_path.read_text())
                save(trial / "verifier/native-task.json", {"submission_sha256": report["submission_sha256"]})
                verdict = {**NOT_EVALUATED, "execution": True, "acceptance": True}
                if fault in {"null", "not-run-quality"}:
                    verdict["acceptance"] = None
                    if fault == "not-run-quality":
                        verdict["quality"] = {"status": "not_run", "score_0_10": None, "normalized_reward": None}
                save(trial / "verifier/result.json", verdict)
                if fault in {"reward-only", "legacy", "missing-verdict"}:
                    (trial / "verifier/result.json").unlink()
                if fault in {"legacy", "missing-identity"}:
                    (trial / "verifier/native-task.json").unlink()
                elif fault == "identity":
                    save(trial / "verifier/native-task.json", {"submission_sha256": "another-submission"})
                elif fault == "missing-hash":
                    save(trial / "verifier/native-task.json", {})
                    save(trial / "agent/generation.json", {"status": "submitted"})
                elif fault == "reward-only":
                    report_path.unlink()
                elif fault == "exception":
                    native = json.loads((trial / "result.json").read_text())
                    native["exception_info"] = {"exception_type": "InfrastructureError"}
                    save(trial / "result.json", native)
                elif fault == "partial":
                    (trial / "result.json").unlink()
                    save(trial / "config.json", {"task": {"path": "tasks/invoice-total"}})
                    (trial / "verifier/result.json").unlink()
                row = summarize_trials(root / "jobs/generated-1")[0]
                self.assertIsNone(row["accepted"])
                self.assertFalse(row["admitted"])
                self.assertIs(row["passed"], fault in {"legacy", "identity", "missing-identity", "missing-hash"})

    def test_invalid_normalized_result_at_the_summary_seam_cannot_prove_acceptance(self):
        with tempfile.TemporaryDirectory() as temporary:
            trial = {
                "result_path": str(Path(temporary) / "one/result.json"),
                "task_name": "invoice-total",
                "acceptance": None,
                "exception": None,
                "rewards": {"reward": 1.0},
                "verdict_path": "verifier/result.json",
                "native_task": {"submission_sha256": "submission"},
                "result": {"execution": True, "acceptance": "true", "quality": None},
            }
            save(
                Path(temporary) / "one/agent/generation.json",
                {"status": "submitted", "submission_sha256": "submission"},
            )
            with patch("sapi_config_lab.coordinate.generate.load_trials", return_value=[trial]):
                self.assertIsNone(summarize_trials(Path(temporary))[0]["accepted"])

    def test_malformed_verdict_files_remain_fail_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            generation_run(root, {"invoice-total": [("one", "2026-10-10T00:00:00Z", True)]})
            save(root / "jobs/generated-1/one/verifier/result.json", {"acceptance": True})
            with self.assertRaisesRegex(ValueError, "requires execution, acceptance and quality"):
                summarize_trials(root / "jobs/generated-1")

    def test_independent_acceptance_does_not_depend_on_reward_or_quality(self):
        for quality in (None, {"status": "not_run", "score_0_10": None, "normalized_reward": None}):
            with self.subTest(quality=quality), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                generation_run(root, {"invoice-total": [("one", "2026-10-10T00:00:00Z", False)]})
                trial = root / "jobs/generated-1/one"
                report = json.loads((trial / "verifier/evaluation/report.json").read_text())
                save(trial / "verifier/native-task.json", {"submission_sha256": report["submission_sha256"]})
                verdict = {"execution": True, "acceptance": True, "quality": quality}
                save(trial / "verifier/result.json", verdict)
                row = summarize_trials(root / "jobs/generated-1")[0]
                self.assertIs(row["accepted"], True)
                self.assertEqual(row["rewards"], {"reward": 0.0})
                self.assertEqual(row["result"], verdict)

    def test_observed_tools_cannot_be_called_clean(self):
        for text in ("exec\ncat a.yaml", "exec /bin/zsh -lc pwd", "tool mcp.read", "file update\nM x"):
            self.assertTrue(audit_stderr(text)["observed_tool_markers"])
        self.assertEqual(
            audit_stderr("model: example-model\nmcp startup: no servers\nthinking\nanswer\ncodex\nx")[
                "observed_tool_markers"
            ],
            [],
        )

    def test_audit_does_not_store_raw_stderr(self):
        audit = audit_stderr("model: example-model\nprivate-string\ntokens used\n1,234\n")
        self.assertNotIn("private-string", json.dumps(audit))
        self.assertEqual(audit["cli_reported_tokens"], 1234)

    def test_reward_alone_is_not_success(self):
        with tempfile.TemporaryDirectory() as directory:
            trial = Path(directory) / "invoice_trial"
            (trial / "agent").mkdir(parents=True)
            (trial / "verifier/evaluation").mkdir(parents=True)
            (trial / "result.json").write_text(
                json.dumps({"task_name": "invoice-total", "verifier_result": {"rewards": {"reward": 1.0}}})
            )
            (trial / "agent/generation.json").write_text('{"status":"submitted"}')
            (trial / "verifier/evaluation/report.json").write_text('{"passed":false,"cases":[]}')
            result = summarize_trials(Path(directory))[0]
            self.assertFalse(result["passed"])
            self.assertEqual(result["failure_stage"], "yaml_parsing_or_definition")

    def test_compile_failure_is_reported_separately(self):
        with tempfile.TemporaryDirectory() as directory:
            trial = Path(directory) / "invoice_trial"
            (trial / "agent").mkdir(parents=True)
            (trial / "verifier/evidence/cases/sample").mkdir(parents=True)
            (trial / "verifier/evaluation").mkdir()
            (trial / "result.json").write_text(
                json.dumps({"task_name": "invoice-total", "verifier_result": {"rewards": {"reward": 0.0}}})
            )
            (trial / "agent/generation.json").write_text('{"status":"submitted"}')
            (trial / "verifier/evaluation/report.json").write_text(
                json.dumps(
                    {
                        "passed": False,
                        "cases": [
                            {"passed": False, "kind": "positive", "artifacts": "/logs/verifier/evidence/cases/sample"}
                        ],
                    }
                )
            )
            (trial / "verifier/evidence/cases/sample/case.json").write_text('{"status":"compile_error"}')
            self.assertEqual(summarize_trials(Path(directory))[0]["failure_stage"], "compilation")


if __name__ == "__main__":
    unittest.main()
