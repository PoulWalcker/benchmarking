"""Compiler event binding survives deferral of the lifecycle controller."""

import json
from pathlib import Path
import shutil
import tempfile
import unittest

from sapi_config_lab import profile
from sapi_config_lab.contracts import CompileOptions, RunBinding
from sapi_config_lab.paths import workspace_root
from tests.support.invoice import CATALOG, OPERATION_SOURCE
from tests.support.native import SimulatedN8n

ROOT = workspace_root() / "tests/support/admission"


def fixture_bindings():
    return profile.read_bindings(ROOT / "bindings.yaml")


def FixtureN8n():
    return SimulatedN8n((ROOT / "operations.js").read_text())


def record_error(record):
    return json.dumps(record["error"])


class CompilerAdmissionTests(unittest.TestCase):
    def setUp(self):
        self.config = profile.read(ROOT / "config.yaml")

    def test_admitted_candidate_compiles_once_and_takes_its_event_and_deadline_at_run_time(self):
        admission = {
            "kind": "Callback",
            "rule_id": "fixture-test-requested",
            "event_id": "actual-event",
            "workflow_ref": {"id": "lifecycle-fixture", "revision": 1},
            "purpose": "test",
        }
        backend = FixtureN8n()
        bindings = fixture_bindings()
        with self.assertRaises(profile.Unsupported):
            backend.compile(self.config, bindings, CompileOptions())
        with self.assertRaises(ValueError):
            CompileOptions(activation="event")
        options = CompileOptions(activation="event", bound_deadline=True)
        compiled = backend.compile(self.config, bindings, options)
        # Identical definition and options give byte-identical artifacts; no run value is inside.
        self.assertEqual(
            json.dumps(backend.compile(self.config, bindings, options).document), json.dumps(compiled.document)
        )
        self.assertNotIn("actual-event", json.dumps(compiled.document))

        def run(binding):
            directory = Path(tempfile.mkdtemp())
            self.addCleanup(shutil.rmtree, directory)
            return backend.execute(compiled, directory, binding)

        with self.assertRaisesRegex(RuntimeError, "absolute deadline"):
            run(RunBinding(admission=admission))
        with self.assertRaisesRegex(RuntimeError, "admitted event"):
            run(RunBinding(deadline_at=9000000000))
        record = run(RunBinding(deadline_at=9000000000.5, admission=admission))
        self.assertEqual(record["result"]["admission"], admission)
        self.assertEqual(record["result"]["input_source"], "event")
        fixture = record["run_data"]["Fixture"][0]["data"]["main"][0][0]["json"]
        self.assertEqual(fixture["deadline_at_ms"], 9000000000.5 * 1000)
        other = {**admission, "workflow_ref": {"id": "lifecycle-fixture", "revision": 2}}
        self.assertIn(
            "Admission revision differs", record_error(run(RunBinding(deadline_at=9000000000, admission=other)))
        )
        wrong_rule = {**admission, "rule_id": "fixture-scheduled"}
        self.assertIn("Invalid lifecycle admission", record_error(run(RunBinding(9000000000, wrong_rule))))

    def test_an_ordinary_case_needs_no_binding_and_ignores_a_supplied_event(self):
        config = profile.read(workspace_root() / "tasks/invoice-total/solution/config.yaml")
        backend = SimulatedN8n(OPERATION_SOURCE)
        compiled = backend.compile(config, profile.read_bindings(CATALOG), CompileOptions())
        self.assertNotIn("SAPI_RUN_BINDING", json.dumps(compiled.document))
        with tempfile.TemporaryDirectory() as directory:
            record = backend.execute(compiled, Path(directory), RunBinding())
        self.assertEqual(record["status"], "success")
        self.assertNotIn("deadline_at_ms", record["run_data"]["Fixture"][0]["data"]["main"][0][0]["json"])
