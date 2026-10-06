"""Reply experiment admission only; no Docker or outgoing model calls."""

import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from sapi_config_lab.coordinate.expansion import ExpansionSeries, RefinementSeries
from sapi_config_lab.coordinate.generate import main as generate
from sapi_config_lab.coordinate.live import main as live
from sapi_config_lab.coordinate.live_evidence import case_budget
from sapi_config_lab.coordinate.packages import stage_tasks
from sapi_config_lab.paths import workspace_root
from sapi_config_lab.profile import read
from tests.test_extension_verification import evidence
from verification.verify import check_execution


class RefinementAdmissionTests(unittest.TestCase):
    def test_fresh_policy_reserves_three_authorings_then_two_bounded_cases(self):
        with tempfile.TemporaryDirectory() as directory:
            series = RefinementSeries(Path(directory))
            self.assertEqual(series.ceilings, {"authoring": 3, "runtime": 6})
            self.assertEqual(json.loads(series.path.read_text())["events"], [])
            with self.assertRaises(ValueError):
                series.reserve("revise-answer", "runtime", "valid-reply", Path(directory) / "early.json")
            for number in (1, 2, 3):
                index = series.reserve("revise-answer", "authoring", str(number), Path(directory) / "generation.json")
                series.finish(index, True)
            for name in ("valid-reply", "impossible-limit"):
                index = series.reserve("revise-answer", "runtime", name, Path(directory) / "live.json")
                series.finish(index, True)
            ledger = json.loads(series.path.read_text())
            self.assertEqual([row["count"] for row in ledger["events"]], [1, 1, 1, 3, 3])
            with self.assertRaises(ValueError):
                series.reserve("revise-answer", "runtime", "valid-reply", Path(directory) / "retry.json")
            with self.assertRaises(ValueError):
                ExpansionSeries(Path(directory))

    def test_unknown_or_failed_attempt_and_old_ledger_do_not_resume(self):
        with tempfile.TemporaryDirectory() as directory:
            series = RefinementSeries(Path(directory))
            reservation = series.reserve("revise-answer", "authoring", "1", Path(directory) / "generation.json")
            with self.assertRaises(ValueError):
                RefinementSeries(Path(directory)).reserve(
                    "revise-answer", "authoring", "2", Path(directory) / "retry.json"
                )
            series.finish(reservation, False)
            with self.assertRaises(ValueError):
                series.reserve("revise-answer", "authoring", "2", Path(directory) / "retry.json")
        with tempfile.TemporaryDirectory() as directory:
            ExpansionSeries(Path(directory))
            before = (Path(directory) / "series.json").read_bytes()
            with self.assertRaises(ValueError):
                RefinementSeries(Path(directory))
            self.assertEqual((Path(directory) / "series.json").read_bytes(), before)

    def test_cli_rejects_missing_caps_without_external_work(self):
        with contextlib.redirect_stderr(io.StringIO()), patch("subprocess.run") as external:
            for args in (
                ["--scenario", "revise-answer"],
                ["--scenario", "revise-answer", "--attempts", "2", "--series-dir", "/unused"],
            ):
                with self.assertRaises(SystemExit):
                    generate(args)
            with self.assertRaises(SystemExit):
                live(["--scenario", "revise-answer", "--stub-report", "/unused"])
            external.assert_not_called()

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
            config = read(workspace_root() / "configs/04-revise-answer.yaml")
            for case in cases["positive"]:
                config["workflow"]["inputs"] = case["inputs"]
                budget = case_budget("revise-answer", case["name"], config)
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
