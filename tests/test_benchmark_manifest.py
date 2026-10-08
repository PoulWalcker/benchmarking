"""Directory-only descriptors, explicit closures and isolated selected imports."""

from dataclasses import FrozenInstanceError, replace
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import ModuleType
import unittest

from sapi_config_lab.benchmark import MANIFEST_VERSION, discover_benchmarks, load_benchmark
from sapi_config_lab.benchmark_loading import freeze_identity, load_entrypoints
from sapi_config_lab.coordinate.benchmark_discovery import select_benchmarks
from sapi_config_lab.paths import workspace_root

ROOT = workspace_root()


class ManifestTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.directory, self.meta = self.package("invoice-total")

    def write(self, relative, content=""):
        path = self.directory / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        return path

    def package(self, name):
        directory = self.root / name
        directory.mkdir()
        contents = {
            "task.md": "Public problem only",
            "bindings.yaml": "operations: {}",
            "config.yaml": "Private reference",
            "runtime/operations.js": "// trusted implementation",
            "task.toml": "[verifier]\ntimeout_sec = 6000\n",
            "evaluation/cases.json": "{}",
            "evaluation/evaluator.py": (
                "from pathlib import Path\n"
                "Path(__file__).with_name('imported').touch()\n"
                "def plan(submission, options):\n    return {'submission': str(submission)}\n"
                f"def evaluate(evidence, options):\n    return {{'owner': '{name}', 'quality': None}}\n"
            ),
        }
        for relative, content in contents.items():
            path = directory / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
        meta = {
            "version": MANIFEST_VERSION,
            "id": name,
            "default": True,
            "public": ["task.md", "bindings.yaml"],
            "trusted": ["runtime/operations.js", "task.toml", "evaluation/cases.json", "evaluation/evaluator.py"],
            "reference": "config.yaml",
            "bindings": "bindings.yaml",
            "operations": "runtime/operations.js",
            "harbor_task": "task.toml",
            "entrypoints": {
                "plan": {"path": "evaluation/evaluator.py", "symbol": "plan"},
                "evaluate": {"path": "evaluation/evaluator.py", "symbol": "evaluate"},
            },
            "dependencies": {},
            "controls": {"oracle_acceptance": True, "reference_reward": None},
            "budgets": {"authoring_attempts": None, "runtime_model_calls": None, "judge_calls": 0},
            "config": {"cases": "evaluation/cases.json", "nested": [{"rule": "sum"}]},
        }
        (directory / "scenario.json").write_text(json.dumps(meta))
        return directory, meta

    def load(self):
        (self.directory / "scenario.json").write_text(json.dumps(self.meta))
        return load_benchmark(self.root, self.directory)

    def selected(self):
        benchmark = self.load()
        return load_entrypoints(benchmark, freeze_identity(benchmark, {"seed": 1, "mode": "stub"}))

    def test_listing_is_metadata_only_and_descriptor_is_deeply_immutable(self):
        self.package("checkout-recovery")
        discovered = discover_benchmarks(self.root)
        self.assertEqual({item.name for item in discovered}, {"invoice-total", "checkout-recovery"})
        self.assertFalse(list(self.root.glob("*/evaluation/imported")))
        descriptor = self.load()
        with self.assertRaises(FrozenInstanceError):
            descriptor.default = False
        with self.assertRaises(TypeError):
            descriptor.config["nested"][0]["rule"] = "changed"
        with self.assertRaises(TypeError):
            descriptor.entrypoints["plan"] = descriptor.entrypoints["evaluate"]
        self.assertIsNone(descriptor.controls.reference_reward)
        self.assertIsNone(descriptor.budgets.authoring_attempts)
        self.assertEqual(descriptor.reference.visibility, "reference")

    def test_checkout_hooks_and_opaque_business_configuration(self):
        self.meta["default"] = False
        self.meta["config"] = {
            "provenance": {"source": "new-source", "challenge": "unregistered"},
            "workflow_id": "checkout-workflow",
            "output": {"artifact_field": "incident_summary"},
            "completion": {"requires_receipt": True},
        }
        self.meta["controls"]["reference_reward"] = 0.732
        self.meta["budgets"] = {"authoring_attempts": 2, "runtime_model_calls": 4, "judge_calls": 1}
        self.write(
            "evaluation/environment.py",
            "from sapi_config_lab.contracts import RunBinding\n"
            "def prepare(context):\n    return RunBinding(deadline_at=context['deadline'])\n"
            "def snapshot(context):\n    return {'terminal': None}\n",
        )
        self.meta["trusted"].append("evaluation/environment.py")
        self.meta["entrypoints"].update(
            {role: {"path": "evaluation/environment.py", "symbol": role} for role in ("prepare", "snapshot")}
        )
        selected = self.selected()
        self.assertEqual(selected.prepare({"deadline": 123}).deadline_at, 123)
        self.assertIsNone(selected.snapshot({})["terminal"])
        self.assertEqual(self.load().budgets.judge_calls, 1)

    def test_only_selected_local_callables_load_with_same_filename_isolation(self):
        other, _ = self.package("checkout-recovery")
        first = self.selected()
        self.assertFalse((other / "evaluation/imported").exists())
        descriptor = load_benchmark(self.root, other)
        second = load_entrypoints(descriptor, freeze_identity(descriptor, {}))
        self.assertNotEqual(first.package, second.package)
        self.assertEqual(first.evaluate(Path("record"), {})["owner"], "invoice-total")
        self.assertEqual(second.evaluate(Path("record"), {})["owner"], "checkout-recovery")
        self.assertIsNone(first.prepare)
        self.assertEqual(first.plan(Path("submission.yaml"), {}), {"submission": "submission.yaml"})
        self.assertNotIn(str(self.directory), sys.path)

    def test_same_id_under_different_roots_is_isolated(self):
        first = self.selected()
        old_root = self.root
        self.root = old_root / "other-root"
        self.root.mkdir()
        other, _ = self.package("invoice-total")
        descriptor = load_benchmark(self.root, other)
        second = load_entrypoints(descriptor, freeze_identity(descriptor, {"seed": 1, "mode": "stub"}))
        self.assertEqual(first.identity, second.identity)
        self.assertNotEqual(first.package, second.package)

    def test_duplicate_ids_and_unsupported_versions_fail(self):
        other, meta = self.package("other")
        meta["id"] = "invoice-total"
        (other / "scenario.json").write_text(json.dumps(meta))
        with self.assertRaisesRegex(ValueError, "Duplicate benchmark id"):
            discover_benchmarks(self.root)
        self.meta["version"] = "future/v99"
        with self.assertRaisesRegex(ValueError, "Unsupported benchmark version"):
            self.load()

    def test_bad_paths_and_missing_files_fail(self):
        for path in ("/tmp/private", "../private", "task.md/../task.md", "./task.md", "missing", "runtime//x", "x\\y"):
            with self.subTest(path=path):
                self.meta["public"] = [path]
                with self.assertRaisesRegex(ValueError, "path|file"):
                    self.load()

    def test_symlink_files_directories_and_benchmark_roots_fail(self):
        (self.directory / "linked").symlink_to(self.directory / "task.md")
        self.meta["public"].append("linked")
        with self.assertRaisesRegex(ValueError, "Symlink"):
            self.load()
        self.meta["public"].remove("linked")
        (self.directory / "linked-dir").symlink_to(self.directory / "evaluation", target_is_directory=True)
        self.meta["trusted"].append("linked-dir/cases.json")
        with self.assertRaisesRegex(ValueError, "Symlink"):
            self.load()
        alias = self.root / "alias"
        alias.symlink_to(self.directory, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "direct directory"):
            load_benchmark(self.root, alias)

    def test_declarations_cannot_cross_trust_classes_or_reference_a_public_evaluator(self):
        for key, value in (("reference", "task.md"), ("bindings", "config.yaml"), ("operations", "undeclared.js")):
            old = self.meta[key]
            self.meta[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.load()
            self.meta[key] = old
        self.meta["trusted"].remove("evaluation/evaluator.py")
        self.meta["public"].append("evaluation/evaluator.py")
        with self.assertRaisesRegex(ValueError, "trusted Python"):
            self.load()

    def test_missing_dependency_and_conflicting_python_modules_fail(self):
        self.meta["dependencies"] = {"family": {"path": "_shared/family", "public": [], "trusted": ["rules.py"]}}
        with self.assertRaisesRegex(ValueError, "Missing.*dependency"):
            self.load()
        self.meta["dependencies"] = {}
        self.write("evaluation/evaluator/__init__.py")
        self.meta["trusted"].append("evaluation/evaluator/__init__.py")
        with self.assertRaisesRegex(ValueError, "Conflicting module identity"):
            self.load()

    def test_declared_shared_dependency_imports_are_scoped_and_hashed(self):
        shared = self.root / "_shared/family"
        shared.mkdir(parents=True)
        (shared / "rules.py").write_text("EXPECTED = 7\n")
        self.meta["dependencies"] = {"family": {"path": "_shared/family", "public": [], "trusted": ["rules.py"]}}
        self.write(
            "evaluation/evaluator.py",
            "from ..dependencies.family.rules import EXPECTED\n"
            "def plan(submission, options):\n    return {}\n"
            "def evaluate(evidence, options):\n    return {'expected': EXPECTED}\n",
        )
        selected = self.selected()
        self.assertEqual(selected.evaluate(Path("record"), {}), {"expected": 7})
        self.assertIn("dependencies/family/rules.py", dict(selected.identity.files))
        (shared / "rules.py").write_text("EXPECTED = 8\n")
        with self.assertRaisesRegex(ValueError, "identity mismatch"):
            load_entrypoints(self.load(), selected.identity)

    def test_undeclared_sibling_import_is_refused_even_if_file_exists(self):
        self.write("evaluation/secret.py", "raise AssertionError('must not import')\n")
        self.write("evaluation/evaluator.py", "from . import secret\n")
        with self.assertRaisesRegex(ModuleNotFoundError, "Undeclared benchmark module"):
            self.selected()

    def test_stale_sources_options_and_module_cache_fail_closed(self):
        descriptor = self.load()
        identity = freeze_identity(descriptor, {"seed": 1})
        selected = load_entrypoints(descriptor, identity)
        with self.assertRaisesRegex(ValueError, "identity mismatch"):
            load_entrypoints(descriptor, replace(identity, options_json='{"seed":2}'))
        module_name = selected.evaluate.__module__
        original = sys.modules[module_name]
        sys.modules[module_name] = ModuleType(module_name)
        try:
            with self.assertRaisesRegex(ValueError, "Conflicting loaded module identity"):
                load_entrypoints(descriptor, identity)
        finally:
            sys.modules[module_name] = original
        self.write("evaluation/evaluator.py", "def evaluate(*args): return {}\n")
        with self.assertRaisesRegex(ValueError, "identity mismatch"):
            load_entrypoints(descriptor, identity)

    def test_cached_loader_uses_verified_bytes_for_delayed_imports(self):
        self.write("evaluation/helper.py", "VALUE = 1\n")
        self.meta["trusted"].append("evaluation/helper.py")
        self.write(
            "evaluation/evaluator.py",
            "def plan(submission, options): return {}\n"
            "def evaluate(evidence, options):\n    from .helper import VALUE\n    return {'value': VALUE}\n",
        )
        selected = self.selected()
        self.write("evaluation/helper.py", "VALUE = 2\n")
        self.assertEqual(selected.evaluate(Path("record"), {}), {"value": 1})

    def test_noncallables_and_bad_signatures_fail_on_selected_load(self):
        for definition in (
            "evaluate = 1",
            "def evaluate(): return {}",
            "async def evaluate(evidence, options): return {}",
        ):
            self.write("evaluation/evaluator.py", "def plan(submission, options): return {}\n" + definition + "\n")
            with self.subTest(definition=definition), self.assertRaisesRegex(ValueError, "callable|signature"):
                self.selected()

    def test_budgets_controls_and_default_reject_coercion(self):
        for value in (-1, True, 1.5, "2"):
            self.meta["budgets"]["authoring_attempts"] = value
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "budgets"):
                self.load()
        self.meta["budgets"]["authoring_attempts"] = 0
        self.meta["budgets"]["judge_calls"] = None
        with self.assertRaisesRegex(ValueError, "budgets"):
            self.load()
        self.meta["budgets"]["judge_calls"] = 0
        for reward in (True, -1, 1.1, "0.732"):
            self.meta["controls"]["reference_reward"] = reward
            with self.subTest(reward=reward), self.assertRaisesRegex(ValueError, "reward"):
                self.load()
        self.meta["controls"]["reference_reward"] = None
        self.meta["default"] = 1
        with self.assertRaisesRegex(ValueError, "boolean"):
            self.load()

    def test_an_oracle_must_require_positive_acceptance(self):
        for value in (False, 1, None):
            self.meta["controls"]["oracle_acceptance"] = value
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "oracle acceptance"):
                self.load()

    def test_reference_cannot_occupy_reserved_destinations(self):
        self.write("dependencies/family/reference.yaml", "private")
        for path in ("scenario.json", "dependencies/family/reference.yaml"):
            self.meta["reference"] = path
            with self.subTest(path=path), self.assertRaisesRegex(ValueError, "Reserved reference"):
                self.load()

    def test_missing_root_and_nonfinite_json_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "search root"):
            discover_benchmarks(self.root / "absent")
        self.meta["config"] = {"value": float("inf")}
        with self.assertRaisesRegex(ValueError, "Invalid JSON constant"):
            self.load()
        (self.directory / "scenario.json").write_text('{"number": 1e999}')
        with self.assertRaises(ValueError):
            load_benchmark(self.root, self.directory)

    def test_duplicate_json_keys_fail(self):
        (self.directory / "scenario.json").write_text('{"id":"first","id":"second"}')
        with self.assertRaisesRegex(ValueError, "Duplicate manifest key"):
            load_benchmark(self.root, self.directory)

    def test_listing_cli_imports_no_evaluators(self):
        script = (
            "import sys\nfrom sapi_config_lab.coordinate.cli import main\n"
            f"main(['benchmarks', '--root', {str(ROOT / 'benchmarks')!r}])\n"
            "assert not any(name.startswith(('verification', 'sapi_config_lab.evaluate')) for name in sys.modules)\n"
            "assert 'sapi_config_lab.coordinate.providers' not in sys.modules\n"
            "assert 'sapi_config_lab.coordinate.scenarios' not in sys.modules\n"
        )
        result = subprocess.run([sys.executable, "-c", script], cwd=ROOT, capture_output=True, text=True, check=True)
        rows = json.loads(result.stdout)
        self.assertEqual(len(rows), 2)
        self.assertEqual({row["id"] for row in rows}, {"invoice-total", "checkout-recovery"})
        self.assertEqual({row["id"] for row in rows if row["default"]}, {"invoice-total"})

    def test_explicit_selection_preserves_order_and_rejects_bad_names_without_loading(self):
        other, meta = self.package("checkout-recovery")
        meta["default"] = False
        (other / "scenario.json").write_text(json.dumps(meta))
        self.assertEqual([item.name for item in select_benchmarks(self.root)], ["invoice-total"])
        self.assertEqual(
            [item.name for item in select_benchmarks(self.root, ("checkout-recovery", "invoice-total"))],
            ["checkout-recovery", "invoice-total"],
        )
        for names in ((), ("unknown",), ("invoice-total", "invoice-total")):
            with self.subTest(names=names), self.assertRaisesRegex(ValueError, "Unknown, duplicate, or empty"):
                select_benchmarks(self.root, names)
        self.assertFalse(list(self.root.glob("*/evaluation/imported")))

    def test_identity_accepts_frozen_config_and_rejects_non_json_options(self):
        descriptor = self.load()
        identity = freeze_identity(descriptor, {"config": descriptor.config, "seed": 1})
        self.assertEqual(json.loads(identity.options_json)["config"], self.meta["config"])
        self.assertEqual(load_entrypoints(descriptor, identity).identity, identity)
        for options in ({"path": Path("not-json")}, {"nested": {1: "not-a-string-key"}}):
            with self.subTest(options=options), self.assertRaises(TypeError):
                freeze_identity(descriptor, options)

    def test_identity_contains_exact_declared_closure_not_unlisted_files(self):
        descriptor = self.load()
        first = freeze_identity(descriptor, {})
        self.write("private-unlisted", "never staged")
        self.assertEqual(first, freeze_identity(descriptor, {}))
        self.assertEqual(set(dict(first.files)), {"scenario.json", *(file.destination for file in descriptor.files)})
        self.assertEqual(dict(first.files)["config.yaml"], hashlib.sha256(b"Private reference").hexdigest())
