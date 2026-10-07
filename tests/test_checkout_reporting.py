"""Admission of observed native output follows the frozen benchmark protocol."""

import unittest

from sapi_config_lab.coordinate.scenarios import SCENARIOS
from sapi_config_lab.execute.hosting import terminal_submission as admitted

CHECKOUT = SCENARIOS["checkout-recovery"].artifact
CRM = SCENARIOS["crm-lead-qualification"].artifact


def terminal_submission(record, elapsed, run_id, artifact=CHECKOUT):
    return admitted(record, elapsed, run_id, limit=120, artifact=artifact)


class TerminalSubmissionTests(unittest.TestCase):
    def test_valid_native_output_is_submitted_verbatim(self):
        output = {"final_answer": "Recorded result", "incident_summary": "# Incident\n\nObserved evidence.\n"}
        reason, submission = terminal_submission({"status": "success", "output": output}, 119.9, "run-one")
        self.assertEqual(reason, "completed")
        self.assertEqual(submission["run_id"], "run-one")
        self.assertEqual(submission["final_answer"], output["final_answer"])
        self.assertEqual(submission["artifacts"][0]["content"], output["incident_summary"])
        self.assertEqual(submission["artifacts"][0]["name"], "incident-summary.md")

    def test_native_success_without_valid_submission_is_not_completion(self):
        for output in (
            None,
            "I succeeded",
            {},
            {"final_answer": "Success"},
            {"final_answer": 7, "incident_summary": "x"},
        ):
            with self.subTest(output=output):
                reason, submission = terminal_submission({"status": "success", "output": output}, 1, "run-one")
                self.assertEqual(reason, "protocol_error")
                self.assertIsNone(submission)

    def test_timeout_never_admits_a_late_successful_answer(self):
        record = {"status": "success", "output": {"final_answer": "Success", "incident_summary": "All fixed"}}
        self.assertEqual(terminal_submission(record, 120.001, "run-one"), ("timeout", None))

    def test_runtime_failure_cannot_be_replaced_by_reported_success(self):
        for status in ("error", "compile_error", "missing_submission"):
            with self.subTest(status=status):
                record = {"status": status, "output": {"final_answer": "Success", "incident_summary": "All fixed"}}
                self.assertEqual(terminal_submission(record, 1, "run-one"), ("solution_failed", None))

    def test_crm_result_does_not_require_checkout_artifact(self):
        record = {"status": "success", "output": {"final_answer": "actual receipts"}}
        reason, submission = terminal_submission(record, 1, "case", CRM)
        self.assertEqual(reason, "completed")
        self.assertEqual(submission["artifacts"], [])
        self.assertEqual(terminal_submission(record, 1, "case")[0], "protocol_error")

    def test_empty_explanation_remains_semantic_judge_responsibility(self):
        # Upstream allows empty strings: do not secretly turn prose quality into
        # an additional deterministic benchmark gate.
        record = {"status": "success", "output": {"final_answer": "", "incident_summary": ""}}
        reason, submission = terminal_submission(record, 1, "run-one")
        self.assertEqual(reason, "completed")
        self.assertEqual(submission["final_answer"], "")
        self.assertEqual(submission["artifacts"][0]["content"], "")


if __name__ == "__main__":
    unittest.main()
