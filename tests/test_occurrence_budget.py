"""Outgoing occurrence admission is checked before any wrapper request."""

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from sapi_config_lab.paths import CATALOG
from sapi_config_lab.execute.agency import ContractError, DispatchAudit, execute
from sapi_config_lab.profile import read_bindings


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
            with patch("sapi_config_lab.execute.agency.urlopen") as dispatch:
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
                with patch("sapi_config_lab.execute.agency.urlopen") as dispatch:
                    with self.assertRaises(ContractError):
                        execute(request, read_bindings(CATALOG), "http://unused", 1, audit=audit)
                    dispatch.assert_not_called()

    def test_case_grants_are_the_verifiers_expected_calls_for_every_live_case(self):
        from sapi_config_lab.coordinate.live_evidence import case_budget, live_cohort
        from sapi_config_lab.coordinate.scenarios import SCENARIOS
        from sapi_config_lab.profile import read

        caps = {}
        for scenario, definition in SCENARIOS.items():
            if definition.group == "lifecycle":
                continue
            submission = {"path": definition.config, "cases": definition.cases()}
            for name in live_cohort(scenario, submission):
                config = read(definition.config)
                config["workflow"]["inputs"] = next(
                    case["inputs"] for case in definition.cases()["positive"] if case["name"] == name
                )
                budget = case_budget(scenario, name, config, definition.cases())
                caps[f"{scenario}/{name}"] = budget["max_attempts"]
                if name == "normal-empty":
                    self.assertEqual(budget["operations"], {"ticket.classify": 1})
                if scenario == "revise-answer":
                    self.assertEqual(
                        list(budget["occurrences"]), [f"revise-answer/r1/draft/attempt{n}" for n in (1, 2, 3)]
                    )
        # The same caps the recorded series ran under, now derived rather than stored.
        self.assertEqual(
            caps,
            {
                "invoice-total/original-38000": 0,
                "invoice-total/alternate-values-zero": 0,
                "invoice-total/maximum-safe-total": 0,
                "ticket-routing/high-three-days": 1,
                "ticket-routing/normal-boundary-two": 1,
                "competitor-report/original-evidence": 3,
                "competitor-report/unseen-source-markers": 3,
                "revise-answer/valid-reply": 3,
                "revise-answer/impossible-limit": 3,
                "dual-ledger-closeout/base-ledgers": 0,
                "dual-ledger-closeout/alternate-ledgers": 0,
                "support-review-packet/high-packet": 2,
                "support-review-packet/normal-packet": 2,
                "bulletin-market-brief/base-bulletins": 4,
                "bulletin-market-brief/alternate-bulletins": 4,
                "priority-support-brief/high-brief": 4,
                "priority-support-brief/normal-empty": 1,
            },
        )

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
