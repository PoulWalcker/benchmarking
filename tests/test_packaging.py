"""Regression checks for task distribution and separation of responsibilities."""

import ast
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from sapi_config_lab.interfaces.checkout import ORACLE_MODULES, oracle_scrub
from sapi_config_lab.interfaces.tasks import SCENARIOS, stage_tasks
from sapi_config_lab.interfaces.generation.common import fingerprints
from sapi_config_lab.interfaces.harbor import source_manifest
from sapi_config_lab.core.provenance import source_manifest as inventory
from sapi_config_lab.paths import workspace_root

ROOT = workspace_root()


class PackagingTests(unittest.TestCase):
    def test_experiments_reject_code_from_a_different_installation(self):
        with patch("sapi_config_lab.paths.__file__", "/different/site-packages/sapi_config_lab/paths.py"):
            with patch.dict("os.environ", {"SAPI_LAB_ROOT": str(ROOT)}):
                with self.assertRaisesRegex(RuntimeError, "editable package"):
                    workspace_root()

    def test_oracle_packages_take_current_sources_and_only_own_cases(self):
        source_cases = json.loads((ROOT / "verification/cases.json").read_text())
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "tasks"
            stage_tasks(destination)
            for name, config in SCENARIOS.items():
                task = destination / name
                self.assertEqual(
                    (task / "environment/base.yaml").read_bytes(), (ROOT / "configs" / config).read_bytes()
                )
                self.assertEqual(
                    (task / "tests/verify.py").read_bytes(), (ROOT / "verification/verify.py").read_bytes()
                )
                for verifier_source in (ROOT / "verification").glob("*.py"):
                    self.assertEqual((task / "tests" / verifier_source.name).read_bytes(), verifier_source.read_bytes())
                self.assertEqual(json.loads((task / "tests/cases.json").read_text()), {name: source_cases[name]})
                self.assertIn(f"--scenario {name}", (task / "tests/test.sh").read_text())
                self.assertIn(
                    "cp /app/scenario/base.yaml /app/submission/config.yaml", (task / "solution/solve.sh").read_text()
                )
            with self.assertRaises(FileExistsError):
                stage_tasks(destination)

    def test_generation_removes_reference_locations_and_filters_hidden_cases(self):
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "tasks"
            stage_tasks(destination, mode="generation")
            for task in destination.iterdir():
                dockerfile = (task / "environment/Dockerfile").read_text()
                self.assertIn("rm -rf /app/lab/configs /app/scenario /app/submission", dockerfile)
                self.assertEqual(set(json.loads((task / "tests/cases.json").read_text())), {task.name})
                self.assertFalse((task / "solution").exists())
                self.assertEqual(list(task.rglob("*.yaml")), [])

    def test_source_provenance_covers_relocated_behavior_and_dependencies(self):
        manifest = source_manifest()
        self.assertTrue(fingerprints())
        self.assertTrue(set(fingerprints()) <= manifest.keys())
        for path in (ROOT / "src").rglob("*"):
            if path.suffix in {".py", ".js", ".yaml"}:
                self.assertIn(str(path.relative_to(ROOT)), manifest)
        self.assertIn("uv.lock", manifest)
        self.assertIn("harbor/templates/solve.sh", manifest)
        self.assertNotIn("provenance/environment.json", manifest)

    def test_source_inventory_discovers_new_behavior_but_never_local_snapshots(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            public = ("src/engine/new_adapter.py", "verification/new_criteria.py", ".github/workflows/checks.yml")
            private = (
                "reports/example/report.json",
                "generated/example.json",
                "validation/example.json",
                "provenance/environment.json",
                "provenance/future-local-snapshot.json",
                "src/engine/__pycache__/new_adapter.pyc",
            )
            for name in (*public, *private):
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("test fixture")
            self.assertEqual(set(inventory(root)), set(public))
            before = inventory(root)
            (root / public[0]).write_text("changed behavior")
            self.assertNotEqual(inventory(root), before)

    def test_imports_respect_responsibilities(self):
        # core holds the shared rules, runtime the independent services, interfaces the
        # interaction surfaces. Dependencies only ever point inward: core knows about
        # neither of the others, and runtime knows nothing about an interaction surface.
        package = ROOT / "src/sapi_config_lab"
        forbidden = {
            "core": ("sapi_config_lab.runtime", "sapi_config_lab.interfaces", "verification", "harbor"),
            "runtime": ("sapi_config_lab.interfaces", "verification", "harbor"),
        }
        for group in ("core", "runtime", "interfaces"):
            # Without this the rules above silently stop applying to anything.
            self.assertTrue((package / group).is_dir(), group)
        for path in package.rglob("*.py"):
            relative = path.relative_to(package)
            rules = forbidden.get(relative.parts[0])
            if rules is None:
                continue
            for node in ast.walk(ast.parse(path.read_text())):
                imports = (
                    [node.module or ""]
                    if isinstance(node, ast.ImportFrom)
                    else ([a.name for a in node.names] if isinstance(node, ast.Import) else [])
                )
                for name in imports:
                    self.assertFalse(name.startswith(rules), (relative, name))

    def test_agent_image_still_deletes_every_scoring_oracle_module(self):
        # rm -rf exits 0 on a missing path, so a stale entry would leave the oracle
        # readable inside the agent's container and nothing at run time would say so.
        scrub = oracle_scrub()
        self.assertTrue(ORACLE_MODULES)
        for relative in ORACLE_MODULES:
            self.assertTrue((ROOT / "src" / relative).exists(), relative)
            self.assertIn(f"/app/lab/src/{relative}", scrub)

    def test_verifier_injects_runner_but_does_not_trust_its_success(self):
        spec = importlib.util.spec_from_file_location("independent_verifier", ROOT / "verification/verify.py")
        verifier = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(verifier)
        calls = []

        def runner(config, artifacts, **options):
            calls.append(config)
            return {"status": "success", "output": {"total_minor": 999}, "mapping": {}}

        cases = json.loads((ROOT / "verification/cases.json").read_text())["invoice-total"]["positive"]
        with tempfile.TemporaryDirectory() as directory:
            result = verifier.verify_submission(
                "invoice-total",
                ROOT / "configs/01-invoice-total.yaml",
                Path(directory),
                selected_case=cases[0]["name"],
                runner=runner,
            )
            self.assertFalse(result["passed"])
            self.assertEqual(len(calls), 1)
            self.assertEqual(calls[0]["workflow"]["inputs"], cases[0]["inputs"])
            self.assertEqual(json.loads((Path(directory) / "report.json").read_text()), result)
