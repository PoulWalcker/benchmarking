"""Evaluation judges only a complete, unaltered record of the plan it issued.

No test here starts n8n, Docker or a model: the evidence is written by the
observe step with a fake runner, then evaluated, edited and evaluated again.
"""

import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import unittest

from sapi_config_lab.coordinate.packages import runtime_sources
from sapi_config_lab.coordinate.scenarios import all_cases
from sapi_config_lab.paths import workspace_root
from tests.support.verifying import verify_with_runner
from verification import verify as verifier
from verification.contracts import Rejected

ROOT = workspace_root()
CONFIG = ROOT / "benchmarks/01-invoice-total/config.yaml"
CASES = all_cases()["invoice-total"]


def engine_success(config, artifacts, **options):
    """Claims success; acceptance must still reject it for want of native provenance."""
    return {"status": "success", "output": {"total_minor": 1}, "mapping": {}, "run_data": {}}


def recorded(directory: Path) -> dict[str, str]:
    """Hashes of every evidence file, leaving out what evaluation itself writes."""
    written = {"acceptance.json", "report.json", "evaluation.json"}
    return {
        str(path.relative_to(directory)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(directory.rglob("*"))
        if path.is_file() and path.name not in written
    }


class EvidenceBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.directory = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.directory)
        self.evidence = self.directory / "evidence"
        self.report = verify_with_runner(
            verifier, "invoice-total", CONFIG, self.evidence, runner=engine_success, cases=CASES
        )

    def evaluate(self):
        return verifier.evaluate("invoice-total", CONFIG, self.evidence, CASES)

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
            ["mutated-generated-result", "invalid-yaml-unknown-operation", "invalid-yaml-cycle"]
            + ["invalid-yaml-unsupported-required-parallel"],
        )

    def test_reevaluation_is_deterministic_and_leaves_the_evidence_untouched(self):
        self.assertFalse(self.report["passed"])
        self.assertNotIn("observation", self.report.get("error", ""))
        before = recorded(self.evidence)
        acceptance = (self.evidence / "cases" / CASES["positive"][0]["name"] / "acceptance.json").read_bytes()
        again = self.evaluate()
        self.assertEqual(again, self.report)
        self.assertEqual(recorded(self.evidence), before)
        self.assertEqual(
            (self.evidence / "cases" / CASES["positive"][0]["name"] / "acceptance.json").read_bytes(), acceptance
        )
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
        report = verifier.evaluate("invoice-total", submitted, self.evidence, CASES)
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
            "invoice-total", CONFIG, self.evidence, CASES, runtime_src=ROOT / "src", runtime_manifest=manifest
        )
        self.assertIn("Runtime sources differ", report["error"])
