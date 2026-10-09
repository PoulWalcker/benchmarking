"""A task catalog remains the same authoring, execution and verification input."""

from importlib import import_module
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

import yaml

from sapi_config_lab.coordinate.observe import observe
from sapi_config_lab.paths import workspace_root
from sapi_config_lab.profile import read, read_bindings
from tests.support.invoice import DIRECTORY, EVALUATOR


class FixtureBindingsTests(unittest.TestCase):
    def test_selected_catalog_is_public_prompt_and_trusted_execution_input(self):
        experiment = import_module("invoice_task.experiment")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            copied = root / "tasks/invoice-total"
            shutil.copytree(DIRECTORY, copied)
            (root / "generation").mkdir()
            for name in ("FORMAT.md", "PROFILE.md"):
                shutil.copyfile(workspace_root() / "generation" / name, root / "generation" / name)
            catalog = read_bindings(DIRECTORY / "bindings.yaml")
            selected = {key: value for key, value in catalog.items() if key.startswith("invoices.")}
            path = copied / "bindings.yaml"
            path.write_text(yaml.safe_dump({"operations": selected}))
            with patch.object(experiment, "ROOT", copied):
                self.assertTrue(experiment.authoring("full")["prompt"].endswith(path.read_text()))
                self.assertEqual(yaml.safe_load(experiment.authoring("scenario")["catalog"])["operations"], selected)
            with patch.object(EVALUATOR, "ROOT", copied):
                plan = EVALUATOR.plan(
                    copied / "solution/config.yaml", {"mode": "live", "selected_case": "original-38000"}
                )
            seen = []
            observe(
                plan,
                copied / "solution/config.yaml",
                root / "evidence",
                bindings=read_bindings(path),
                runner=lambda config, directory, **options: seen.append(options["bindings"]),
            )
            self.assertEqual(seen, [selected])
            self.assertEqual(
                plan["entries"][0]["config"]["workflow"]["steps"],
                read(copied / "solution/config.yaml")["workflow"]["steps"],
            )
