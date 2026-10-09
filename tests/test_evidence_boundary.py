"""Evaluation judges only a complete, unaltered record of the plan it issued.

No test here starts n8n, Docker or a model: the evidence is written by the
observe step with a fake runner or the simulated engine adapter, then
evaluated, edited and evaluated again.
"""

import functools
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import unittest

from sapi_config_lab.coordinate.cases import run_case
from sapi_config_lab.paths import workspace_root
from sapi_config_lab.profile import read_bindings
from tests.support.invoice import CATALOG, OPERATION_SOURCE, cases
from tests.support.invoice import fixture as invoice_fixture
from tests.support.native import SimulatedN8n
from tests.support.verifying import verify_with_runner
from verification import verify as verifier
from verification.contracts import Rejected

ROOT = workspace_root()
CONFIG = ROOT / "tasks/invoice-total/solution/config.yaml"
CASES = cases()


def engine_success(config, artifacts, **options):
    """Claims success; acceptance must still reject it for want of native provenance."""
    return {"status": "success", "output": {"total_minor": 1}, "mapping": {}, "run_data": {}}


def recorded(directory: Path) -> dict[str, str]:
    """Hashes of every evidence file; evaluation writes none of them."""
    return {
        str(path.relative_to(directory)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(directory.rglob("*"))
        if path.is_file()
    }


def runtime_sources(root):
    suffixes = {".py", ".js", ".yaml"}
    return {
        "suffixes": sorted(suffixes),
        "files": {
            path.relative_to(root / "src").as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in (root / "src").rglob("*")
            if path.is_file() and "__pycache__" not in path.parts and path.suffix in suffixes
        },
    }


class EvidenceBoundaryTests(unittest.TestCase):
    """A run that claims success without native records never reaches judgement."""

    def setUp(self):
        self.directory = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.directory)
        self.evidence = self.directory / "evidence"
        self.report = verify_with_runner(
            verifier,
            "invoice-total",
            CONFIG,
            self.directory,
            runner=engine_success,
            cases=CASES,
            fixture=invoice_fixture(),
            bindings=read_bindings(CATALOG),
        )

    def evaluate(self):
        return verifier.evaluate("invoice-total", CONFIG, self.evidence, CASES, fixture=invoice_fixture())

    def manifest(self) -> dict:
        return json.loads((self.evidence / "observation.json").read_text())

    def rewrite_manifest(self, manifest: dict) -> None:
        (self.evidence / "observation.json").write_text(json.dumps(manifest))

    def test_the_plan_names_every_case_and_every_probe(self):
        names = [entry["name"] for entry in self.manifest()["entries"]]
        expected = [case["name"] for case in CASES["positive"] + CASES["negative"]]
        self.assertEqual(names[: len(expected)], expected)
        self.assertEqual(
            names[len(expected) :],
            [
                "mutated-generated-result",
                "invalid-yaml-unknown-operation",
                "invalid-yaml-cycle",
                "invalid-yaml-unsupported-required-parallel",
            ],
        )

    def test_reevaluation_is_deterministic_and_leaves_the_evidence_untouched(self):
        self.assertFalse(self.report["passed"])
        self.assertNotIn("observation", self.report.get("error", ""))
        before = recorded(self.evidence)
        again = self.evaluate()
        self.assertEqual(again, self.report)
        self.assertEqual(recorded(self.evidence), before)
        # Rejected before any judgement, so no case decision was written.
        self.assertFalse((self.directory / "evaluation/cases").exists())
        case = json.loads((self.evidence / "cases" / CASES["positive"][0]["name"] / "case.json").read_text())
        self.assertNotIn("acceptance", case)

    def test_no_observation_is_never_a_pass(self):
        (self.evidence / "observation.json").unlink()
        report = self.evaluate()
        self.assertFalse(report["passed"])
        self.assertIn("No recorded observation", report["error"])

    def test_an_incomplete_observation_is_rejected(self):
        manifest = self.manifest()
        manifest["entries"].pop(0)
        self.rewrite_manifest(manifest)
        self.assertIn("incomplete or out of order", self.evaluate()["error"])
        manifest["entries"].insert(0, {"name": CASES["positive"][0]["name"], "files": {}})
        self.rewrite_manifest(manifest)
        self.assertIn("Recorded case is incomplete", self.evaluate()["error"])

    def test_evidence_edited_after_collection_is_rejected(self):
        path = self.evidence / "cases" / CASES["positive"][0]["name"] / "case.json"
        path.write_text(path.read_text().replace('"success"', '"error"', 1))
        self.assertIn("changed after collection", self.evaluate()["error"])

    def test_a_record_of_other_fixture_inputs_is_rejected_even_with_a_consistent_manifest(self):
        name = CASES["positive"][0]["name"]
        config = self.evidence / "cases" / name / "config.json"
        executed = json.loads(config.read_text())
        executed["workflow"]["inputs"] = CASES["positive"][1]["inputs"]
        config.write_text(json.dumps(executed))
        manifest = self.manifest()
        manifest["entries"][0]["files"]["config.json"] = hashlib.sha256(config.read_bytes()).hexdigest()
        self.rewrite_manifest(manifest)
        self.assertIn("differs from the planned fixture", self.evaluate()["error"])

    def test_an_observation_of_another_plan_or_submission_is_rejected(self):
        manifest = self.manifest()
        manifest["plan_sha256"] = "0" * 64
        self.rewrite_manifest(manifest)
        self.assertIn("not the plan this verifier issued", self.evaluate()["error"])
        submitted = self.directory / "other.yaml"
        submitted.write_text(CONFIG.read_text() + "\n# different bytes\n")
        report = verifier.evaluate("invoice-total", submitted, self.evidence, CASES, fixture=invoice_fixture())
        self.assertFalse(report["passed"])

    def test_a_changed_runtime_is_rejected_before_any_judgement(self):
        manifest = self.directory / "runtime-sources.json"
        manifest.write_text(json.dumps(runtime_sources(ROOT)))
        verifier.check_runtime_sources(ROOT / "src", manifest)
        changed = runtime_sources(ROOT)
        changed["files"]["sapi_config_lab/compile/n8n.py"] = "0" * 64
        manifest.write_text(json.dumps(changed))
        with self.assertRaisesRegex(Rejected, "Runtime sources differ"):
            verifier.check_runtime_sources(ROOT / "src", manifest)
        report = verifier.evaluate(
            "invoice-total",
            CONFIG,
            self.evidence,
            CASES,
            runtime_src=ROOT / "src",
            runtime_manifest=manifest,
            fixture=invoice_fixture(),
        )
        self.assertIn("Runtime sources differ", report["error"])

    def test_a_success_without_native_records_is_rejected_before_judgement(self):
        self.assertIn("lacks native artifacts", self.report["error"])


class RecordedRunTests(unittest.TestCase):
    """A complete accepted record, from the simulated engine adapter."""

    @classmethod
    def setUpClass(cls):
        cls.source = Path(tempfile.mkdtemp())
        runner = functools.partial(run_case, backend=SimulatedN8n(operation_source=OPERATION_SOURCE))
        cls.report = verify_with_runner(
            verifier,
            "invoice-total",
            CONFIG,
            cls.source,
            runner=runner,
            cases=CASES,
            fixture=invoice_fixture(),
            bindings=read_bindings(CATALOG),
        )

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.source)

    def setUp(self):
        # Every probe edits its own copy; the recorded original is never touched.
        self.directory = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.directory)
        shutil.copytree(self.source / "evidence", self.directory / "evidence")
        self.evidence = self.directory / "evidence"
        self.case = self.evidence / "cases" / CASES["positive"][0]["name"]

    def evaluate(self):
        return verifier.evaluate("invoice-total", CONFIG, self.evidence, CASES, fixture=invoice_fixture())

    def rehash(self, name: str) -> None:
        """Rewrite the manifest so it agrees with an edited or removed file."""
        manifest = json.loads((self.evidence / "observation.json").read_text())
        files = manifest["entries"][0]["files"]
        if (self.case / name).exists():
            files[name] = hashlib.sha256((self.case / name).read_bytes()).hexdigest()
        else:
            files.pop(name)
        (self.evidence / "observation.json").write_text(json.dumps(manifest))

    def test_an_accepted_run_evaluates_twice_without_changing_its_evidence(self):
        self.assertTrue(self.report["passed"], self.report.get("error"))
        before = recorded(self.evidence)
        first = self.evaluate()
        second = self.evaluate()
        self.assertTrue(first["passed"])
        self.assertEqual(first, second)
        self.assertEqual(recorded(self.evidence), before)
        decision = json.loads((self.directory / "evaluation/cases" / self.case.name / "acceptance.json").read_text())
        self.assertTrue(decision["passed"])
        self.assertEqual(decision["evaluator_sha256"], first["evaluator"]["sources_sha256"])
        self.assertEqual(set(decision["evidence"]), set(recorded(self.case)))

    def test_a_compile_error_needs_no_native_execution_artifacts(self):
        rejected = self.evidence / "cases/invalid-yaml-cycle"
        self.assertEqual(sorted(path.name for path in rejected.iterdir()), ["case.json", "config.json"])
        self.assertTrue(self.evaluate()["passed"])

    def test_an_edited_native_record_is_rejected(self):
        persisted = self.case / "execution.persisted.json"
        persisted.write_text(persisted.read_text().replace("38000", "38001"))
        self.assertIn("changed after collection", self.evaluate()["error"])
        self.rehash("execution.persisted.json")
        self.assertIn("Persisted native record differs", self.evaluate()["error"])

    def test_an_edited_workflow_is_rejected_even_with_a_consistent_manifest(self):
        workflow = self.case / "workflow.json"
        workflow.write_text(workflow.read_text().replace('"active": false', '"active": true'))
        self.rehash("workflow.json")
        self.assertIn("Recorded workflow differs from its hash", self.evaluate()["error"])

    def test_a_missing_native_artifact_is_rejected_even_when_unlisted(self):
        (self.case / "execution.metadata.json").unlink()
        self.assertIn("changed after collection", self.evaluate()["error"])
        self.rehash("execution.metadata.json")
        self.assertIn("lacks native artifacts", self.evaluate()["error"])

    def test_extra_files_are_rejected_wherever_they_appear(self):
        (self.case / "planted.json").write_text("{}")
        self.assertIn("changed after collection", self.evaluate()["error"])
        (self.case / "planted.json").unlink()
        (self.evidence / "planted.json").write_text("{}")
        self.assertIn("holds files no entry recorded", self.evaluate()["error"])

    def test_extracted_run_data_must_match_every_native_record(self):
        case = json.loads((self.case / "case.json").read_text())
        case["run_data"]["Result"][0]["executionTime"] = 2
        (self.case / "case.json").write_text(json.dumps(case))
        self.rehash("case.json")
        self.assertIn("differs from the extracted run data", self.evaluate()["error"])
