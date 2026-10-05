"""Checks that the experiment does not substitute references or forgive failures."""

import json
import hashlib
from pathlib import Path
import tempfile
import unittest

from sapi_config_lab.interfaces.generation.common import ROOT, audit_stderr, prepare_tasks, summarize_trials
from sapi_config_lab.interfaces.tasks import SCENARIOS, stage_tasks


class GenerationTests(unittest.TestCase):
    def test_replay_packages_preserve_exact_selected_bytes_and_reject_bad_selection(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            submissions = {}
            for scenario in SCENARIOS:
                path = root / (scenario + ".yaml")
                path.write_bytes(b"# authored bytes\r\nworkflow: {}\r\n")
                submissions[scenario] = {"path": path, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
            stage_tasks(root / "tasks", mode="replay", submissions=submissions)
            for scenario, item in submissions.items():
                self.assertEqual(
                    (root / "tasks" / scenario / "environment/base.yaml").read_bytes(), item["path"].read_bytes()
                )
            for selected in (
                {},
                {**submissions, "unknown": submissions["invoice-total"]},
                {**submissions, "invoice-total": {"path": submissions["invoice-total"]["path"], "sha256": "0" * 64}},
            ):
                with self.assertRaises(ValueError):
                    stage_tasks(root / "invalid", mode="replay", submissions=selected)
                self.assertFalse((root / "invalid").exists())

    def test_packages_have_no_solutions_or_reference_configs(self):
        with tempfile.TemporaryDirectory() as directory:
            tasks = Path(directory) / "tasks"
            hashes = prepare_tasks(tasks, "test-n8n:fixed")
            self.assertEqual(len(hashes), 3)
            for task in tasks.iterdir():
                self.assertFalse((task / "solution").exists())
                self.assertEqual(list(task.rglob("*.yaml")), [])
                prompt = (task / "instruction.md").read_text()
                for config in (ROOT / "configs").glob("*.yaml"):
                    self.assertNotIn(config.read_text().strip(), prompt)
                self.assertNotIn((ROOT / "verification/verify.py").read_text(), prompt)
                self.assertEqual(
                    (task / "tests/verify.py").read_bytes(), (ROOT / "verification/verify.py").read_bytes()
                )

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
            (trial / "verifier").mkdir()
            (trial / "result.json").write_text(
                json.dumps({"task_name": "invoice-total", "verifier_result": {"rewards": {"reward": 1.0}}})
            )
            (trial / "agent/generation.json").write_text('{"status":"submitted"}')
            (trial / "verifier/report.json").write_text('{"passed":false,"cases":[]}')
            result = summarize_trials(Path(directory))[0]
            self.assertFalse(result["passed"])
            self.assertEqual(result["failure_stage"], "yaml_parsing_or_definition")

    def test_compile_failure_is_reported_separately(self):
        with tempfile.TemporaryDirectory() as directory:
            trial = Path(directory) / "invoice_trial"
            (trial / "agent").mkdir(parents=True)
            (trial / "verifier/cases/sample").mkdir(parents=True)
            (trial / "result.json").write_text(
                json.dumps({"task_name": "invoice-total", "verifier_result": {"rewards": {"reward": 0.0}}})
            )
            (trial / "agent/generation.json").write_text('{"status":"submitted"}')
            (trial / "verifier/report.json").write_text(
                json.dumps(
                    {
                        "passed": False,
                        "cases": [{"passed": False, "kind": "positive", "artifacts": "/logs/verifier/cases/sample"}],
                    }
                )
            )
            (trial / "verifier/cases/sample/case.json").write_text('{"status":"compile_error"}')
            self.assertEqual(summarize_trials(Path(directory))[0]["failure_stage"], "compilation")


if __name__ == "__main__":
    unittest.main()
