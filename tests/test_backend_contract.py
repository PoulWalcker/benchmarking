"""Exercise the common runner through its public backend interface."""

import contextlib
import copy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from sapi_config_lab import profile
from sapi_config_lab.contracts import CompiledWorkflow, CompileOptions
from sapi_config_lab.coordinate import cli
from sapi_config_lab.coordinate.backend import N8nBackend
from sapi_config_lab.coordinate.cases import run_case
from sapi_config_lab.paths import workspace_root
from tests.support.invoice import CATALOG


class RecordingBackend:
    """Test collaborator only: deliberately has no n8n dependencies."""

    name = "test-engine"

    def __init__(self):
        self.compiled = []
        self.executed = []

    def compile(self, config, bindings, options):
        self.compiled.append((config, bindings, options))
        profile.validate(config, bindings)
        return CompiledWorkflow(self.name, {"test_document": True}, {"report": "end"}, 60, options)

    def execute(self, compiled, artifact_dir, binding):
        self.executed.append((compiled, artifact_dir))
        return {
            "status": "success",
            "output": {"engine_result": True},
            "workflow_id": "test-workflow",
            "execution_id": "test-execution",
            "engine_version": "test-version",
            "error": None,
        }


class BackendContractTests(unittest.TestCase):
    def setUp(self):
        self.config = profile.read(workspace_root() / "benchmarks/01-invoice-total/config.yaml")

    def test_runner_uses_injected_backend_and_leaves_acceptance_unevaluated(self):
        backend = RecordingBackend()
        original = copy.deepcopy(self.config)
        with tempfile.TemporaryDirectory() as directory, patch("subprocess.run", side_effect=AssertionError("No n8n")):
            record = run_case(
                self.config, Path(directory), backend=backend, llm_mode="live", bindings=profile.read_bindings(CATALOG)
            )
            persisted = json.loads((Path(directory) / "case.json").read_text())
        self.assertEqual(len(backend.compiled), 1)
        self.assertEqual(len(backend.executed), 1)
        self.assertEqual(backend.compiled[0][2].llm_mode, "live")
        self.assertEqual(self.config, original)
        self.assertEqual(record, persisted)
        self.assertTrue(record["execution"]["succeeded"])
        self.assertEqual(record["execution"]["engine"], {"name": "test-engine", "version": "test-version"})
        self.assertEqual(record["acceptance"], {"status": "not_evaluated", "passed": None})
        self.assertEqual(record["input"]["source"], "fixture")
        self.assertIsNone(record["llm"]["agency_http_call_count"])

    def test_invalid_definition_never_reaches_engine(self):
        self.config["activation"] = None
        backend = RecordingBackend()
        with tempfile.TemporaryDirectory() as directory:
            record = run_case(self.config, Path(directory), backend=backend, bindings=profile.read_bindings(CATALOG))
        self.assertEqual(record["status"], "compile_error")
        self.assertIn("activation", record["error"]["message"])
        self.assertEqual(record["error"]["type"], "Invalid")
        self.assertEqual(backend.executed, [])
        self.assertFalse(record["execution"]["succeeded"])

    def test_recursive_yaml_is_reported_without_serialization_crash(self):
        recursive = []
        recursive.append(recursive)
        self.config["workflow"]["inputs"]["recursive"] = recursive
        with tempfile.TemporaryDirectory() as directory:
            record = run_case(
                self.config, Path(directory), backend=RecordingBackend(), bindings=profile.read_bindings(CATALOG)
            )
            self.assertTrue((Path(directory) / "case.json").exists())
            self.assertFalse((Path(directory) / "config.json").exists())
        self.assertEqual(record["status"], "compile_error")
        self.assertIn("config.workflow.inputs.recursive[0]", record["error"]["message"])
        self.assertIsNone(record["input"]["config_artifact"])

    def test_cli_compile_and_execute_use_supplied_backend(self):
        backend = RecordingBackend()
        with tempfile.TemporaryDirectory() as directory, contextlib.redirect_stdout(io.StringIO()):
            output = Path(directory) / "compiled.json"
            config = str(workspace_root() / "benchmarks/01-invoice-total/config.yaml")
            self.assertEqual(cli.main(["compile", config, "--output", str(output)], backend=backend), 0)
            self.assertEqual(json.loads(output.read_text()), {"test_document": True})
            self.assertEqual(
                cli.main(["execute", "--config", config, "--artifacts", str(Path(directory) / "run")], backend=backend),
                0,
            )
        self.assertEqual(len(backend.compiled), 2)
        self.assertEqual(len(backend.executed), 1)

    def test_cli_help_separates_the_two_command_tiers(self):
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout), self.assertRaises(SystemExit):
            cli.main(["--help"])
        text = stdout.getvalue()
        self.assertLess(text.index("commands you run:"), text.index("internal commands"))
        for name in cli.PUBLIC:
            self.assertLess(text.index("  " + name + " "), text.index("internal commands"))
        for name in cli.INTERNAL:
            self.assertGreater(text.index("  " + name + " "), text.index("internal commands"))
        self.assertEqual(set(cli.PUBLIC) & set(cli.INTERNAL), set())
        self.assertEqual(set(cli.MODULES) - set(cli.PUBLIC) - set(cli.INTERNAL), set())

    def test_cli_invalid_shape_has_field_error_without_traceback(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "bad.yaml"
            config.write_text("schema: sapi-lab/v0\nworkflow: null\n")
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                result = cli.main(
                    [
                        "compile",
                        str(config),
                        "--scenario",
                        "invoice-total",
                        "--output",
                        str(Path(directory) / "out.json"),
                    ]
                )
        self.assertEqual(result, 2)
        self.assertEqual(json.loads(stderr.getvalue())["error"]["type"], "Invalid")
        self.assertNotIn("Traceback", stderr.getvalue())

    def test_n8n_adapter_preserves_supported_and_unsupported_profile(self):
        from sapi_config_lab.benchmark import discover_benchmarks

        for scenario in discover_benchmarks(workspace_root() / "benchmarks"):
            path, bindings = scenario.reference.source, profile.read_bindings(scenario.directory / scenario.bindings)
            config = profile.read(path)
            # Both environments compile through the same backend; a simulator's tools are bound at run time.
            options = CompileOptions(operation_url="http://tools/tools" if "prepare" in scenario.entrypoints else None)
            with self.subTest(config=path.parent.name):
                compiled = N8nBackend((scenario.directory / scenario.operations).read_text()).compile(
                    config, bindings, options
                )
                self.assertNotIn("not-persisted", str(compiled.document))
                self.assertEqual(compiled.engine, "n8n")
                attempts = config["execution"].get("refinement", {}).get("max_attempts", 1)
                self.assertEqual(len(compiled.mapping), attempts * len(config["workflow"]["steps"]))
