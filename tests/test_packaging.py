"""Regression checks for task distribution and separation of responsibilities."""

from dataclasses import replace
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import yaml

from sapi_config_lab.coordinate.packages import stage_tasks
from sapi_config_lab.coordinate.provenance import source_manifest
from sapi_config_lab.coordinate.provenance import source_manifest as inventory
from sapi_config_lab.coordinate.providers import ENVIRONMENTS, EVALUATORS, host_only_modules
from sapi_config_lab.coordinate.scenarios import DEFAULT_SCENARIOS as SCENARIOS
from sapi_config_lab.coordinate.scenarios import SCENARIOS as SCENARIOS_ALL
from sapi_config_lab.coordinate.scenarios import all_cases
from sapi_config_lab.paths import workspace_root
from sapi_config_lab.profile import read
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
            hashes = stage_tasks(Path(directory) / "tasks", mode="generation", scenarios=tuple(GENERATION_PROMPTS))
        self.assertEqual(hashes, GENERATION_PROMPTS)

    def test_the_reduced_catalog_arm_changes_only_the_catalog_section(self):
        with tempfile.TemporaryDirectory() as directory:
            names = tuple(SCENARIO_CATALOG_PROMPTS)
            full = Path(directory) / "full"
            self.assertEqual(stage_tasks(full, mode="generation", scenarios=names), GENERATION_PROMPTS)
            reduced = Path(directory) / "reduced"
            self.assertEqual(
                stage_tasks(reduced, mode="generation", scenarios=names, catalog="scenario"), SCENARIO_CATALOG_PROMPTS
            )
            for name in names:
                before, catalog = (full / name / "instruction.md").read_text().split("\n\nOPERATION CATALOG\n")
                after, subset = (reduced / name / "instruction.md").read_text().split("\n\nOPERATION CATALOG\n")
                self.assertEqual(before, after)
                used = {step["uses"] for step in read(SCENARIOS_ALL[name].config)["workflow"]["steps"]}
                self.assertEqual(set(yaml.safe_load(subset)["operations"]), used)
                self.assertLess(len(subset), len(catalog))
            with self.assertRaises(ValueError):
                stage_tasks(Path(directory) / "oracle", scenarios=names, catalog="scenario")

    @unittest.skipUnless(AVAILABLE, "Requires the pinned upstream source and benchmark extra")
    def test_hosted_catalogs_have_no_reduced_arm(self):
        with tempfile.TemporaryDirectory() as directory, self.assertRaisesRegex(ValueError, "fixture scenarios only"):
            stage_tasks(Path(directory) / "t", mode="generation", scenarios=("checkout-recovery",), catalog="scenario")

    def test_documentation_is_not_part_of_any_prompt(self):
        real = Path.read_text

        def guarded(path, *args, **kwargs):
            self.assertNotIn("docs", Path(path).relative_to(ROOT).parts if Path(path).is_relative_to(ROOT) else ())
            return real(path, *args, **kwargs)

        with tempfile.TemporaryDirectory() as directory, patch.object(Path, "read_text", guarded):
            stage_tasks(Path(directory) / "tasks", mode="generation", scenarios=tuple(GENERATION_PROMPTS))

    def test_experiments_reject_code_from_a_different_installation(self):
        with patch("sapi_config_lab.paths.__file__", "/different/site-packages/sapi_config_lab/paths.py"):
            with patch.dict("os.environ", {"SAPI_LAB_ROOT": str(ROOT)}):
                with self.assertRaisesRegex(RuntimeError, "editable package"):
                    workspace_root()

    def test_oracle_packages_take_current_sources_and_only_own_cases(self):
        source_cases = all_cases()
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "tasks"
            stage_tasks(destination)
            for name, definition in SCENARIOS.items():
                task = destination / name
                if definition.benchmark is not None:
                    self.assertEqual((task / "solution/config.yaml").read_bytes(), definition.config.read_bytes())
                    self.assertEqual(json.loads((task / "tests/payload/cases.json").read_text()), source_cases[name])
                    self.assertFalse((task / "environment/base.yaml").exists())
                    for item in definition.benchmark.public:
                        self.assertEqual(
                            (task / "environment/payload" / item.destination).read_bytes(), item.source.read_bytes()
                        )
                    for item in definition.benchmark.trusted:
                        self.assertEqual(
                            (task / "tests/payload" / item.destination).read_bytes(), item.source.read_bytes()
                        )
                        self.assertFalse((task / "environment/payload" / item.destination).exists())
                    continue
                self.assertEqual((task / "environment/base.yaml").read_bytes(), definition.config.read_bytes())
                self.assertEqual(
                    (task / "tests/verify.py").read_bytes(), (ROOT / "verification/verify.py").read_bytes()
                )
                for verifier_source in (ROOT / "verification").glob("*.py"):
                    self.assertEqual((task / "tests" / verifier_source.name).read_bytes(), verifier_source.read_bytes())
                self.assertEqual(json.loads((task / "tests/cases.json").read_text()), {name: source_cases[name]})
                # Only this scenario's evaluation data, and only in the trusted tests area.
                source = definition.directory / "evaluation"
                packaged = task / "tests/evaluation"
                expected = {f"{name}/{path.name}": path.read_bytes() for path in source.glob("*")}
                found = {str(path.relative_to(packaged)): path.read_bytes() for path in packaged.rglob("*.json")}
                self.assertEqual(found, expected)
                self.assertEqual(
                    [p for p in task.rglob("*.json") if "evaluation" in p.parts and "tests" not in p.parts], []
                )
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
                if (task / "tests/benchmark.json").exists():
                    self.assertNotIn("rm -rf", dockerfile)
                    self.assertFalse((task / "solution").exists())
                    self.assertEqual(list((task / "environment").rglob("*.json")), [])
                    self.assertEqual(
                        json.loads((task / "tests/payload/cases.json").read_text()), all_cases()[task.name]
                    )
                    continue
                self.assertIn("rm -rf /app/lab/benchmarks /app/scenario /app/submission", dockerfile)
                self.assertEqual(set(json.loads((task / "tests/cases.json").read_text())), {task.name})
                self.assertFalse((task / "solution").exists())
                self.assertEqual(list(task.rglob("*.yaml")), [task / "tests/bindings.yaml"])

    @unittest.skipUnless(AVAILABLE, "Requires the pinned upstream source and benchmark extra")
    def test_hosted_packages_scrub_the_evaluator_and_carry_no_hidden_data(self):
        names = ("checkout-recovery",)
        for mode in ("generation", "oracle"):
            with tempfile.TemporaryDirectory() as directory:
                hashes = stage_tasks(Path(directory) / "tasks", mode=mode, scenarios=names)
                self.assertEqual(hashes if mode == "generation" else HOSTED_PROMPTS, HOSTED_PROMPTS)
                for name in names:
                    task = Path(directory) / "tasks" / name
                    files = {str(path.relative_to(task)) for path in task.rglob("*") if path.is_file()}
                    dockerfile = (task / "environment/Dockerfile").read_text()
                    if SCENARIOS_ALL[name].benchmark is not None:
                        self.assertNotIn("rm -rf", dockerfile)
                        self.assertFalse((task / "tests/environment.json").exists())
                        self.assertTrue((task / "tests/docker-compose.yaml").is_file())
                        public = task / "environment"
                        self.assertFalse(
                            any(
                                path.name in {"cases.json", "scorecard.json", "evaluator.py"}
                                for path in public.rglob("*")
                            )
                        )
                        self.assertFalse((public / "payload/config.yaml").exists())
                        continue
                    for relative in host_only_modules():
                        self.assertIn(f"/app/lab/src/{relative}", dockerfile)
                    self.assertIn("/app/lab/benchmarks", dockerfile)
                    self.assertFalse(any("cases" in f or f.endswith(".py") for f in files), files)
                    text = "".join((task / f).read_text() for f in files)
                    self.assertNotIn("scorecard", text)
                    expected = "tests/admission.json" if mode == "generation" else "tests/environment.json"
                    self.assertIn(expected, files)

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
        from sapi_config_lab.coordinate.scenarios import SCENARIOS as ALL

        self.assertEqual(len(ALL), len(list((ROOT / "benchmarks").glob("*/scenario.json"))))
        for name, definition in ALL.items():
            if definition.environment == "fixtures":
                required = ("config.yaml", "task.md", "instruction.md", "cases.json")
                self.assertIn("positive", definition.cases())
            else:
                required = ("config.yaml", "instruction.md", "authoring-notes.md", "bindings.yaml")
                self.assertTrue(definition.provenance and definition.runtime_model_calls >= 0)
            for file in required:
                self.assertTrue((definition.directory / file).is_file(), (name, file))
        # The lab image copies benchmarks/ for its reference configs; cases.json is
        # evaluator-only and reaches a container only as a staged tests/ file.
        self.assertIn("COPY benchmarks /app/lab/benchmarks/", (ROOT / "infra/Dockerfile").read_text())
        self.assertIn("benchmarks/*/cases.json", (ROOT / ".dockerignore").read_text().splitlines())
        self.assertIn("benchmarks/*/evaluation/", (ROOT / ".dockerignore").read_text().splitlines())

    def test_every_scrubbed_evaluator_module_still_exists(self):
        # rm -rf exits 0 on a missing path, so a stale entry would leave the evaluator
        # readable inside the candidate's container and nothing at run time would say so.
        for relative in host_only_modules():
            self.assertTrue((ROOT / "src" / relative).exists(), relative)

    def test_every_provider_scrubs_its_own_modules(self):
        environment = replace(ENVIRONMENTS["autowfbench"], modules=("sapi_config_lab/execute/other.py",))
        evaluator = replace(EVALUATORS["autowfbench"], modules=("sapi_config_lab/evaluate/other.py",))
        with patch.dict(ENVIRONMENTS, {"other": environment}), patch.dict(EVALUATORS, {"other": evaluator}):
            self.assertIn("sapi_config_lab/execute/other.py", host_only_modules())
            self.assertIn("sapi_config_lab/evaluate/other.py", host_only_modules())

    def test_the_hosted_worker_imports_nothing_scrubbed_from_its_container(self):
        scrubbed = [
            relative.removesuffix(".py").replace("/", ".")
            for relative in host_only_modules()
            if relative.endswith(".py")
        ]
        script = (
            "import sys, sapi_config_lab.coordinate.hosted_worker\n"
            f"print([name for name in {scrubbed!r} if name in sys.modules])"
        )
        loaded = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, check=True).stdout
        self.assertEqual(loaded.strip(), "[]")

    def test_evaluation_does_not_trust_a_recorded_engine_success(self):
        spec = importlib.util.spec_from_file_location("independent_verifier", ROOT / "verification/verify.py")
        verifier = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(verifier)
        calls = []

        def runner(config, artifacts, **options):
            calls.append(config)
            return {"status": "success", "output": {"total_minor": 999}, "mapping": {}}

        fixtures = all_cases()["invoice-total"]
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
            )
            self.assertFalse(result["passed"])
            self.assertEqual(len(calls), 1)
            self.assertEqual(calls[0]["workflow"]["inputs"], cases[0]["inputs"])
            self.assertEqual(json.loads((Path(directory) / "evaluation/report.json").read_text()), result)
