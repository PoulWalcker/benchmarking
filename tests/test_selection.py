"""Generation packages and replay selection by a fixed rule; no Docker or model calls."""

import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from sapi_config_lab.coordinate.packages import stage_tasks
from sapi_config_lab.coordinate.replay import load_selection, select_submission
from sapi_config_lab.coordinate.scenarios import all_cases

SOURCES = {"source.py": "frozen"}


def save(path: Path, content) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(content))


def generation_run(root: Path, attempts: dict[str, list[tuple[str, str, bool]]]) -> Path:
    """A recorded generation run: per scenario, (trial, started_at, passed) attempts."""
    save(root / "source-manifest.json", SOURCES)
    trials, prompts, cases = [], {}, {}
    for scenario, rows in attempts.items():
        prompt = "Frozen prompt for " + scenario
        prompts[scenario] = hashlib.sha256(prompt.encode()).hexdigest()
        case_path = root / "task-packages" / scenario / "tests/cases.json"
        save(case_path, {scenario: {"positive": [], "negative": []}})
        cases[scenario] = hashlib.sha256(case_path.read_bytes()).hexdigest()
        for number, (name, started, passed) in enumerate(rows, 1):
            trial = root / f"jobs/generated-{number}" / name
            submission = f"schema: sapi-lab/v0\n# {name}\n".encode()
            digest = hashlib.sha256(submission).hexdigest()
            reward = 1.0 if passed else 0.0
            save(
                trial / "result.json",
                {
                    "task_name": scenario,
                    "verifier_result": {"rewards": {"reward": reward}},
                    "agent_execution": {"started_at": started},
                },
            )
            (trial / "agent").mkdir(parents=True)
            (trial / "agent/prompt.txt").write_text(prompt)
            (trial / "agent/submission.yaml").write_bytes(submission)
            generation = {
                "status": "submitted",
                "generation_calls": 1,
                "repairs": 0,
                "model": "m",
                "observed_tool_markers": [],
            }
            save(
                trial / "agent/generation.json",
                {**generation, "submission_sha256": digest, "prompt_sha256": prompts[scenario]},
            )
            save(
                trial / "verifier/evaluation/report.json",
                {"scenario": scenario, "mode": "stub", "passed": passed, "submission_sha256": digest},
            )
            trials.append({"scenario": scenario, "passed": passed, "result_path": str(trial / "result.json")})
    report = {
        "schema": "sapi-lab-generation/v2",
        "source_unchanged": True,
        "trials": trials,
        "prompt_sha256": prompts,
        "private_cases_sha256": cases,
    }
    save(root / "report.json", report)
    return root / "report.json"


class SelectionTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        patcher = patch("sapi_config_lab.coordinate.replay.source_manifest", return_value=SOURCES)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_the_first_started_attempt_is_selected_for_baseline_and_expansion_alike(self):
        report = generation_run(
            self.root,
            {
                "ticket-routing": [
                    ("z-first", "2026-10-04T10:00:00Z", True),
                    ("a-second", "2026-10-04T10:01:00Z", True),
                ],
                "dual-ledger-closeout": [
                    ("b-later", "2026-10-04T11:05:00Z", False),
                    ("c-first", "2026-10-04T11:00:00Z", True),
                ],
            },
        )
        manifest = select_submission(report, ("ticket-routing", "dual-ledger-closeout"))
        self.assertEqual(manifest["rule"], "first-started-attempt")
        self.assertEqual([e["source_trial"] for e in manifest["entries"]], ["z-first", "c-first"])
        # Unselected and failed attempts stay visible.
        self.assertEqual(
            [(a["trial"], a["passed"]) for a in manifest["entries"][1]["attempts"]],
            [("c-first", True), ("b-later", False)],
        )
        save(self.root / "selection.json", manifest)
        loaded = load_selection(self.root / "selection.json", copy_to=self.root / "copied")
        self.assertEqual(set(loaded), {"ticket-routing", "dual-ledger-closeout"})
        self.assertTrue((self.root / "copied/ticket-routing/submission.yaml").is_file())

    def test_a_failed_first_attempt_is_never_replaced_by_a_later_one(self):
        report = generation_run(
            self.root,
            {"ticket-routing": [("first", "2026-10-04T10:00:00Z", False), ("second", "2026-10-04T10:01:00Z", True)]},
        )
        with self.assertRaisesRegex(ValueError, "no later attempt is selected"):
            select_submission(report, ("ticket-routing",))

    def test_any_changed_byte_rejects_the_selection(self):
        report = generation_run(self.root, {"ticket-routing": [("only", "2026-10-04T10:00:00Z", True)]})
        save(self.root / "selection.json", select_submission(report, ("ticket-routing",)))
        cases = self.root / "task-packages/ticket-routing/tests/cases.json"
        for path in (cases, self.root / "jobs/generated-1/only/agent/submission.yaml"):
            original = path.read_bytes()
            path.write_bytes(original + b"\n")
            with self.subTest(path=path.name), self.assertRaises(ValueError):
                load_selection(self.root / "selection.json")
            path.write_bytes(original)
        load_selection(self.root / "selection.json")
        with patch("sapi_config_lab.coordinate.replay.source_manifest", return_value={"source.py": "edited"}):
            with self.assertRaisesRegex(ValueError, "Sources changed"):
                load_selection(self.root / "selection.json")


class PackageTests(unittest.TestCase):
    def test_generation_package_holds_only_its_prompt_and_given_cases(self):
        scenario = "dual-ledger-closeout"
        fresh = all_cases()[scenario]
        fresh["positive"][0]["inputs"]["domestic_invoices"][0]["id"] = "FRESH-PRIVATE"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "tasks"
            stage_tasks(path, mode="generation", scenarios=(scenario,), cases={scenario: fresh})
            self.assertEqual([p.name for p in path.iterdir()], [scenario])
            self.assertFalse(list(path.rglob("*.yaml")))
            self.assertEqual(json.loads((path / scenario / "tests/cases.json").read_text()), {scenario: fresh})
            for invalid in ((), ("unknown",), (scenario, scenario)):
                with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                    stage_tasks(Path(directory) / "invalid", scenarios=invalid)
            with self.assertRaises(ValueError):
                stage_tasks(Path(directory) / "other", scenarios=("invoice-total",), cases={scenario: fresh})
