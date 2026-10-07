"""A new ordinary fixture needs independent evaluator code, not new generic dispatch branches."""

from functools import partial
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import yaml

from sapi_config_lab.coordinate.cases import run_case
from sapi_config_lab.coordinate.scenarios import SCENARIOS
from sapi_config_lab.profile import read
from tests.support.native import SimulatedN8n
from tests.support.verifying import verify_with_runner
from verification import roles, verify
from verification.contracts import Rejected, equal
from verification.fixture_evaluators import EVALUATORS, FixtureEvaluator, fresh_cases


class FixtureOwnershipTests(unittest.TestCase):
    def test_a_new_fixture_uses_standard_plan_evidence_and_corruption_checks(self):
        source = SCENARIOS["invoice-total"]
        config = read(source.config)
        config["workflow"]["id"] = config["activation"]["workflow_ref"]["id"] = "new-invoice-task"
        calls = []

        def independent_check(inputs, observation, mode, *, case):
            calls.append(case["name"])
            invoices = inputs["invoices"]
            equal(
                observation.final["output"],
                {
                    "total_minor": sum(row["amount_minor"] for row in invoices),
                    "currency": invoices[0]["currency"],
                    "invoice_count": len(invoices),
                },
                "Wrong new-task result",
            )

        contract_file = source.directory / "evaluation/contract.json"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            submission = root / "config.yaml"
            submission.write_text(yaml.safe_dump(config))
            with (
                patch.dict(EVALUATORS, {"new-invoice-task": FixtureEvaluator(independent_check)}),
                patch.object(roles, "scenario_file", return_value=contract_file),
            ):
                report = verify_with_runner(
                    verify,
                    "new-invoice-task",
                    submission,
                    root / "run",
                    runner=partial(run_case, backend=SimulatedN8n()),
                    cases=source.cases(),
                )
                self.assertTrue(report["passed"], report)
                self.assertEqual(
                    verify.expected_model_calls("new-invoice-task", config, config["workflow"]["inputs"]), {}
                )
            self.assertTrue(calls)
            self.assertTrue(any(row["kind"] == "mutated-generated-workflow" for row in report["cases"]))

    def test_freshness_requires_an_explicit_evaluator_contract(self):
        with self.assertRaisesRegex(Rejected, "no freshness contract"):
            fresh_cases("invoice-total", SCENARIOS["invoice-total"].cases())
