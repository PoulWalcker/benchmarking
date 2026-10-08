"""Checkout-owned scoring preserves sealed evidence and saved judge identities offline."""

import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tarfile
import tempfile
from types import MappingProxyType
import unittest
from unittest.mock import patch

from sapi_config_lab.paths import workspace_root
from tests.support.pinned import AVAILABLE, SOURCE

ROOT = workspace_root()
DIRECTORY = ROOT / "benchmarks/10-checkout-recovery/evaluation"
ARCHIVE = ROOT / "evidence/migration-01-baseline/historical/hosted-before-native-fields.tar.gz"
PACKAGE = "_checkout_package_evaluator_tests"
SPEC = importlib.util.spec_from_file_location(
    PACKAGE, DIRECTORY / "evaluator.py", submodule_search_locations=[str(DIRECTORY)]
)
EVALUATOR = importlib.util.module_from_spec(SPEC)
sys.modules[PACKAGE] = EVALUATOR
SPEC.loader.exec_module(EVALUATOR)
SCORING = sys.modules[PACKAGE + ".scoring"]
CALIBRATION = __import__(PACKAGE + ".calibration", fromlist=["calibration_fixture"])


class CheckoutEvaluatorOwnershipTests(unittest.TestCase):
    def test_checkout_calibration_retains_only_its_own_exact_narratives(self):
        actual = json.loads((DIRECTORY / "judge-calibration.json").read_text())
        # The digest pins checkout's subset of the audited baseline calibration document.
        self.assertEqual(
            hashlib.sha256(json.dumps(actual, sort_keys=True).encode()).hexdigest(),
            "55e7edc0237a1715d69bd4d30068e50a914f210b9216d23bde80763974633bf0",
        )

    def test_adapter_has_no_global_evaluator_provider_or_world_dependency(self):
        source = "\n".join(path.read_text() for path in DIRECTORY.glob("*.py"))
        for forbidden in (
            "sapi_config_lab.evaluate",
            "sapi_config_lab.execute",
            "sapi_config_lab.coordinate",
            "autowfbench.runtime.environment",
            "ChallengeEnvironment",
            "crm-lead-qualification",
        ):
            self.assertNotIn(forbidden, source)


@unittest.skipUnless(AVAILABLE, "Requires verified external AutoWFBench checkout and benchmark extra")
class CheckoutPackageEvaluatorTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.evidence = self.root / "evidence"
        self.evidence.mkdir()
        with tarfile.open(ARCHIVE) as archive:
            prefix = "hosted-before-native-fields/"
            self.saved = {
                name: json.load(archive.extractfile(prefix + "evaluation/" + name))
                for name in ("task-contract.json", "judge-reply.json", "run-log.json", "evaluation.json")
            }
            for name in ("trial.json", "environment-evidence.json"):
                (self.evidence / name).write_bytes(archive.extractfile(prefix + "evidence/" + name).read())
        self.options = {
            "source_manifest": str(SOURCE.manifest),
            "source_root": str(SOURCE.root),
            "evaluation": str(self.root / "evaluation"),
            "contract": self.saved["task-contract.json"],
            "judgement": self.saved["judge-reply.json"],
        }

    def test_saved_judgement_recreates_exact_score_and_run_without_world_or_dispatch(self):
        before = {path.name: path.read_bytes() for path in self.evidence.iterdir()}
        with patch.object(SCORING, "judge", side_effect=AssertionError("Unexpected judge dispatch")):
            result = EVALUATOR.evaluate(self.evidence, MappingProxyType(self.options))
        self.assertEqual(
            result,
            {
                "execution": True,
                "acceptance": True,
                "harbor_reward": 0.732,
                "quality": {"status": "complete", "score_0_10": 7.32, "normalized_reward": 0.732},
            },
        )
        self.assertEqual(before, {path.name: path.read_bytes() for path in self.evidence.iterdir()})
        for name in ("task-contract.json", "run-log.json"):
            self.assertEqual(json.loads((self.root / "evaluation" / name).read_text()), self.saved[name])
        report = json.loads((self.root / "evaluation/evaluation.json").read_text())
        diagnostic = report.pop("project_acceptance")
        historical = {key: value for key, value in self.saved["evaluation.json"].items() if key != "project_acceptance"}
        self.assertEqual(report, historical)
        self.assertTrue(diagnostic["passed"])
        self.assertFalse(diagnostic["affects_upstream_score"])
        self.assertEqual((self.root / "evaluation/reward.txt").read_text(), "0.732\n")
        self.assertFalse((self.root / "evaluation/judge-dispatch.json").exists())
        acceptance = json.loads((self.root / "evaluation/report.json").read_text())
        self.assertEqual(acceptance["schema"], "sapi-lab-upstream-acceptance/v1")
        self.assertEqual(acceptance["result"], {key: value for key, value in result.items() if key != "harbor_reward"})
        self.assertTrue(acceptance["passed"])
        self.assertIsNone(acceptance["native_execution"])
        self.assertIsNone(acceptance["terminal_completion"])

    def test_saved_paid_judge_identity_can_rescore_without_dispatch(self):
        contract = SCORING.freeze_contract(
            SOURCE, "production-checkout-recovery", judge_mode="codex", judge_model="saved-only-model"
        )
        self.options["contract"] = contract.as_dict()
        self.options["judgement"]["provenance"].update(mode="codex", model="saved-only-model")
        with patch.object(SCORING, "judge", side_effect=AssertionError("Unexpected paid dispatch")):
            result = EVALUATOR.evaluate(self.evidence, self.options)
        self.assertEqual(result["harbor_reward"], 0.732)
        report = json.loads((self.root / "evaluation/task-contract.json").read_text())
        self.assertEqual(report["judge"], contract.as_dict()["judge"])

    def test_native_and_terminal_facts_are_copied_without_reinterpretation(self):
        trial = json.loads((self.evidence / "trial.json").read_text())
        trial.update(native_execution=False, terminal_completion=True)
        (self.evidence / "trial.json").write_text(json.dumps(trial))
        EVALUATOR.evaluate(self.evidence, self.options)
        report = json.loads((self.root / "evaluation/report.json").read_text())
        self.assertIs(report["native_execution"], False)
        self.assertIs(report["terminal_completion"], True)
        self.assertTrue(report["result"]["execution"])

    def test_local_replay_resolves_verified_cache_from_benchmark_root(self):
        self.options.pop("source_root")
        result = EVALUATOR.evaluate(self.evidence, self.options)
        self.assertEqual(result["harbor_reward"], 0.732)

    def test_local_demo_judge_preserves_reference_reward(self):
        self.options.pop("judgement")
        result = EVALUATOR.evaluate(self.evidence, self.options)
        self.assertEqual(result["quality"]["normalized_reward"], 0.732)
        self.assertEqual(json.loads((self.root / "evaluation/judge-dispatch.json").read_text())["attempts"], 1)

    def test_missing_judge_remains_null_even_when_business_checks_pass(self):
        self.options.pop("judgement")
        self.options["dispatch"] = False
        result = EVALUATOR.evaluate(self.evidence, self.options)
        self.assertTrue(result["acceptance"])
        self.assertIsNone(result["quality"]["score_0_10"])
        self.assertIsNone(result["quality"]["normalized_reward"])
        self.assertIsNone(result["harbor_reward"])
        self.assertFalse((self.root / "evaluation/reward.txt").exists())

    def test_empty_failed_candidate_cannot_pass_or_get_manufactured_zero(self):
        trial = json.loads((self.evidence / "trial.json").read_text())
        trial.update(submission=None, termination_reason="solution_failed")
        (self.evidence / "trial.json").write_text(json.dumps(trial))
        environment = json.loads((self.evidence / "environment-evidence.json").read_text())
        environment.update(events=[], verification=[], final=environment["initial"])
        environment["checks"] = dict.fromkeys(environment["checks"], False)
        (self.evidence / "environment-evidence.json").write_text(json.dumps(environment))
        self.options.pop("judgement")
        result = EVALUATOR.evaluate(self.evidence, self.options)
        self.assertFalse(result["execution"])
        self.assertFalse(result["acceptance"])
        self.assertIsNone(result["quality"]["normalized_reward"])
        self.assertIsNone(result["harbor_reward"])
        self.assertFalse((self.root / "evaluation/reward.txt").exists())

    def test_terminal_completion_does_not_claim_business_acceptance(self):
        environment = json.loads((self.evidence / "environment-evidence.json").read_text())
        environment["checks"] = dict.fromkeys(environment["checks"], False)
        (self.evidence / "environment-evidence.json").write_text(json.dumps(environment))
        self.options.pop("judgement")
        result = EVALUATOR.evaluate(self.evidence, self.options)
        self.assertTrue(result["execution"])
        self.assertFalse(result["acceptance"])
        self.assertEqual(result["quality"]["normalized_reward"], 0.132)

    def test_foreign_saved_judge_and_changed_contract_fail_before_output(self):
        for field in ("judgement", "contract"):
            options = copy.deepcopy(self.options)
            if field == "judgement":
                options[field]["provenance"]["run_log_digest"] = "foreign-record"
            else:
                options[field]["source"]["revision"] = "0" * 40
            with self.subTest(field=field), self.assertRaises(ValueError):
                EVALUATOR.evaluate(self.evidence, options)
            self.assertFalse((self.root / "evaluation").exists())

    def test_source_mutation_is_rejected_before_output(self):
        manifest = json.loads(SOURCE.manifest.read_text())
        manifest["files"]["autowfbench/core/scoring.py"] = "0" * 64
        source_manifest = self.root / "changed-source.json"
        source_manifest.write_text(json.dumps(manifest))
        self.options["source_manifest"] = str(source_manifest)
        with self.assertRaisesRegex(ValueError, "Pinned source hash mismatch"):
            EVALUATOR.evaluate(self.evidence, self.options)
        self.assertFalse((self.root / "evaluation").exists())

    def test_paid_dispatch_and_writing_into_evidence_are_rejected(self):
        options = {**self.options, "judge_mode": "codex", "judge_model": "not-dispatched", "dispatch": True}
        options.pop("contract")
        options.pop("judgement")
        with self.assertRaisesRegex(ValueError, "host reservation"):
            EVALUATOR.evaluate(self.evidence, options)
        with self.assertRaisesRegex(ValueError, "outside original evidence"):
            EVALUATOR.evaluate(self.evidence, {**self.options, "evaluation": str(self.evidence / "derived")})
        self.assertFalse((self.root / "evaluation").exists())

    def test_calibration_changes_prose_without_changing_recorded_business_state(self):
        contract = SCORING.freeze_contract(SOURCE, "production-checkout-recovery", judge_mode="demo")
        fixture = CALIBRATION.calibration_fixture(contract, self.saved["run-log.json"], "contentless")
        self.assertEqual(fixture["run_log"]["checks"], self.saved["run-log.json"]["checks"])
        self.assertEqual(fixture["run_log"]["snapshots"], self.saved["run-log.json"]["snapshots"])
        self.assertIsNone(CALIBRATION.compare_calibration(fixture, None)["passed"])
        self.assertFalse(fixture["affects_candidate_score"])
