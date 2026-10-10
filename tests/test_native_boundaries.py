"""Exercise native ownership rules in isolated task trees."""

from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from sapi_config_lab.paths import workspace_root
from tests import test_boundaries as boundaries


class NativeBoundaryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        source = workspace_root()
        shutil.copytree(source / "tasks", self.root / "tasks", ignore=shutil.ignore_patterns("__pycache__"))
        for directory in ("src", "verification"):
            (self.root / directory).symlink_to(source / directory, target_is_directory=True)
        self.task = self.root / "tasks/boundary-probe"
        for relative, content in (
            ("task.toml", ""),
            ("experiment.py", "from sapi_config_lab.coordinate import native_tasks\n"),
            ("tests/main.py", "from payload.evaluation.evaluator import evaluate\n"),
            ("tests/helper.py", "from payload import experiment\n"),
            ("evaluation/__init__.py", ""),
            ("evaluation/evaluator.py", "def evaluate(): pass\n"),
            ("environment/__init__.py", ""),
            ("environment/hooks.py", ""),
        ):
            path = self.task / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
        patched = patch.object(boundaries, "ROOT", self.root)
        patched.start()
        self.addCleanup(patched.stop)

    def run_check(self, case, method):
        result = unittest.TestResult()
        case(method).run(result)
        self.assertEqual(result.testsRun, 1)
        return result

    def test_fifth_task_roots_pass_existing_boundary_and_ownership_checks_without_registration(self):
        for suffix in ("experiment", "tests.main", "tests.helper"):
            with self.subTest(root=suffix):
                self.assertEqual(boundaries.stage_of(f"native_tasks.boundary-probe.{suffix}"), "native_coordinate")
        for case, method in (
            (boundaries.StageBoundaryTests, "test_every_module_has_a_stage"),
            (boundaries.StageBoundaryTests, "test_imports_follow_stage_rules"),
            (
                boundaries.OwnershipTests,
                "test_core_and_verifier_do_not_import_benchmarks_and_only_integration_imports_harbor",
            ),
        ):
            with self.subTest(check=method):
                result = self.run_check(case, method)
                self.assertTrue(result.wasSuccessful(), result.errors + result.failures)

    def test_cross_task_imports_are_rejected_in_static_relative_and_literal_dynamic_forms(self):
        module = "native_tasks.boundary-probe.tests.main"
        for source in (
            "import native_tasks.foreign_task.evaluation",
            "from native_tasks import foreign_task",
            "from ...foreign_task.evaluation import evaluate",
            "__import__('native_tasks.research-report.evaluation.evaluator')",
            "from importlib import import_module as load\nload('native_tasks.research-report.experiment')",
            "import tasks.research_report.evaluation",
        ):
            with self.subTest(source=source):
                self.assertIn((module, "tasks"), boundaries.ownership_edges(module, source))

    def test_foreign_and_undeclared_payload_modules_fail_the_shared_ownership_check(self):
        module = "native_tasks.boundary-probe.tests.main"
        path = self.task / "tests/main.py"
        original = path.read_text()
        for source in (
            "from payload.evaluation.calibration import calibrate",
            "from payload.undeclared import private",
            "from payload import undeclared",
            "from payload.environment import undeclared",
            "import payload.evaluation.evaluator.undeclared",
            "__import__('payload.evaluation.evaluator.undeclared')",
            "from importlib import import_module as load\nload('payload.undeclared')",
        ):
            with self.subTest(source=source):
                try:
                    path.write_text(source)
                    self.assertIn((module, "benchmark_domain"), boundaries.ownership_edges(module, source))
                    result = self.run_check(
                        boundaries.OwnershipTests,
                        "test_core_and_verifier_do_not_import_benchmarks_and_only_integration_imports_harbor",
                    )
                    self.assertTrue(result.failures)
                    self.assertIn("benchmark_domain", result.failures[0][1])
                finally:
                    path.write_text(original)

    def test_domain_modules_cannot_import_coordination(self):
        for area in ("evaluation", "environment"):
            with self.subTest(area=area):
                path = self.task / area / "forbidden.py"
                try:
                    path.write_text("from sapi_config_lab.coordinate.task_worker import main\n")
                    module = f"native_tasks.boundary-probe.{area}.forbidden"
                    self.assertEqual(boundaries.stage_of(module), "benchmark_domain")
                    self.assertIn((module, "coordinate.task_worker"), boundaries.StageBoundaryTests().violations())
                    result = self.run_check(boundaries.StageBoundaryTests, "test_imports_follow_stage_rules")
                    self.assertTrue(result.failures)
                    self.assertIn("coordinate.task_worker", result.failures[0][1])
                finally:
                    path.unlink()

    def test_non_roots_and_nonexistent_roots_never_gain_composition_permissions(self):
        for module in (
            "native_tasks.boundary-probe.tests.missing",
            "native_tasks.missing-task.experiment",
            "native_tasks.missing-task.tests.main",
        ):
            with self.subTest(module=module):
                self.assertEqual(boundaries.stage_of(module), "unclassified_native")
                self.assertIn(
                    (module, "benchmark_domain"), boundaries.ownership_edges(module, "import payload.experiment")
                )
        for relative in ("solution/helper.py", "helper.py", "tests/nested/helper.py"):
            with self.subTest(path=relative):
                path = self.task / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                try:
                    path.write_text("from sapi_config_lab.coordinate import task_worker\n")
                    module = "native_tasks.boundary-probe." + relative.removesuffix(".py").replace("/", ".")
                    self.assertEqual(boundaries.stage_of(module), "unclassified_native")
                    self.assertIn((module, "coordinate.task_worker"), boundaries.StageBoundaryTests().violations())
                    result = self.run_check(boundaries.StageBoundaryTests, "test_every_module_has_a_stage")
                    self.assertTrue(result.failures)
                    self.assertIn(module, result.failures[0][1])
                finally:
                    path.unlink()

    def test_symlink_escapes_cannot_grant_root_or_payload_permissions(self):
        foreign = self.root / "tasks/research-report"
        for relative, target, source in (
            ("environment/escape.py", foreign / "experiment.py", "import payload.environment.escape"),
            ("escape", foreign / "evaluation", "from payload.escape import evaluator"),
        ):
            path = self.task / relative
            path.symlink_to(target)
            self.assertIn(
                ("native_tasks.boundary-probe.tests.main", "benchmark_domain"),
                boundaries.ownership_edges("native_tasks.boundary-probe.tests.main", source),
            )
        root = self.task / "tests/escape.py"
        root.symlink_to(foreign / "experiment.py")
        self.assertEqual(boundaries.stage_of("native_tasks.boundary-probe.tests.escape"), "unclassified_native")
        (self.root / "tasks/linked-task").symlink_to(foreign, target_is_directory=True)
        self.assertEqual(boundaries.stage_of("native_tasks.linked-task.experiment"), "unclassified_native")

    def test_local_modules_packages_and_symbols_are_allowed_only_at_discovered_roots(self):
        module = "native_tasks.boundary-probe.tests.main"
        for source in (
            "import payload",
            "from payload import experiment",
            "import payload.environment",
            "from payload.environment import hooks",
            "from payload.evaluation.evaluator import evaluate",
            "__import__('payload.experiment')",
        ):
            with self.subTest(source=source):
                self.assertFalse(boundaries.ownership_edges(module, source))
                for non_root in (
                    "native_tasks.boundary-probe.evaluation.evaluator",
                    "native_tasks.boundary-probe.solution.helper",
                    "sapi_config_lab.coordinate.native_tasks",
                    "verification.native",
                ):
                    self.assertIn((non_root, "benchmark_domain"), boundaries.ownership_edges(non_root, source))

    def test_harbor_and_task_implementations_remain_inaccessible_to_verification(self):
        for module in ("verification.native", "native_tasks.boundary-probe.tests.main"):
            for source in (
                "from harbor.models.task import Task",
                "__import__('harbor.models.task')",
                "from importlib import import_module as load\nload('harbor.models.task')",
            ):
                with self.subTest(module=module, source=source):
                    self.assertIn((module, "harbor"), boundaries.ownership_edges(module, source))
        module = "verification.native"
        self.assertIn(
            (module, "tasks"),
            boundaries.ownership_edges(module, "__import__('native_tasks.boundary-probe.evaluation.evaluator')"),
        )
        self.assertNotIn("benchmark_domain", boundaries.ALLOWED["verification"])
