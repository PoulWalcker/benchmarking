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

from sapi_config_lab.coordinate.backend import N8nBackend
from sapi_config_lab.coordinate.cases import run_case
from sapi_config_lab.coordinate.native_tasks import invoke
from sapi_config_lab.coordinate.observe import observe
from sapi_config_lab.coordinate.provenance import source_manifest
from sapi_config_lab.coordinate.replay import load_selection, select_submission
from sapi_config_lab.evidence import digest, sha256, write_json
from sapi_config_lab.paths import workspace_root
from sapi_config_lab.profile import read_bindings
from tests.support.invoice import EVALUATOR, card, fixture
from tests.support.invoice import cases as invoice_cases
from tests.support.native import SimulatedN8n
from verification import verify

ROOT = workspace_root()
DIRECTORY = ROOT / "tasks/invoice-total"


class InvoicePackageTests(unittest.TestCase):
    def setUp(self):
        self.options = {"mode": "stub", "deadline_seconds": 30}
        self.identity = {"task": DIRECTORY.name, "sources_sha256": digest(source_manifest())}

    def test_versioned_rubric_card_is_present(self):
        self.assertTrue((DIRECTORY / "evaluation/rubric.json").is_file())
        rubric = card()
        self.assertEqual((rubric.id, rubric.version, rubric.origin), ("invoice-total", "1.0.0", "local"))

    def test_plan_is_exactly_the_frozen_fixture_and_probe_plan(self):
        case_data = json.loads((DIRECTORY / "cases.json").read_text())
        legacy = verify.plan(
            "invoice-total", DIRECTORY / "solution/config.yaml", case_data, deadline_budget=30, fixture=fixture()
        )
        with patch.dict(sys.modules, {"verification.fixture_evaluators": None}):
            self.assertEqual(EVALUATOR.plan(DIRECTORY / "solution/config.yaml", self.options), legacy)
        self.assertEqual(len(legacy["entries"]), 14)
        self.assertFalse((ROOT / "src/sapi_config_lab/bindings.yaml").exists())
        self.assertFalse((ROOT / "src/sapi_config_lab/compile/operations.js").exists())

    def observed(self, root):
        backend = N8nBackend(operation_source=(DIRECTORY / "operations.js").read_text())
        backend.execute = SimulatedN8n((DIRECTORY / "operations.js").read_text()).execute
        plan = EVALUATOR.plan(DIRECTORY / "solution/config.yaml", self.options)
        observe(
            plan,
            DIRECTORY / "solution/config.yaml",
            root / "evidence",
            runner=partial(run_case, backend=backend),
            bindings=read_bindings(DIRECTORY / "bindings.yaml"),
        )
        return plan

    def evaluate(self, root):
        return EVALUATOR.evaluate(
            root / "evidence",
            {
                **self.options,
                "submission": str(DIRECTORY / "solution/config.yaml"),
                "evaluation": str(root / "evaluation"),
                "identity": self.identity,
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
            result = EVALUATOR.evaluate(
                root / "evidence",
                {**self.options, "submission": str(root / "missing.yaml"), "evaluation": str(root / "evaluation")},
            )
            self.assertIs(result["acceptance"], False)
            self.assertIsNone(result["execution"])
            self.assertEqual(result["quality"]["status"], "unscored")
            self.assertIsNone(result["quality"]["normalized_reward"])

    def test_evaluator_identity_covers_generic_verifier_sources(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            identities = []
            for index, core_hash in enumerate(("a" * 64, "b" * 64)):
                output = root / str(index)
                EVALUATOR.evaluate(
                    root / "evidence",
                    {
                        **self.options,
                        "submission": str(root / "missing.yaml"),
                        "evaluation": str(output),
                        "identity": {
                            "task": DIRECTORY.name,
                            "sources_sha256": digest({"verification/verify.py": core_hash}),
                        },
                    },
                )
                identities.append(json.loads((output / "report.json").read_text())["evaluator"]["sources_sha256"])
            self.assertNotEqual(*identities)

    def test_native_evaluator_imports_without_host_or_legacy_dispatch(self):
        script = (
            f"import sys; sys.path[:0] = {[str(ROOT / 'src'), str(ROOT), str(DIRECTORY)]!r}\n"
            "import evaluation.evaluator\n"
            "print([m for m in sys.modules if any(part in m for part in "
            "('benchmark_loading', 'fixture_evaluators', 'coordinate.providers', 'execute.host', 'verification.business'))])\n"
        )
        result = subprocess.run([sys.executable, "-I", "-B", "-c", script], capture_output=True, text=True, check=True)
        self.assertEqual(result.stdout.strip(), "[]")

    def test_native_private_fixtures_round_trip_selection_without_staging(self):
        sources = source_manifest()
        prompt = invoke(DIRECTORY, {"action": "prompt", "catalog": "full"}, sources)["prompt"]
        cases = invoice_cases()
        cases["positive"][0]["inputs"]["invoices"][0]["id"] = "PRIVATE-FRESH-INVOICE"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            trial = root / "jobs/generated-1/only"

            def save(relative, value):
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                write_json(path, value)

            save("source-manifest.json", sources)
            save("inputs/invoice-total/cases.json", {"invoice-total": cases})
            (trial / "agent").mkdir(parents=True)
            (trial / "agent/prompt.txt").write_text(prompt)
            submission = trial / "agent/submission.yaml"
            submission.write_bytes((DIRECTORY / "solution/config.yaml").read_bytes())
            save(
                "jobs/generated-1/only/agent/generation.json",
                {
                    "status": "submitted",
                    "generation_calls": 1,
                    "repairs": 0,
                    "model": "mock-record-only",
                    "expected_model": "mock-record-only",
                    "observed_tool_markers": [],
                    "submission_sha256": sha256(submission),
                    "prompt_sha256": sha256(trial / "agent/prompt.txt"),
                },
            )
            save(
                "jobs/generated-1/only/result.json",
                {
                    "task_name": "invoice-total",
                    "verifier_result": {"rewards": {"reward": 1.0}},
                    "agent_execution": {"started_at": "2026-10-04T10:00:00Z"},
                },
            )
            save(
                "jobs/generated-1/only/verifier/evaluation/report.json",
                {
                    "scenario": "invoice-total",
                    "mode": "stub",
                    "passed": True,
                    "submission_sha256": sha256(submission),
                },
            )
            save("jobs/generated-1/only/verifier/result.json", {"execution": True, "acceptance": True, "quality": None})
            case_file = root / "inputs/invoice-total/cases.json"
            save(
                "report.json",
                {
                    "schema": "sapi-lab-generation/v2",
                    "wrapper_identity": {"model": "mock-record-only"},
                    "source_unchanged": True,
                    "native_tasks": {"invoice-total": str(DIRECTORY)},
                    "trials": [
                        {"scenario": "invoice-total", "passed": True, "result_path": str(trial / "result.json")}
                    ],
                    "prompt_sha256": {"invoice-total": sha256(trial / "agent/prompt.txt")},
                    "private_cases_paths": {"invoice-total": "inputs/invoice-total/cases.json"},
                    "private_cases_sha256": {"invoice-total": sha256(case_file)},
                },
            )
            selection = select_submission(root / "report.json", ("invoice-total",))
            save("selection.json", selection)
            loaded = load_selection(root / "selection.json", copy_to=root / "replay-inputs")["invoice-total"]
            self.assertEqual(loaded["cases"], cases)
            self.assertEqual(
                (root / "replay-inputs/invoice-total/submission.yaml").read_bytes(), submission.read_bytes()
            )
            plan = invoke(
                DIRECTORY,
                {
                    "action": "plan",
                    "submission": str(loaded["path"]),
                    "options": {**self.options, "cases": loaded["cases"]},
                },
                sources,
            )["plan"]
            self.assertEqual(plan["entries"][0]["config"]["workflow"]["inputs"], cases["positive"][0]["inputs"])
            case_file.write_bytes(case_file.read_bytes() + b"\n")
            with self.assertRaisesRegex(ValueError, "Private fixture hash mismatch"):
                load_selection(root / "selection.json")
