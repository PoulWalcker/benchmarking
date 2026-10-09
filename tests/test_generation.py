"""Checks that the experiment does not substitute references or forgive failures."""

import json
from pathlib import Path
import tempfile
import unittest

from sapi_config_lab.coordinate.generate import summarize_trials
from sapi_config_lab.harbor_integration.yaml_agent import audit_stderr


class GenerationTests(unittest.TestCase):
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
