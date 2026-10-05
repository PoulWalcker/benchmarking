"""Opt-in packaging and exact-byte private fixture/submission replay."""

import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from sapi_config_lab.interfaces.tasks import stage_tasks
from sapi_config_lab.interfaces.replay import expansion_selection, load_selection
from sapi_config_lab.paths import workspace_root

ROOT = workspace_root()
SCENARIO = "dual-ledger-closeout"


class ExpansionPackagingTests(unittest.TestCase):
    def test_selected_generation_package_contains_only_its_prompt_and_cases(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "tasks"
            stage_tasks(path, mode="generation", scenarios=(SCENARIO,))
            self.assertEqual([p.name for p in path.iterdir()], [SCENARIO])
            self.assertFalse(list(path.rglob("*.yaml")))
            self.assertIn("OPERATION CATALOG", (path / SCENARIO / "instruction.md").read_text())
            self.assertEqual(set(json.loads((path / SCENARIO / "tests/cases.json").read_text())), {SCENARIO})
            for invalid in ((), ("unknown",), (SCENARIO, SCENARIO)):
                with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                    stage_tasks(Path(directory) / "invalid", scenarios=invalid)
            self.assertFalse((Path(directory) / "invalid").exists())

    def test_replay_preserves_private_fixture_bytes_and_rejects_their_drift(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cases = root / "private.json"
            original = json.loads((ROOT / "verification/cases.json").read_text())[SCENARIO]
            original["positive"][0]["inputs"]["domestic_invoices"][0]["id"] = "FRESH-PRIVATE"
            cases.write_text(json.dumps({SCENARIO: original}, indent=4) + "\n")
            submission = ROOT / "configs/06-dual-ledger-closeout.yaml"
            selected = {
                SCENARIO: {
                    "path": submission,
                    "sha256": hashlib.sha256(submission.read_bytes()).hexdigest(),
                    "cases_path": cases,
                    "cases_sha256": hashlib.sha256(cases.read_bytes()).hexdigest(),
                }
            }
            stage_tasks(root / "replay", mode="replay", submissions=selected, scenarios=(SCENARIO,))
            self.assertEqual((root / "replay" / SCENARIO / "tests/cases.json").read_bytes(), cases.read_bytes())
            cases.write_text(cases.read_text() + "\n")
            with self.assertRaisesRegex(ValueError, "fixture hash"):
                stage_tasks(root / "drift", mode="replay", submissions=selected, scenarios=(SCENARIO,))

    def test_selection_uses_first_submitted_attempt_and_pins_every_private_byte(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)

            def save(path, content):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(content))

            frozen = {"source.py": "frozen"}
            save(root / "frozen-core.json", frozen)
            case_path = root / "task-packages" / SCENARIO / "tests/cases.json"
            save(case_path, {SCENARIO: {"positive": [], "negative": []}})
            prompt = "Frozen public authoring prompt"
            prompt_hash = hashlib.sha256(prompt.encode()).hexdigest()
            trials = []
            for name, started in (("z-first", "2026-10-04T10:00:00Z"), ("a-second", "2026-10-04T10:01:00Z")):
                trial = root / "jobs/generated" / name
                save(
                    trial / "result.json",
                    {
                        "task_name": SCENARIO,
                        "verifier_result": {"rewards": {"reward": 1.0}},
                        "agent_execution": {"started_at": started},
                    },
                )
                (trial / "agent").mkdir()
                (trial / "agent/prompt.txt").write_text(prompt)
                submission = b"schema: sapi-lab/v0\n# preserved exact test bytes\n"
                (trial / "agent/submission.yaml").write_bytes(submission)
                digest = hashlib.sha256(submission).hexdigest()
                save(
                    trial / "agent/generation.json",
                    {
                        "status": "submitted",
                        "generation_calls": 1,
                        "repairs": 0,
                        "model": "gpt-6-astra",
                        "observed_tool_markers": [],
                        "submission_sha256": digest,
                        "prompt_sha256": prompt_hash,
                    },
                )
                save(
                    trial / "verifier/report.json",
                    {"scenario": SCENARIO, "mode": "stub", "passed": True, "submission_sha256": digest},
                )
                trials.append({"scenario": SCENARIO, "passed": True, "result_path": str(trial / "result.json")})
            report = {
                "schema": "sapi-lab-generation/v1",
                "status": "passed",
                "frozen_core_unchanged": True,
                "attempts_per_task": 2,
                "trials": trials,
                "prompt_sha256": {SCENARIO: prompt_hash},
                "private_cases_sha256": {SCENARIO: hashlib.sha256(case_path.read_bytes()).hexdigest()},
            }
            save(root / "report.json", report)
            with patch("sapi_config_lab.core.provenance.source_manifest", return_value=frozen):
                manifest = expansion_selection(root / "report.json", SCENARIO)
                self.assertEqual(manifest["entries"][0]["source_trial"], "z-first")
                save(root / "selection.json", manifest)
                loaded = load_selection(root / "selection.json", scenarios=(SCENARIO,))
                self.assertEqual(loaded[SCENARIO]["cases_path"], case_path.resolve())
                case_path.write_text(case_path.read_text() + "\n")
                with self.assertRaisesRegex(ValueError, "fixture hash"):
                    load_selection(root / "selection.json", scenarios=(SCENARIO,))
