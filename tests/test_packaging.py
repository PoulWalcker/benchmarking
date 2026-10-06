"""Regression checks for task distribution and separation of responsibilities."""

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from sapi_config_lab.coordinate.benchmark import ORACLE_MODULES, oracle_scrub
from sapi_config_lab.coordinate.packages import stage_tasks
from sapi_config_lab.coordinate.provenance import source_manifest
from sapi_config_lab.coordinate.provenance import source_manifest as inventory
from sapi_config_lab.coordinate.scenarios import BASELINE_SCENARIOS as SCENARIOS
from sapi_config_lab.paths import workspace_root
from sapi_config_lab.coordinate.scenarios import all_cases

ROOT = workspace_root()


# sha256 of each generation package's instruction.md: the exact prompt a model
# is given. Recorded authoring evidence pins these hashes, so a change here is a
# change to the benchmark and must be deliberate.
GENERATION_PROMPTS = {
    "bulletin-market-brief": "f1e595a18f8500edffa61029811a9e949107bd160b410a56150e1beb54ad7297",
    "competitor-report": "2d17a6ddd01a5261f62ba421906a600c2774cd23ab0bcbcbba1ff04184dff0d4",
    "daily-digest": "70d11e29f5910912e9fd70947e41a7a1f31ac0f4a1396e12f511e3a1a0c197b6",
    "dual-ledger-closeout": "7f2baad6cf11d00e45db9230ed1063ca9b722dcef3e9188ca27cfa08621a09e6",
    "invoice-total": "d1e72a8298a682f09c6198beb8e26f54beaf086f56a65a79a7cc68e0a0625f49",
    "priority-support-brief": "446aefbcbfdbe3dcb4b37116241ca0964a3112943d95f3d2b7d48cf1f8dccd94",
    "revise-answer": "94a2fa3749b2c3be6355247ae36d7759f71db995c9e4b54212d1c2e4dd6f5e96",
    "support-review-packet": "8817f0794c1f945059287ec85503d6d517e6f571f0f8c00233dc182a76e7a9f0",
    "ticket-routing": "21ea6dc4070a0070ee7f1cb59d9556ba262d6919eae426ed9f56f5743bdc4a77",
}


class PackagingTests(unittest.TestCase):
    def test_generation_prompts_are_byte_identical_to_the_recorded_ones(self):
        with tempfile.TemporaryDirectory() as directory:
            hashes = stage_tasks(Path(directory) / "tasks", mode="generation", scenarios=tuple(GENERATION_PROMPTS))
        self.assertEqual(hashes, GENERATION_PROMPTS)

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
                self.assertEqual((task / "environment/base.yaml").read_bytes(), definition.config.read_bytes())
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
                self.assertIn("rm -rf /app/lab/benchmarks /app/scenario /app/submission", dockerfile)
                self.assertEqual(set(json.loads((task / "tests/cases.json").read_text())), {task.name})
                self.assertFalse((task / "solution").exists())
                self.assertEqual(list(task.rglob("*.yaml")), [])

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
            for required in ("config.yaml", "task.md", "instruction.md", "cases.json"):
                self.assertTrue((definition.directory / required).is_file(), (name, required))
            self.assertIn("positive", definition.cases())
            if definition.runtime_caps:
                self.assertEqual(list(definition.runtime_caps), definition.cases()["live_cases"], name)
        # The lab image copies benchmarks/ for its reference configs; cases.json is
        # evaluator-only and reaches a container only as a staged tests/ file.
        self.assertIn("COPY benchmarks /app/lab/benchmarks/", (ROOT / "infra/Dockerfile").read_text())
        self.assertIn("benchmarks/*/cases.json", (ROOT / ".dockerignore").read_text().splitlines())

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

        fixtures = all_cases()["invoice-total"]
        cases = fixtures["positive"]
        with tempfile.TemporaryDirectory() as directory:
            result = verifier.verify_submission(
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
            self.assertEqual(json.loads((Path(directory) / "report.json").read_text()), result)
