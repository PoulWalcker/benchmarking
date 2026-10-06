"""The reply refinement task's package and live grant; no Docker or model calls."""

import json
from pathlib import Path
import tempfile
import unittest

from sapi_config_lab.coordinate.live_evidence import case_budget
from sapi_config_lab.coordinate.packages import stage_tasks
from sapi_config_lab.paths import workspace_root
from sapi_config_lab.profile import read
from tests.test_refinement_verification import evidence
from verification.verify import check_execution


class RefinementAdmissionTests(unittest.TestCase):
    def test_package_is_authoring_without_manual_solution_and_declares_real_refinement(self):
        with tempfile.TemporaryDirectory() as directory:
            package = Path(directory) / "tasks"
            stage_tasks(package, mode="generation", scenarios=("revise-answer",))
            task = package / "revise-answer"
            self.assertFalse((task / "solution").exists())
            self.assertFalse((task / "environment/base.yaml").exists())
            prompt = (task / "instruction.md").read_text()
            self.assertIn("exactly three maximum attempts", prompt)
            self.assertIn("last_accepted_only", prompt)
            self.assertIn("runtime.previous", prompt)
            cases = json.loads((task / "tests/cases.json").read_text())["revise-answer"]
            self.assertEqual(cases["live_cases"], ["valid-reply", "impossible-limit"])
            config = read(workspace_root() / "benchmarks/04-revise-answer/config.yaml")
            for case in cases["positive"]:
                config["workflow"]["inputs"] = case["inputs"]
                budget = case_budget("revise-answer", case["name"], config, cases)
                self.assertEqual(budget["max_attempts"], 3)
                self.assertEqual(budget["operations"], {"reply.generate": 3})
                self.assertEqual(len(budget["occurrences"]), 3)
                self.assertTrue(
                    all(f"attempt{number}" in list(budget["occurrences"])[number - 1] for number in (1, 2, 3))
                )

    def test_expected_exhaustion_passes_the_case_without_an_accepted_reply(self):
        config, run = evidence(["A7842"] * 3, limit=1)
        result = check_execution(
            "revise-answer", config["workflow"]["inputs"], run, "live", config=config, case={"expected": "exhausted"}
        )
        self.assertTrue(result["exhausted"])
        self.assertIsNone(result["output"])
        with self.assertRaises(AssertionError):
            check_execution(
                "revise-answer", config["workflow"]["inputs"], run, "live", config=config, case={"expected": "accepted"}
            )
