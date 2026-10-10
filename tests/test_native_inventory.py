"""Shared inventory guards cover an isolated extra task and reject broken task assets."""

import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from sapi_config_lab.coordinate.native_tasks import select_tasks
from sapi_config_lab.paths import workspace_root
from tests import test_native_tasks, test_retirement, test_rubric


class NativeInventoryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        source = workspace_root() / "tasks"
        self.names = {path.parent.name for path in source.glob("*/task.toml")}
        for task in select_tasks(source, sorted(self.names)):
            shutil.copytree(task, self.root / "tasks" / task.name, ignore=shutil.ignore_patterns("__pycache__"))
        self.extra = self.root / "tasks/inventory-probe"
        shutil.copytree(self.root / "tasks/ticket-routing", self.extra)
        for area in ("environment", "tests"):
            dockerfile = self.extra / area / "Dockerfile"
            dockerfile.write_text(dockerfile.read_text().replace("ticket-routing", self.extra.name))
        card = self.extra / "evaluation/rubric.json"
        document = json.loads(card.read_text())
        document["id"] = self.extra.name
        document["version"] = "0.1.0"
        document["criteria"][0]["check_id"] = "probe_accepted"
        card.write_text(json.dumps(document))
        for target, value in (
            ("tests.test_retirement.workspace_root", self.root),
            ("tests.test_rubric.workspace_root", self.root),
            ("sapi_config_lab.coordinate.cli.benchmark_root", self.root / "tasks"),
        ):
            patched = patch(target, return_value=value)
            patched.start()
            self.addCleanup(patched.stop)
        patched = patch.object(test_native_tasks, "ROOT", self.root)
        patched.start()
        self.addCleanup(patched.stop)

    def run_check(self, case, method):
        result = unittest.TestResult()
        case(method).run(result)
        self.assertEqual(result.testsRun, 1)
        return result

    def test_extra_task_passes_shared_inventory_checks_without_registration_or_pins(self):
        root = self.root / "tasks"
        discovered = select_tasks(root, sorted(path.parent.name for path in root.glob("*/task.toml")))
        self.assertEqual({task.name for task in discovered}, self.names | {self.extra.name})
        self.assertIn(self.extra.name, test_rubric.scenario_cards())
        for case, method in (
            (
                test_retirement.RetirementTests,
                "test_production_selection_includes_retained_tasks_and_default_is_invoice_only",
            ),
            (test_retirement.RetirementTests, "test_build_compiles_only_retained_references"),
            (test_native_tasks.NativeTaskTests, "test_discovered_task_config_and_evaluator_assets"),
            (test_native_tasks.NativeTaskTests, "test_harbor_author_environment_has_no_private_compose_overlay"),
            (test_rubric.ImmutabilityTests, "test_a_card_is_read_from_scenario_data_and_cannot_be_altered_in_process"),
            (test_rubric.ArithmeticTests, "test_every_card_weighs_ten_with_a_deterministic_majority"),
            (test_rubric.RewardInvariantTests, "test_binary_cards_keep_the_existing_pass_fail_behaviour"),
        ):
            with self.subTest(check=method):
                result = self.run_check(case, method)
                self.assertTrue(result.wasSuccessful(), result.errors + result.failures)

    def test_extra_default_is_rejected_by_the_fixed_invoice_default_guard(self):
        config = self.extra / "task.toml"
        config.write_text(config.read_text().replace("default = false", "default = true"))
        result = self.run_check(
            test_retirement.RetirementTests,
            "test_production_selection_includes_retained_tasks_and_default_is_invoice_only",
        )
        self.assertTrue(result.failures)

    def test_extra_task_wrong_tags_and_missing_or_invalid_evaluator_fail_static_checks(self):
        for relative, replacement in (
            ("environment/Dockerfile", "FROM sapi-native-ticket-routing-public:phase1\n"),
            ("tests/Dockerfile", "FROM sapi-native-ticket-routing-verifier:phase1\n"),
            ("evaluation/evaluator.py", None),
            ("evaluation/evaluator.py", "def invalid(\n"),
            ("task.toml", "invalid = [\n"),
        ):
            path = self.extra / relative
            original = path.read_bytes()
            with self.subTest(asset=relative, replacement=replacement):
                try:
                    if replacement is None:
                        path.unlink()
                    else:
                        path.write_text(replacement)
                    result = self.run_check(
                        test_native_tasks.NativeTaskTests, "test_discovered_task_config_and_evaluator_assets"
                    )
                    self.assertFalse(result.wasSuccessful())
                finally:
                    path.write_bytes(original)

    def test_extra_task_invalid_card_is_rejected(self):
        path = self.extra / "evaluation/rubric.json"
        document = json.loads(path.read_text())
        document["criteria"][0]["weight"] = 5
        document["criteria"] *= 2
        path.write_text(json.dumps(document))
        result = self.run_check(test_rubric.ArithmeticTests, "test_every_card_weighs_ten_with_a_deterministic_majority")
        self.assertFalse(result.wasSuccessful())

    def test_missing_duplicate_and_unsafe_selections_still_fail(self):
        root = self.root / "tasks"
        for names in ([], ["missing"], [self.extra.name, self.extra.name]):
            with self.subTest(names=names), self.assertRaisesRegex(ValueError, "Unknown, duplicate, or empty"):
                select_tasks(root, names)
        (self.extra / "task.toml").unlink()
        with self.assertRaisesRegex(ValueError, "Unknown"):
            select_tasks(root, [self.extra.name])
        for name in ("invalid_name", "linked-task"):
            task = root / name
            if name == "linked-task":
                task.symlink_to(root / "invoice-total", target_is_directory=True)
            else:
                task.mkdir()
                (task / "task.toml").write_text("")
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, "Unsafe"):
                select_tasks(root, [name])
