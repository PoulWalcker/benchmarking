"""Native prompt bytes, source provenance and independent acceptance regressions."""

import hashlib
from importlib import import_module
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import yaml

from sapi_config_lab.coordinate.native_tasks import invoke
from sapi_config_lab.coordinate.provenance import source_manifest
from sapi_config_lab.paths import workspace_root
from sapi_config_lab.profile import read, read_bindings
from tests.support.invoice import CATALOG, fixture
from tests.support.invoice import cases as invoice_cases
from tests.support.verifying import verify_with_runner
from verification import verify

ROOT = workspace_root()
FIXTURE_JUDGE_PROMPT = "ec8023162bcb95ddf8f25a3edb02851944dcbde701d0d0fdc83a4beba3536142"

# Recorded authoring evidence pins the exact bytes of all supported prompt arms.
GENERATION_PROMPTS = {
    "research-report": "12e7a18a0881c4afeee2651ff00cfdcfae531e0e961a5df1c0b8a1e88399aff3",
    "invoice-total": "d1e72a8298a682f09c6198beb8e26f54beaf086f56a65a79a7cc68e0a0625f49",
}
SCENARIO_CATALOG_PROMPTS = {
    "invoice-total": "8c43ed0f1fd947ba5430cd95cb5555a0575b183fa0d6fbc2883ca2c98981150a",
}
HOSTED_PROMPTS = {
    "checkout-recovery": "920472a634ec32c6d55d66aceec6c4a66035d6cd69b5bc3ad36f3b3c315fae17",
}


class PackagingTests(unittest.TestCase):
    def test_generic_judge_prompt_is_pinned_and_in_source_inventory(self):
        path = ROOT / "src/sapi_config_lab/coordinate/fixture-judge-prompt.md"
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), FIXTURE_JUDGE_PROMPT)
        self.assertEqual(source_manifest()[str(path.relative_to(ROOT))], FIXTURE_JUDGE_PROMPT)

    def test_generation_prompts_are_byte_identical_to_the_recorded_ones(self):
        sources = source_manifest()
        for name, expected in (GENERATION_PROMPTS | HOSTED_PROMPTS).items():
            with self.subTest(task=name):
                prompt = invoke(ROOT / "tasks" / name, {"action": "prompt", "catalog": "full"}, sources)["prompt"]
                self.assertEqual(hashlib.sha256(prompt.encode()).hexdigest(), expected)

    def test_the_reduced_catalog_arm_changes_only_the_catalog_section(self):
        sources = source_manifest()
        for name, expected in SCENARIO_CATALOG_PROMPTS.items():
            task = ROOT / "tasks" / name
            full = invoke(task, {"action": "prompt", "catalog": "full"}, sources)["prompt"]
            reduced = invoke(task, {"action": "prompt", "catalog": "scenario"}, sources)["prompt"]
            self.assertEqual(hashlib.sha256(full.encode()).hexdigest(), GENERATION_PROMPTS[name])
            self.assertEqual(hashlib.sha256(reduced.encode()).hexdigest(), expected)
            before, catalog = full.split("\n\nOPERATION CATALOG\n")
            after, subset = reduced.split("\n\nOPERATION CATALOG\n")
            self.assertEqual(before, after)
            used = {step["uses"] for step in read(task / "solution/config.yaml")["workflow"]["steps"]}
            self.assertEqual(set(yaml.safe_load(subset)["operations"]), used)
            self.assertLess(len(subset), len(catalog))

    def test_hosted_catalogs_have_no_reduced_arm(self):
        with self.assertRaises(subprocess.CalledProcessError):
            invoke(ROOT / "tasks/checkout-recovery", {"action": "prompt", "catalog": "scenario"}, source_manifest())

    def test_documentation_is_not_part_of_the_invoice_prompt(self):
        authoring = import_module("invoice_task.experiment").authoring
        real = Path.read_text

        def guarded(path, *args, **kwargs):
            self.assertNotIn("docs", Path(path).relative_to(ROOT).parts if Path(path).is_relative_to(ROOT) else ())
            return real(path, *args, **kwargs)

        with patch.object(Path, "read_text", guarded):
            for arm in ("full", "scenario"):
                authoring(arm)

    def test_experiments_reject_code_from_a_different_installation(self):
        with patch("sapi_config_lab.paths.__file__", "/different/site-packages/sapi_config_lab/paths.py"):
            with patch.dict("os.environ", {"SAPI_LAB_ROOT": str(ROOT)}):
                with self.assertRaisesRegex(RuntimeError, "editable package"):
                    workspace_root()

    def test_source_inventory_keeps_the_task_owned_upstream_pin(self):
        self.assertNotIn("provenance/autowfbench-source.json", source_manifest())
        self.assertIn("tasks/checkout-recovery/provenance/autowfbench-source.json", source_manifest())

    def test_source_provenance_covers_runtime_tasks_and_documentation(self):
        manifest = source_manifest()
        for directory in ("src", "tasks"):
            for path in (ROOT / directory).rglob("*"):
                if path.suffix in {".py", ".js", ".yaml", ".toml"}:
                    self.assertIn(str(path.relative_to(ROOT)), manifest)
        for relative in ("uv.lock", "tasks/invoice-total/solution/solve.sh", "docs/ARCHITECTURE.md"):
            self.assertIn(relative, manifest)
        self.assertNotIn("provenance/environment.json", manifest)

    def test_source_inventory_discovers_new_behavior_but_never_local_snapshots(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            public = (
                "src/engine/new_adapter.py",
                "verification/new_criteria.py",
                ".github/workflows/checks.yml",
                "tasks/example/experiment.py",
                "tasks/example/task.toml",
                "docs/example.md",
            )
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
            self.assertEqual(set(source_manifest(root)), set(public))
            before = source_manifest(root)
            (root / public[0]).write_text("changed behavior")
            self.assertNotEqual(source_manifest(root), before)

    def test_evaluation_does_not_trust_a_recorded_engine_success(self):
        calls = []

        def runner(config, artifacts, **options):
            calls.append(config)
            return {"status": "success", "output": {"total_minor": 999}, "mapping": {}}

        fixtures = invoice_cases()
        cases = fixtures["positive"]
        with tempfile.TemporaryDirectory() as directory:
            result = verify_with_runner(
                verify,
                "invoice-total",
                ROOT / "tasks/invoice-total/solution/config.yaml",
                Path(directory),
                selected_case=cases[0]["name"],
                runner=runner,
                cases=fixtures,
                fixture=fixture(),
                bindings=read_bindings(CATALOG),
            )
            self.assertFalse(result["passed"])
            self.assertEqual(len(calls), 1)
            self.assertEqual(calls[0]["workflow"]["inputs"], cases[0]["inputs"])
            self.assertEqual(json.loads((Path(directory) / "evaluation/report.json").read_text()), result)
