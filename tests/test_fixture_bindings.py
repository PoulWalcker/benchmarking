"""A scenario catalog is the same input to authoring, staging, execution and verification."""

from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import yaml

from sapi_config_lab.coordinate.lifecycle import LifecycleController
from sapi_config_lab.coordinate.observe import observe
from sapi_config_lab.coordinate.packages import generation_prompt, scenario_catalog, stage_tasks
from sapi_config_lab.coordinate.scenarios import SCENARIOS
from sapi_config_lab.paths import workspace_root
from sapi_config_lab.profile import Invalid, read, read_bindings
from tests.test_lifecycle import DigestBackend


class FixtureBindingsTests(unittest.TestCase):
    def test_selected_catalog_is_public_prompt_and_trusted_execution_input(self):
        scenario = SCENARIOS["invoice-total"]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            catalog = read_bindings(scenario.bindings)
            selected = {key: value for key, value in catalog.items() if key.startswith("invoices.")}
            path = root / "catalog.yaml"
            path.write_text(yaml.safe_dump({"operations": selected}))
            scenario = replace(scenario, bindings=path, benchmark=None)
            prompt = generation_prompt(workspace_root(), scenario)
            self.assertTrue(prompt.endswith(path.read_text()))
            self.assertEqual(yaml.safe_load(scenario_catalog(scenario))["operations"], selected)
            with patch.dict(SCENARIOS, {scenario.name: scenario}):
                stage_tasks(root / "tasks", scenarios=(scenario.name,))
            staged = root / "tasks" / scenario.name / "tests"
            self.assertEqual((staged / "bindings.yaml").read_bytes(), path.read_bytes())
            self.assertIn("--bindings /tests/bindings.yaml", (staged / "test.sh").read_text())
            plan = {"mode": "stub", "entries": [{"name": "one", "procedure": "case", "config": read(scenario.config)}]}
            seen = []
            observe(
                plan,
                scenario.config,
                root / "evidence",
                bindings=read_bindings(staged / "bindings.yaml"),
                runner=lambda config, directory, **options: seen.append(options["bindings"]),
            )
            self.assertEqual(seen, [selected])

    def test_lifecycle_admission_and_backend_use_selected_catalog_without_default_fallback(self):
        scenario = SCENARIOS["daily-digest"]
        config = read(scenario.config)
        selected = {key: value for key, value in read_bindings(scenario.bindings).items() if key.startswith("digest.")}
        backend = DigestBackend()
        with tempfile.TemporaryDirectory() as directory:
            controller = LifecycleController(Path(directory), backend=backend, bindings=selected)
            with patch.object(backend, "compile", wraps=backend.compile) as compile_call:
                controller.callback(controller.register(config), "selected-catalog")
            self.assertEqual(compile_call.call_args.args[1], selected)
        with tempfile.TemporaryDirectory() as directory:
            controller = LifecycleController(Path(directory), bindings={})
            with self.assertRaisesRegex(Invalid, "unknown operation|Unknown operation"):
                controller.register(config)
