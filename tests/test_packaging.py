"""Regression checks for task distribution and separation of responsibilities."""

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import yaml

from sapi_config_lab.benchmark import discover_benchmarks
from sapi_config_lab.coordinate.benchmark_discovery import select_benchmarks
from sapi_config_lab.coordinate.packages import stage_tasks
from sapi_config_lab.coordinate.provenance import source_manifest
from sapi_config_lab.coordinate.provenance import source_manifest as inventory
from sapi_config_lab.paths import resource_root, workspace_root
from sapi_config_lab.profile import read, read_bindings
from tests.support.invoice import CATALOG, fixture
from tests.support.invoice import cases as invoice_cases
from tests.support.pinned import AVAILABLE
from tests.support.verifying import verify_with_runner

ROOT = workspace_root()


# sha256 of each generation package's instruction.md: the exact prompt a model
# is given. Recorded authoring evidence pins these hashes, so a change here is a
# change to the benchmark and must be deliberate.
GENERATION_PROMPTS = {
    "invoice-total": "d1e72a8298a682f09c6198beb8e26f54beaf086f56a65a79a7cc68e0a0625f49",
}


# The reduced-catalog experiment arm (`--catalog scenario`): the same prompts with only the
# operations each scenario's reference uses. Not the default; a change here is an experiment change.
SCENARIO_CATALOG_PROMPTS = {
    "invoice-total": "8c43ed0f1fd947ba5430cd95cb5555a0575b183fa0d6fbc2883ca2c98981150a",
}


# Hosted prompt revision: environment-neutral wording; historical authoring records retain their original hashes.
HOSTED_PROMPTS = {
    "checkout-recovery": "920472a634ec32c6d55d66aceec6c4a66035d6cd69b5bc3ad36f3b3c315fae17",
}


class PackagingTests(unittest.TestCase):
    def test_generation_prompts_are_byte_identical_to_the_recorded_ones(self):
        with tempfile.TemporaryDirectory() as directory:
            hashes = stage_tasks(
                Path(directory) / "tasks",
                mode="generation",
                root=ROOT,
                benchmarks=select_benchmarks(ROOT / "benchmarks", tuple(GENERATION_PROMPTS)),
            )
        self.assertEqual(hashes, GENERATION_PROMPTS)

    def test_the_reduced_catalog_arm_changes_only_the_catalog_section(self):
        with tempfile.TemporaryDirectory() as directory:
            names = tuple(SCENARIO_CATALOG_PROMPTS)
            full = Path(directory) / "full"
            self.assertEqual(
                stage_tasks(
                    full, mode="generation", root=ROOT, benchmarks=select_benchmarks(ROOT / "benchmarks", names)
                ),
                GENERATION_PROMPTS,
            )
            reduced = Path(directory) / "reduced"
            self.assertEqual(
                stage_tasks(
                    reduced,
                    mode="generation",
                    root=ROOT,
                    benchmarks=select_benchmarks(ROOT / "benchmarks", names),
                    catalog="scenario",
                ),
                SCENARIO_CATALOG_PROMPTS,
            )
            for name in names:
                before, catalog = (full / name / "instruction.md").read_text().split("\n\nOPERATION CATALOG\n")
                after, subset = (reduced / name / "instruction.md").read_text().split("\n\nOPERATION CATALOG\n")
                self.assertEqual(before, after)
                used = {
                    step["uses"]
                    for step in read(select_benchmarks(ROOT / "benchmarks", (name,))[0].reference.source)["workflow"][
                        "steps"
                    ]
                }
                self.assertEqual(set(yaml.safe_load(subset)["operations"]), used)
                self.assertLess(len(subset), len(catalog))
            with self.assertRaises(ValueError):
                stage_tasks(
                    Path(directory) / "oracle",
                    root=ROOT,
                    benchmarks=select_benchmarks(ROOT / "benchmarks", names),
                    catalog="scenario",
                )

    @unittest.skipUnless(AVAILABLE, "Requires the pinned upstream source and benchmark extra")
    def test_hosted_catalogs_have_no_reduced_arm(self):
        with tempfile.TemporaryDirectory() as directory, self.assertRaisesRegex(ValueError, "fixture scenarios only"):
            stage_tasks(
                Path(directory) / "t",
                mode="generation",
                root=ROOT,
                benchmarks=select_benchmarks(ROOT / "benchmarks", ("checkout-recovery",)),
                catalog="scenario",
            )

    def test_documentation_is_not_part_of_any_prompt(self):
        real = Path.read_text

        def guarded(path, *args, **kwargs):
            self.assertNotIn("docs", Path(path).relative_to(ROOT).parts if Path(path).is_relative_to(ROOT) else ())
            return real(path, *args, **kwargs)

        with tempfile.TemporaryDirectory() as directory, patch.object(Path, "read_text", guarded):
            stage_tasks(
                Path(directory) / "tasks",
                mode="generation",
                root=ROOT,
                benchmarks=select_benchmarks(ROOT / "benchmarks", tuple(GENERATION_PROMPTS)),
            )

    def test_experiments_reject_code_from_a_different_installation(self):
        with patch("sapi_config_lab.paths.__file__", "/different/site-packages/sapi_config_lab/paths.py"):
            with patch.dict("os.environ", {"SAPI_LAB_ROOT": str(ROOT)}):
                with self.assertRaisesRegex(RuntimeError, "editable package"):
                    workspace_root()

    def test_installed_resource_resolution_does_not_admit_checkout_experiments(self):
        with tempfile.TemporaryDirectory() as directory:
            package = Path(directory) / "site-packages/sapi_config_lab"
            resources = package / "resources"
            (resources / "benchmarks").mkdir(parents=True)
            (resources / "generation").mkdir()
            (resources / "generation/PROFILE.md").write_text("resource fixture")
            with patch("sapi_config_lab.paths.__file__", str(package / "paths.py")):
                with patch.dict("os.environ", {"SAPI_LAB_ROOT": str(ROOT)}):
                    self.assertEqual(resource_root(), resources.resolve())
                    with self.assertRaisesRegex(RuntimeError, "editable package"):
                        workspace_root()

    def test_source_inventory_uses_declared_pins_without_provider_filename_fallback(self):
        self.assertNotIn("provenance/autowfbench-source.json", source_manifest())
        self.assertIn("benchmarks/10-checkout-recovery/provenance/autowfbench-source.json", source_manifest())

    def test_oracle_packages_take_current_sources_and_only_own_cases(self):
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "tasks"
            selected = select_benchmarks(ROOT / "benchmarks")
            stage_tasks(destination, root=ROOT, benchmarks=selected)
            for definition in selected:
                task = destination / definition.name
                self.assertEqual((task / "solution/config.yaml").read_bytes(), definition.reference.source.read_bytes())
                self.assertEqual(json.loads((task / "tests/payload/cases.json").read_text()), invoice_cases())
                self.assertFalse((task / "environment/base.yaml").exists())
                for item in definition.public:
                    self.assertEqual(
                        (task / "environment/payload" / item.destination).read_bytes(), item.source.read_bytes()
                    )
                for item in definition.trusted:
                    self.assertEqual((task / "tests/payload" / item.destination).read_bytes(), item.source.read_bytes())
                    self.assertFalse((task / "environment/payload" / item.destination).exists())
            with self.assertRaises(FileExistsError):
                stage_tasks(destination, root=ROOT, benchmarks=selected)

    def test_generation_removes_reference_locations_and_filters_hidden_cases(self):
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "tasks"
            stage_tasks(destination, root=ROOT, benchmarks=select_benchmarks(ROOT / "benchmarks"), mode="generation")
            task = destination / "invoice-total"
            self.assertFalse((task / "solution").exists())
            self.assertFalse((task / "environment/payload/config.yaml").exists())
            self.assertEqual(list((task / "environment").rglob("*.json")), [])
            self.assertEqual(json.loads((task / "tests/payload/cases.json").read_text()), invoice_cases())

    @unittest.skipUnless(AVAILABLE, "Requires the pinned upstream source and benchmark extra")
    def test_hosted_packages_scrub_the_evaluator_and_carry_no_hidden_data(self):
        selected = select_benchmarks(ROOT / "benchmarks", ("checkout-recovery",))
        for mode in ("generation", "oracle"):
            with tempfile.TemporaryDirectory() as directory:
                hashes = stage_tasks(Path(directory) / "tasks", root=ROOT, benchmarks=selected, mode=mode)
                if mode == "generation":
                    self.assertEqual(hashes, HOSTED_PROMPTS)
                task = Path(directory) / "tasks/checkout-recovery"
                self.assertFalse((task / "tests/environment.json").exists())
                self.assertTrue((task / "tests/docker-compose.yaml").is_file())
                public = task / "environment"
                self.assertFalse(
                    any(path.name in {"cases.json", "scorecard.json", "evaluator.py"} for path in public.rglob("*"))
                )
                self.assertFalse((public / "payload/config.yaml").exists())

    def test_source_provenance_covers_relocated_behavior_and_dependencies(self):
        manifest = source_manifest()
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

    def test_every_benchmark_is_complete_and_its_fixtures_stay_out_of_the_image(self):
        selected = discover_benchmarks(ROOT / "benchmarks")
        self.assertEqual(len(selected), len(list((ROOT / "benchmarks").glob("*/scenario.json"))))
        for definition in selected:
            for item in definition.files:
                self.assertTrue(item.source.is_file())
        # The lab image copies benchmarks/ for its reference configs; cases.json is
        # evaluator-only and reaches a container only as a staged tests/ file.
        self.assertIn("COPY benchmarks /app/lab/benchmarks/", (ROOT / "infra/Dockerfile").read_text())
        self.assertIn("benchmarks/*/cases.json", (ROOT / ".dockerignore").read_text().splitlines())
        self.assertIn("benchmarks/*/evaluation/", (ROOT / ".dockerignore").read_text().splitlines())

    def test_evaluation_does_not_trust_a_recorded_engine_success(self):
        spec = importlib.util.spec_from_file_location("independent_verifier", ROOT / "verification/verify.py")
        verifier = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(verifier)
        calls = []

        def runner(config, artifacts, **options):
            calls.append(config)
            return {"status": "success", "output": {"total_minor": 999}, "mapping": {}}

        fixtures = invoice_cases()
        cases = fixtures["positive"]
        with tempfile.TemporaryDirectory() as directory:
            result = verify_with_runner(
                verifier,
                "invoice-total",
                ROOT / "benchmarks/01-invoice-total/config.yaml",
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
