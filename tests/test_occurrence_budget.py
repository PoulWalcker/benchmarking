"""Outgoing occurrence admission is checked before any wrapper request."""

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from sapi_config_lab.paths import CATALOG
from sapi_config_lab.runtime.agency import ContractError, DispatchAudit, execute
from sapi_config_lab.core.profile import read_bindings


class OccurrenceBudgetTests(unittest.TestCase):
    def test_wrong_occurrence_never_spends_an_allowed_operation_budget(self):
        budget = {
            "max_attempts": 1,
            "operations": {"ticket.classify": 1},
            "model": "gpt-6-astra",
            "occurrences": {"priority-support-brief/r1/classify": "ticket.classify"},
        }
        request = {
            "invocation_id": "priority-support-brief/r1/extra/wf/1",
            "operation": "ticket.classify",
            "inputs": {"ticket": {"id": "T1", "text": "late", "days_overdue": 2}},
        }
        with tempfile.TemporaryDirectory() as directory:
            audit = DispatchAudit(Path(directory) / "audit.jsonl", budget)
            with patch("sapi_config_lab.runtime.agency.urlopen") as dispatch:
                with self.assertRaisesRegex(ContractError, "occurrence"):
                    execute(request, read_bindings(CATALOG), "http://unused", 1, audit=audit)
                dispatch.assert_not_called()
            self.assertTrue(audit.failed)
            self.assertEqual([row["event"] for row in audit.records()], ["failure"])

    def test_zero_call_and_expired_cases_cannot_dispatch(self):
        import time

        for budget in (
            {"max_attempts": 0, "operations": {}, "model": "gpt-6-astra", "occurrences": {}},
            {
                "max_attempts": 1,
                "operations": {"ticket.classify": 1},
                "model": "gpt-6-astra",
                "occurrences": {"priority-support-brief/r1/classify": "ticket.classify"},
                "expires_at": time.time() - 1,
            },
        ):
            request = {
                "invocation_id": "priority-support-brief/r1/classify/wf/1",
                "operation": "ticket.classify",
                "inputs": {"ticket": {"id": "T1", "text": "late", "days_overdue": 2}},
            }
            with self.subTest(budget=budget), tempfile.TemporaryDirectory() as directory:
                audit = DispatchAudit(Path(directory) / "audit.jsonl", budget)
                with patch("sapi_config_lab.runtime.agency.urlopen") as dispatch:
                    with self.assertRaises(ContractError):
                        execute(request, read_bindings(CATALOG), "http://unused", 1, audit=audit)
                    dispatch.assert_not_called()

    def test_named_case_admission_covers_all_seventeen_expected_occurrences(self):
        import json
        from sapi_config_lab.experiments.live_evidence import case_budget
        from sapi_config_lab.paths import workspace_root
        from sapi_config_lab.core.scenarios import EXPANSION_SCENARIOS
        from sapi_config_lab.core.profile import read

        root = workspace_root()
        cases = json.loads((root / "verification/cases.json").read_text())
        counts = []
        for scenario, filename in EXPANSION_SCENARIOS.items():
            config = read(root / "configs" / filename)
            for name in cases[scenario]["live_cases"]:
                config["workflow"]["inputs"] = next(
                    case["inputs"] for case in cases[scenario]["positive"] if case["name"] == name
                )
                budget = case_budget(scenario, name, config)
                counts.append(budget["max_attempts"])
                if name == "normal-empty":
                    self.assertEqual(budget["operations"], {"ticket.classify": 1})
        self.assertEqual(counts, [0, 0, 2, 2, 4, 4, 4, 1])
        self.assertEqual(sum(counts), 17)

    def test_refinement_attempts_have_distinct_bounded_native_occurrences(self):
        budget = {
            "max_attempts": 2,
            "operations": {"reply.generate": 2},
            "model": "gpt-6-astra",
            "occurrences": {
                "revise-answer/r1/draft/attempt1": "reply.generate",
                "revise-answer/r1/draft/attempt2": "reply.generate",
            },
        }
        with tempfile.TemporaryDirectory() as directory:
            audit = DispatchAudit(Path(directory) / "audit.jsonl", budget)
            for number in (1, 2):
                audit.begin(
                    {
                        "invocation_id": f"revise-answer/r1/draft/attempt{number}/owned-workflow/7",
                        "operation": "reply.generate",
                        "inputs": {},
                    },
                    "test prompt",
                )
            self.assertEqual([r["attempt"] for r in audit.records()], [1, 2])
            with self.assertRaisesRegex(ContractError, "occurrence"):
                audit.begin(
                    {
                        "invocation_id": "revise-answer/r1/draft/attempt3/owned-workflow/7",
                        "operation": "reply.generate",
                        "inputs": {},
                    },
                    "test prompt",
                )

    def test_ui_budget_rejects_an_unowned_native_workflow_before_dispatch(self):
        import time

        budget = {
            "max_attempts": 1,
            "operations": {"ticket.classify": 1},
            "model": "gpt-6-astra",
            "occurrences": {"priority-support-brief/r1/classify": "ticket.classify"},
            "expires_at": time.time() + 60,
            "workflow_id": "owned-workflow",
        }
        with tempfile.TemporaryDirectory() as directory:
            audit = DispatchAudit(Path(directory) / "audit.jsonl", budget)
            with self.assertRaisesRegex(ContractError, "workflow"):
                audit.begin(
                    {
                        "invocation_id": "priority-support-brief/r1/classify/protected-workflow/1",
                        "operation": "ticket.classify",
                        "inputs": {},
                    },
                    "test prompt",
                )
            self.assertEqual(audit.records(), [])
            audit.begin(
                {
                    "invocation_id": "priority-support-brief/r1/classify/owned-workflow/1",
                    "operation": "ticket.classify",
                    "inputs": {},
                },
                "test prompt",
            )
            self.assertEqual(len(audit.records()), 1)
