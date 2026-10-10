"""Generation packages and replay selection by a fixed rule; no Docker or model calls."""

import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from sapi_config_lab.coordinate.replay import load_selection, select_submission

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
        if scenario == "invoice-total":
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
                "expected_model": "m",
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
        "wrapper_identity": {"model": "m"},
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

    def test_unbound_authoring_identity_is_refused(self):
        path = generation_run(self.root, {"invoice-total": [("only", "2026-10-04T10:00:00Z", True)]})
        report = json.loads(path.read_text())
        for identity in (None, {}, {"model": None}, {"model": ""}, {"model": 42}, {"model": "   "}):
            with self.subTest(identity=identity):
                changed = dict(report)
                if identity is None:
                    changed.pop("wrapper_identity")
                else:
                    changed["wrapper_identity"] = identity
                save(path, changed)
                with self.assertRaisesRegex(ValueError, "Authoring identity was not bound"):
                    select_submission(path, ("invoice-total",))

    def test_a_different_authoring_model_is_refused_without_replacing_the_first(self):
        path = generation_run(
            self.root,
            {"invoice-total": [("first", "2026-10-04T10:00:00Z", True), ("later", "2026-10-04T10:01:00Z", True)]},
        )
        audit_path = self.root / "jobs/generated-1/first/agent/generation.json"
        audit = json.loads(audit_path.read_text())
        for fields in (
            {"model": "other"},
            {"expected_model": "other"},
            {"model": "other", "expected_model": "other"},
            {"model": None},
            {"expected_model": None},
        ):
            with self.subTest(fields=fields):
                save(audit_path, audit | fields)
                with self.assertRaisesRegex(ValueError, "Authoring model differs from the bound identity"):
                    select_submission(path, ("invoice-total",))

    def test_the_first_started_attempt_is_selected_for_every_scenario(self):
        report = generation_run(
            self.root,
            {
                "invoice-total": [
                    ("z-first", "2026-10-04T10:00:00Z", True),
                    ("a-second", "2026-10-04T10:01:00Z", True),
                ],
                "checkout-recovery": [
                    ("b-later", "2026-10-04T11:05:00Z", False),
                    ("c-first", "2026-10-04T11:00:00Z", True),
                ],
            },
        )
        manifest = select_submission(report, ("invoice-total", "checkout-recovery"))
        self.assertEqual(manifest["rule"], "first-started-attempt")
        self.assertEqual([e["source_trial"] for e in manifest["entries"]], ["z-first", "c-first"])
        # Unselected and failed attempts stay visible.
        self.assertEqual(
            [(a["trial"], a["passed"]) for a in manifest["entries"][1]["attempts"]],
            [("c-first", True), ("b-later", False)],
        )
        save(self.root / "selection.json", manifest)
        loaded = load_selection(self.root / "selection.json", copy_to=self.root / "copied")
        self.assertEqual(set(loaded), {"invoice-total", "checkout-recovery"})
        self.assertTrue((self.root / "copied/invoice-total/submission.yaml").is_file())

    def test_a_failed_first_attempt_is_never_replaced_by_a_later_one(self):
        report = generation_run(
            self.root,
            {"invoice-total": [("first", "2026-10-04T10:00:00Z", False), ("second", "2026-10-04T10:01:00Z", True)]},
        )
        with self.assertRaisesRegex(ValueError, "no later attempt is selected"):
            select_submission(report, ("invoice-total",))

    def test_any_changed_byte_rejects_the_selection(self):
        report = generation_run(self.root, {"invoice-total": [("only", "2026-10-04T10:00:00Z", True)]})
        save(self.root / "selection.json", select_submission(report, ("invoice-total",)))
        cases = self.root / "task-packages/invoice-total/tests/cases.json"
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
