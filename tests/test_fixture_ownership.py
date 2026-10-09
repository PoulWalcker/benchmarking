"""A selected fixture supplies independent behavior without a shared dispatch table."""

from dataclasses import replace
from functools import partial
from pathlib import Path
import tempfile
import unittest

import yaml

from sapi_config_lab.coordinate.cases import run_case
from sapi_config_lab.profile import read, read_bindings
from tests.support.invoice import CATALOG, DIRECTORY, OPERATION_SOURCE, cases, fixture
from tests.support.native import SimulatedN8n
from tests.support.verifying import verify_with_runner
from verification import verify
from verification.contracts import equal


class FixtureOwnershipTests(unittest.TestCase):
    def test_a_new_fixture_uses_standard_plan_evidence_and_corruption_checks(self):
        config = read(DIRECTORY / "solution/config.yaml")
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

        selected = replace(fixture(), business=independent_check)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            submission = root / "config.yaml"
            submission.write_text(yaml.safe_dump(config))
            report = verify_with_runner(
                verify,
                "new-invoice-task",
                submission,
                root / "run",
                runner=partial(run_case, backend=SimulatedN8n(OPERATION_SOURCE)),
                cases=cases(),
                fixture=selected,
                bindings=read_bindings(CATALOG),
            )
            self.assertTrue(report["passed"], report)
            self.assertEqual(
                verify.expected_model_calls("new-invoice-task", config, config["workflow"]["inputs"], fixture=selected),
                {},
            )
            self.assertTrue(calls)
            self.assertTrue(any(row["kind"] == "mutated-generated-workflow" for row in report["cases"]))
