"""Outgoing occurrence admission is checked before any wrapper request."""

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from sapi_config_lab.execute.agency import ContractError, DispatchAudit, execute
from sapi_config_lab.profile import read_bindings
from tests.support.refinement import ROOT, definition
from verification.refinement import refinement_model_calls


class OccurrenceBudgetTests(unittest.TestCase):
    def test_wrong_occurrence_never_spends_an_allowed_operation_budget(self):
        budget = {
            "max_attempts": 1,
            "operations": {"probe.advance": 1},
            "model": "gpt-6-astra",
            "occurrences": {"numeric-refinement/r1/draft/attempt1": "probe.advance"},
        }
        request = {
            "invocation_id": "numeric-refinement/r1/extra/attempt1/wf/1",
            "operation": "probe.advance",
            "inputs": {"previous": None, "feedback": []},
        }
        with tempfile.TemporaryDirectory() as directory:
            audit = DispatchAudit(Path(directory) / "audit.jsonl", budget)
            with patch("sapi_config_lab.execute.agency.urlopen") as dispatch:
                with self.assertRaisesRegex(ContractError, "occurrence"):
                    execute(request, read_bindings(ROOT / "bindings.yaml"), "http://unused", 1, audit=audit)
                dispatch.assert_not_called()
            self.assertTrue(audit.failed)
            self.assertEqual([row["event"] for row in audit.records()], ["failure"])

    def test_zero_call_and_expired_cases_cannot_dispatch(self):
        import time

        for budget in (
            {"max_attempts": 0, "operations": {}, "model": "gpt-6-astra", "occurrences": {}},
            {
                "max_attempts": 1,
                "operations": {"probe.advance": 1},
                "model": "gpt-6-astra",
                "occurrences": {"numeric-refinement/r1/draft/attempt1": "probe.advance"},
                "expires_at": time.time() - 1,
            },
        ):
            request = {
                "invocation_id": "numeric-refinement/r1/draft/attempt1/wf/1",
                "operation": "probe.advance",
                "inputs": {"previous": None, "feedback": []},
            }
            with self.subTest(budget=budget), tempfile.TemporaryDirectory() as directory:
                audit = DispatchAudit(Path(directory) / "audit.jsonl", budget)
                with patch("sapi_config_lab.execute.agency.urlopen") as dispatch:
                    with self.assertRaises(ContractError):
                        execute(request, read_bindings(ROOT / "bindings.yaml"), "http://unused", 1, audit=audit)
                    dispatch.assert_not_called()

    def test_refinement_attempts_have_distinct_bounded_native_occurrences(self):
        config = definition()
        config["execution"]["refinement"]["max_attempts"] = 2
        budget = {
            "max_attempts": 2,
            "operations": {"probe.advance": 2},
            "model": "gpt-6-astra",
            "occurrences": refinement_model_calls(config),
        }
        with tempfile.TemporaryDirectory() as directory:
            audit = DispatchAudit(Path(directory) / "audit.jsonl", budget)
            for number in (1, 2):
                audit.begin(
                    {
                        "invocation_id": f"numeric-refinement/r1/draft/attempt{number}/owned-workflow/7",
                        "operation": "probe.advance",
                        "inputs": {},
                    },
                    "test prompt",
                )
            self.assertEqual([r["attempt"] for r in audit.records()], [1, 2])
            with self.assertRaisesRegex(ContractError, "occurrence"):
                audit.begin(
                    {
                        "invocation_id": "numeric-refinement/r1/draft/attempt3/owned-workflow/7",
                        "operation": "probe.advance",
                        "inputs": {},
                    },
                    "test prompt",
                )

    def test_ui_budget_rejects_an_unowned_native_workflow_before_dispatch(self):
        import time

        budget = {
            "max_attempts": 1,
            "operations": {"probe.advance": 1},
            "model": "gpt-6-astra",
            "occurrences": {"numeric-refinement/r1/draft/attempt1": "probe.advance"},
            "expires_at": time.time() + 60,
            "workflow_id": "owned-workflow",
        }
        with tempfile.TemporaryDirectory() as directory:
            audit = DispatchAudit(Path(directory) / "audit.jsonl", budget)
            with self.assertRaisesRegex(ContractError, "workflow"):
                audit.begin(
                    {
                        "invocation_id": "numeric-refinement/r1/draft/attempt1/protected-workflow/1",
                        "operation": "probe.advance",
                        "inputs": {},
                    },
                    "test prompt",
                )
            self.assertEqual(audit.records(), [])
            audit.begin(
                {
                    "invocation_id": "numeric-refinement/r1/draft/attempt1/owned-workflow/1",
                    "operation": "probe.advance",
                    "inputs": {},
                },
                "test prompt",
            )
            self.assertEqual(len(audit.records()), 1)
