"""Trusted operation source injection, including recursively lowered refinement."""

import copy
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from sapi_config_lab.compile.n8n import RESOURCES, compile_n8n
from sapi_config_lab.compile.refinement import compile_refinement
from sapi_config_lab.contracts import CompileOptions
from sapi_config_lab.coordinate.backend import N8nBackend
from sapi_config_lab.paths import CATALOG, workspace_root
from sapi_config_lab.profile import Invalid, Unsupported, read, read_bindings

ROOT = workspace_root()
BUNDLE = """function need(condition, message) { if (!condition) throw new Error(message); }
const operations = {
  'example.increment': ({value}) => ({value: value + 1}),
};
"""


class OperationBundleTests(unittest.TestCase):
    def setUp(self):
        self.config = read(ROOT / "benchmarks/01-invoice-total/config.yaml")
        self.config["workflow"].update(
            id="bundle-example",
            inputs={"value": 5},
            steps=[
                {
                    "id": "increment",
                    "kind": "Script",
                    "uses": "example.increment",
                    "with": {"value": {"ref": "inputs.value"}},
                }
            ],
            dependencies=[],
            output={"ref": "steps.increment"},
        )
        self.config["activation"]["workflow_ref"]["id"] = "bundle-example"
        self.bindings = {
            "example.increment": {
                "kind": "Script",
                "inputs": ["value"],
                "outputs": ["value"],
                "implementation": "local_js",
            }
        }

    def refinement(self):
        config = copy.deepcopy(self.config)
        config["workflow"]["steps"][0]["with"] = {"value": {"ref": "runtime.value"}}
        config["execution"]["refinement"] = {
            "region": ["increment"],
            "initial_state": {"value": 0},
            "carry": {"value": {"ref": "steps.increment.value"}},
            "until": {"ref": "steps.increment.value", "eq": 2},
            "max_attempts": 3,
            "exhausted": "failed",
            "output_policy": "last_accepted_only",
        }
        return config

    def run_export(self, document):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "workflow.json"
            path.write_text(json.dumps(document))
            result = subprocess.run(
                ["node", str(ROOT / "tests/support/run-refinement-export.mjs"), str(path)],
                capture_output=True,
                text=True,
                check=True,
                timeout=10,
            )
        exported = json.loads(result.stdout)
        self.assertIsNone(exported["error"])
        return exported

    def test_new_operation_executes_from_explicit_source_without_global_registration(self):
        with self.assertRaisesRegex(Unsupported, "No local implementation for example.increment"):
            compile_n8n(self.config, self.bindings)
        document, _ = compile_n8n(self.config, self.bindings, operation_source=BUNDLE)
        self.assertEqual(self.run_export(document)["result"]["output"], {"value": 6})
        compiled = N8nBackend(operation_source=BUNDLE).compile(self.config, self.bindings, CompileOptions())
        self.assertEqual(compiled.document, document)
        self.assertNotIn("invoices.sum", json.dumps(document))

    def test_refinement_and_every_recursive_attempt_use_injected_source(self):
        config = self.refinement()
        original_read = Path.read_text

        def without_legacy_operations(path, *args, **kwargs):
            if path == RESOURCES / "operations.js":
                raise AssertionError("Explicit bundle must not read legacy operations")
            return original_read(path, *args, **kwargs)

        with patch.object(Path, "read_text", without_legacy_operations):
            ordinary_entry, _ = compile_n8n(config, self.bindings, operation_source=BUNDLE)
            direct_entry, _ = compile_refinement(config, self.bindings, "stub", None, 190, operation_source=BUNDLE)
            backend_entry = (
                N8nBackend(operation_source=BUNDLE).compile(config, self.bindings, CompileOptions()).document
            )
        self.assertEqual(ordinary_entry, direct_entry)
        self.assertEqual(ordinary_entry, backend_entry)
        probe = self.run_export(backend_entry)
        self.assertEqual(probe["result"]["output"], {"value": 2})
        self.assertEqual([attempt["accepted"] for attempt in probe["result"]["refinement"]["attempts"]], [False, True])
        self.assertNotIn("Attempt 3 / increment", probe["executed"])
        for node in backend_entry["nodes"]:
            if node["type"].endswith(".code") and node["name"] != "Fixture":
                self.assertIn(BUNDLE, node["parameters"]["jsCode"], node["name"])

    def test_missing_implementation_never_falls_back_to_legacy_source(self):
        invoice = read(ROOT / "benchmarks/01-invoice-total/config.yaml")
        bindings = read_bindings(CATALOG)
        for source in ("", BUNDLE):
            with (
                self.subTest(source=source),
                self.assertRaisesRegex(Unsupported, "No local implementation for invoices"),
            ):
                N8nBackend(operation_source=source).compile(invoice, bindings, CompileOptions())
        empty = "function need(condition, message) {}\nconst operations = {};\n"
        for config in (self.config, self.refinement()):
            with self.subTest(refinement="refinement" in config["execution"]):
                with self.assertRaisesRegex(Unsupported, "No local implementation for example.increment"):
                    compile_n8n(config, self.bindings, operation_source=empty)
        with self.assertRaisesRegex(Unsupported, "No local implementation for example.increment"):
            compile_refinement(self.refinement(), self.bindings, "stub", None, 190, operation_source=empty)

    def test_injection_accepts_source_text_not_candidate_selected_paths(self):
        for source in (Path("candidate.js"), False):
            with self.subTest(source=source), self.assertRaisesRegex(Invalid, "operation_source.*JavaScript text"):
                N8nBackend(operation_source=source).compile(self.config, self.bindings, CompileOptions())
        self.config["workflow"]["operation_source"] = "candidate.js"
        with self.assertRaises(Invalid):
            compile_n8n(self.config, self.bindings, operation_source=BUNDLE)

    def test_legacy_default_preserves_exact_artifacts_for_both_compiler_paths(self):
        source = (RESOURCES / "operations.js").read_text()
        bindings = read_bindings(CATALOG)
        for scenario in ("benchmarks/01-invoice-total/config.yaml", "tests/support/graphs/refinement.yaml"):
            config = read(ROOT / scenario)
            with self.subTest(scenario=scenario):
                self.assertEqual(
                    compile_n8n(config, bindings),
                    compile_n8n(config, bindings, operation_source=source),
                )
