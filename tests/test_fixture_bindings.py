"""A scenario catalog is the same input to authoring, staging, execution and verification."""

from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

import yaml

from sapi_config_lab.benchmark import load_benchmark
from sapi_config_lab.coordinate.benchmark_discovery import select_benchmarks
from sapi_config_lab.coordinate.lifecycle import LifecycleController
from sapi_config_lab.coordinate.observe import observe
from sapi_config_lab.coordinate.packages import generation_prompt, scenario_catalog, stage_tasks
from sapi_config_lab.paths import workspace_root
from sapi_config_lab.profile import Invalid, read, read_bindings
from tests.support.lifecycle import acceptance
from tests.support.lifecycle import bindings as fixture_bindings
from tests.support.lifecycle import config as fixture_config
from tests.test_lifecycle import LifecycleBackend


class FixtureBindingsTests(unittest.TestCase):
    def test_selected_catalog_is_public_prompt_and_trusted_execution_input(self):
        scenario = select_benchmarks(workspace_root() / "benchmarks", ("invoice-total",))[0]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            catalog = read_bindings(
                next(item.source for item in scenario.files if item.destination == scenario.bindings)
            )
            selected = {key: value for key, value in catalog.items() if key.startswith("invoices.")}
            path = root / "catalog.yaml"
            path.write_text(yaml.safe_dump({"operations": selected}))
            selected_root = root / "benchmarks"
            copied = selected_root / scenario.directory.name
            shutil.copytree(scenario.directory, copied)
            (copied / scenario.bindings).write_bytes(path.read_bytes())
            scenario = load_benchmark(selected_root, copied)
            prompt = generation_prompt(workspace_root(), scenario, "full")
            self.assertTrue(prompt.endswith(path.read_text()))
            self.assertEqual(yaml.safe_load(scenario_catalog(scenario))["operations"], selected)
            stage_tasks(root / "tasks", root=workspace_root(), benchmarks=(scenario,))
            staged = root / "tasks" / scenario.name / "tests"
            self.assertEqual((staged / "payload/bindings.yaml").read_bytes(), path.read_bytes())
            self.assertTrue((staged / "benchmark.json").exists())
            plan = {
                "mode": "stub",
                "entries": [{"name": "one", "procedure": "case", "config": read(scenario.reference.source)}],
            }
            seen = []
            observe(
                plan,
                scenario.reference.source,
                root / "evidence",
                bindings=read_bindings(staged / "payload/bindings.yaml"),
                runner=lambda config, directory, **options: seen.append(options["bindings"]),
            )
            self.assertEqual(seen, [selected])

    def test_lifecycle_admission_and_backend_use_selected_catalog_without_default_fallback(self):
        config = fixture_config()
        selected = fixture_bindings()
        backend = LifecycleBackend()
        with tempfile.TemporaryDirectory() as directory:
            controller = LifecycleController(Path(directory), verifier=acceptance, backend=backend, bindings=selected)
            with patch.object(backend, "compile", wraps=backend.compile) as compile_call:
                controller.callback(controller.register(config), "selected-catalog")
            self.assertEqual(compile_call.call_args.args[1], selected)
        with tempfile.TemporaryDirectory() as directory:
            controller = LifecycleController(Path(directory), verifier=acceptance, bindings={})
            with self.assertRaisesRegex(Invalid, "unknown operation|Unknown operation"):
                controller.register(config)
