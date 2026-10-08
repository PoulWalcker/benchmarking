"""Invoice ownership, independent acceptance and the positive trusted runtime closure."""

from functools import partial
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from sapi_config_lab.benchmark import load_benchmark
from sapi_config_lab.benchmark_loading import freeze_identity, load_entrypoints
from sapi_config_lab.coordinate.backend import N8nBackend
from sapi_config_lab.coordinate.benchmark_discovery import select_benchmarks
from sapi_config_lab.coordinate.cases import run_case
from sapi_config_lab.coordinate.live import validate_packages
from sapi_config_lab.coordinate.observe import observe
from sapi_config_lab.coordinate.packages import stage_tasks
from sapi_config_lab.coordinate.replay import load_selection, select_submission
from sapi_config_lab.evidence import sha256
from sapi_config_lab.paths import workspace_root
from sapi_config_lab.profile import read_bindings
from tests.support.invoice import cases as invoice_cases
from tests.support.invoice import fixture
from tests.support.native import SimulatedN8n
from tests.test_selection import SOURCES, generation_run
from verification import verify

ROOT = workspace_root()
DIRECTORY = ROOT / "benchmarks/01-invoice-total"


class InvoicePackageTests(unittest.TestCase):
    def setUp(self):
        self.benchmark = load_benchmark(ROOT / "benchmarks", DIRECTORY)
        self.options = {"mode": "stub", "deadline_seconds": 30}
        self.identity = freeze_identity(self.benchmark, self.options)
        self.hooks = load_entrypoints(self.benchmark, self.identity)

    def test_plan_is_exactly_the_frozen_fixture_and_probe_plan(self):
        case_data = json.loads((DIRECTORY / "cases.json").read_text())
        legacy = verify.plan(
            "invoice-total", DIRECTORY / "config.yaml", case_data, deadline_budget=30, fixture=fixture()
        )
        with patch.dict(sys.modules, {"verification.fixture_evaluators": None}):
            self.assertEqual(self.hooks.plan(DIRECTORY / "config.yaml", self.options), legacy)
        self.assertEqual(len(legacy["entries"]), 14)
        self.assertFalse((ROOT / "src/sapi_config_lab/bindings.yaml").exists())
        self.assertFalse((ROOT / "src/sapi_config_lab/compile/operations.js").exists())

    def observed(self, root):
        backend = N8nBackend(operation_source=(DIRECTORY / "operations.js").read_text())
        backend.execute = SimulatedN8n((DIRECTORY / "operations.js").read_text()).execute
        plan = self.hooks.plan(DIRECTORY / "config.yaml", self.options)
        observe(
            plan,
            DIRECTORY / "config.yaml",
            root / "evidence",
            runner=partial(run_case, backend=backend),
            bindings=read_bindings(DIRECTORY / "bindings.yaml"),
        )
        return plan

    def evaluate(self, root):
        return self.hooks.evaluate(
            root / "evidence",
            {
                **self.options,
                "submission": str(DIRECTORY / "config.yaml"),
                "evaluation": str(root / "evaluation"),
                "identity": self.identity.sha256,
            },
        )

    def test_full_flow_needs_no_provider_or_fixture_table_entry(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.dict(sys.modules, {"verification.fixture_evaluators": None}):
                self.observed(root)
                result = self.evaluate(root)
            self.assertIs(result["acceptance"], True)
            self.assertIs(result["execution"], True)
            self.assertEqual(result["quality"]["normalized_reward"], 1)
            report = json.loads((root / "evaluation/report.json").read_text())
            self.assertEqual(
                [row["output"]["total_minor"] for row in report["cases"] if row["kind"] == "positive"],
                [38000, 770, 9007199254740991],
            )
            self.assertTrue(all(row["passed"] for row in report["cases"]))
            self.assertEqual(
                report["cases"][0]["verifier_corruptions_rejected"],
                ["wrong-final-output", "output-without-real-result-node", "falsified-step-trace"],
            )

    def test_resealed_adapter_tampering_still_fails_against_native_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.observed(root)
            record = root / "evidence/cases/original-38000/case.json"
            data = json.loads(record.read_text())
            data["output"]["total_minor"] += 1
            record.write_text(json.dumps(data))
            manifest = root / "evidence/observation.json"
            changed = json.loads(manifest.read_text())
            changed["entries"][0]["files"]["case.json"] = hashlib.sha256(record.read_bytes()).hexdigest()
            manifest.write_text(json.dumps(changed))
            self.assertIs(self.evaluate(root)["acceptance"], False)
            report = json.loads((root / "evaluation/report.json").read_text())
            self.assertIn("Adapter output differs", report["error"])

    def test_missing_submission_remains_rejected_with_null_execution_and_quality(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result = self.hooks.evaluate(
                root / "evidence",
                {**self.options, "submission": str(root / "missing.yaml"), "evaluation": str(root / "evaluation")},
            )
            self.assertIs(result["acceptance"], False)
            self.assertIsNone(result["execution"])
            self.assertEqual(result["quality"]["status"], "unscored")
            self.assertIsNone(result["quality"]["normalized_reward"])

    def test_staged_runtime_imports_without_any_legacy_dispatch_or_host_modules(self):
        with tempfile.TemporaryDirectory() as directory:
            task = Path(directory) / "tasks/invoice-total"
            stage_tasks(task.parent, root=ROOT, benchmarks=select_benchmarks(ROOT / "benchmarks", ("invoice-total",)))
            metadata = json.loads((task / "tests/benchmark.json").read_text())
            self.assertEqual(metadata["options"]["deadline_seconds"], 30)
            script = (
                f"import sys; sys.path[:0] = {[str(task / 'tests/core'), str(task / 'tests')]!r}\n"
                "import sapi_config_lab.coordinate.benchmark_worker, payload.evaluation.evaluator\n"
                "print([m for m in sys.modules if any(part in m for part in ('fixture_evaluators', 'coordinate.providers', 'execute.host', 'verification.business'))])\n"
            )
            result = subprocess.run(
                [sys.executable, "-I", "-c", script], cwd=task, capture_output=True, text=True, check=True
            )
            self.assertEqual(result.stdout.strip(), "[]")
            public = task / "environment"
            self.assertFalse((public / "payload/cases.json").exists())
            self.assertFalse((public / "payload/operations.js").exists())
            self.assertFalse((task / "tests/payload/config.yaml").exists())
            self.assertEqual((task / "solution/config.yaml").read_bytes(), (DIRECTORY / "config.yaml").read_bytes())

    def test_live_package_gate_accepts_native_layout_and_rejects_tampering(self):
        scenario = self.benchmark
        selected = {
            scenario.name: {
                "path": scenario.reference.source,
                "sha256": sha256(scenario.reference.source),
                "cases": invoice_cases(),
            }
        }
        for mode in ("oracle", "replay"):
            with tempfile.TemporaryDirectory() as directory:
                tasks = Path(directory) / "tasks"
                stage_tasks(
                    tasks,
                    root=ROOT,
                    benchmarks=(scenario,),
                    mode=mode,
                    submissions={
                        scenario.name: {"path": scenario.reference.source, "sha256": sha256(scenario.reference.source)}
                    }
                    if mode == "replay"
                    else None,
                    cases={scenario.name: invoice_cases()},
                )
                validate_packages(tasks, selected, {scenario.name: scenario})
                task = tasks / scenario.name
                self.assertEqual(json.loads((task / "tests/cases.json").read_text()), {scenario.name: invoice_cases()})
                changed = task / "environment/Dockerfile"
                changed.write_text(changed.read_text() + "\nRUN echo tampered\n")
                with self.assertRaisesRegex(ValueError, "differs from current sources"):
                    validate_packages(tasks, selected, {scenario.name: scenario})

    def test_evaluator_identity_covers_generic_verifier_sources(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            identities = []
            for index, core_hash in enumerate(("a" * 64, "b" * 64)):
                output = root / str(index)
                self.hooks.evaluate(
                    root / "evidence",
                    {
                        **self.options,
                        "submission": str(root / "missing.yaml"),
                        "evaluation": str(output),
                        "identity": {
                            "benchmark": self.identity.sha256,
                            "core_files": {"verification/verify.py": core_hash},
                        },
                    },
                )
                identities.append(json.loads((output / "report.json").read_text())["evaluator"]["sources_sha256"])
            self.assertNotEqual(*identities)

    def test_actual_generation_package_round_trips_selection_and_replay_without_calls(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            report_path = generation_run(root, {"invoice-total": [("only", "2026-10-04T10:00:00Z", True)]})
            (root / "task-packages").rename(root / "synthetic-packages")
            cases = invoice_cases()
            cases["positive"][0]["inputs"]["invoices"][0]["id"] = "PRIVATE-FRESH-INVOICE"
            prompts = stage_tasks(
                root / "task-packages",
                mode="generation",
                root=ROOT,
                benchmarks=select_benchmarks(ROOT / "benchmarks", ("invoice-total",)),
                cases={"invoice-total": cases},
            )
            task = root / "task-packages/invoice-total"
            trial = root / "jobs/generated-1/only"
            (trial / "agent/prompt.txt").write_bytes((task / "instruction.md").read_bytes())
            (trial / "agent/submission.yaml").write_bytes((DIRECTORY / "config.yaml").read_bytes())
            for relative in ("agent/generation.json", "verifier/evaluation/report.json"):
                path = trial / relative
                data = json.loads(path.read_text())
                data["submission_sha256"] = sha256(trial / "agent/submission.yaml")
                if relative.startswith("agent/"):
                    data["prompt_sha256"] = prompts["invoice-total"]
                path.write_text(json.dumps(data))
            report = json.loads(report_path.read_text())
            report["prompt_sha256"] = prompts
            report["private_cases_sha256"] = {"invoice-total": sha256(task / "tests/cases.json")}
            report_path.write_text(json.dumps(report))
            with patch("sapi_config_lab.coordinate.replay.source_manifest", return_value=SOURCES):
                selection = select_submission(report_path, ("invoice-total",))
                selection_path = root / "selection.json"
                selection_path.write_text(json.dumps(selection))
                loaded = load_selection(selection_path)
                self.assertEqual(loaded["invoice-total"]["cases"], cases)
                replay = root / "replay"
                stage_tasks(
                    replay,
                    mode="replay",
                    root=ROOT,
                    benchmarks=select_benchmarks(ROOT / "benchmarks", ("invoice-total",)),
                    submissions=loaded,
                    cases={"invoice-total": loaded["invoice-total"]["cases"]},
                )
                validate_packages(replay, loaded, {self.benchmark.name: self.benchmark})
                case_file = task / "tests/cases.json"
                case_file.write_bytes(case_file.read_bytes() + b"\n")
                with self.assertRaisesRegex(ValueError, "Private fixture hash mismatch"):
                    load_selection(selection_path)
