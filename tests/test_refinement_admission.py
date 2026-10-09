"""Generic bounded refinement grants and exhaustion; no benchmark registry or paid calls."""

from collections import Counter
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from tests.support.refinement import definition, verify_refinement
from tests.test_refinement_verification import evidence
from verification.contracts import require
from verification.refinement import refinement_model_calls


class RefinementAdmissionTests(unittest.TestCase):
    def test_grant_reserves_every_possible_model_attempt(self):
        config = definition()
        occurrences = refinement_model_calls(config)
        self.assertEqual(list(occurrences), [f"numeric-refinement/r1/draft/attempt{n}" for n in (1, 2, 3)])
        self.assertEqual(dict(Counter(occurrences.values())), {"probe.advance": 3})
        config["execution"]["refinement"]["max_attempts"] = 2
        self.assertEqual(len(refinement_model_calls(config)), 2)
        config["workflow"]["steps"][0]["kind"] = "Script"
        self.assertEqual(refinement_model_calls(config), {})

    def test_expected_exhaustion_keeps_output_null(self):
        config, run = evidence([2] * 3, limit=1)
        result = verify_refinement(config, run)
        self.assertTrue(result["exhausted"])
        self.assertIsNone(result["output"])
        with self.assertRaises(AssertionError):
            require(not result["exhausted"], "Expected acceptance")


class IsolatedRefinementTests(unittest.TestCase):
    @unittest.skipIf(os.environ.get("SAPI_REFINEMENT_ISOLATED") == "1", "already running without benchmarks")
    def test_suites_run_without_any_production_benchmark_directory(self):
        root = Path(__file__).parents[1]
        with tempfile.TemporaryDirectory() as directory:
            isolated = Path(directory)
            shutil.copytree(root / "src", isolated / "src", ignore=shutil.ignore_patterns("__pycache__"))
            shutil.copytree(
                root / "verification", isolated / "verification", ignore=shutil.ignore_patterns("__pycache__")
            )
            tests = isolated / "tests"
            tests.mkdir()
            for name in (
                "__init__.py",
                "test_refinement.py",
                "test_refinement_admission.py",
                "test_refinement_verification.py",
                "test_occurrence_budget.py",
            ):
                shutil.copyfile(root / "tests" / name, tests / name)
            support = tests / "support"
            support.mkdir()
            shutil.copytree(
                root / "tests/support/refinement", support / "refinement", ignore=shutil.ignore_patterns("__pycache__")
            )
            shutil.copyfile(root / "tests/support/run-refinement-export.mjs", support / "run-refinement-export.mjs")
            env = {
                **os.environ,
                "PYTHONPATH": str(isolated / "src") + os.pathsep + str(isolated),
                "SAPI_REFINEMENT_ISOLATED": "1",
                "SAPI_RUN_DOCKER_TESTS": "0",
            }
            self.assertFalse((isolated / "tasks").exists())
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "unittest",
                    "tests.test_refinement",
                    "tests.test_refinement_admission",
                    "tests.test_refinement_verification",
                    "tests.test_occurrence_budget",
                    "-v",
                ],
                cwd=isolated,
                env=env,
                capture_output=True,
                text=True,
                timeout=30,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("OK (skipped=2)", result.stderr)
