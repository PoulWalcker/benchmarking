"""Enforce benchmark-neutral stages and the independent verifier without legacy exceptions."""

import ast
from pathlib import Path
import unittest

from sapi_config_lab.paths import workspace_root

ROOT = workspace_root()
PACKAGE = "sapi_config_lab"

SHARED = "shared"
ALLOWED = {
    SHARED: {SHARED},
    "compile": {SHARED, "compile"},
    "execute": {SHARED, "execute"},
    "evaluate": {SHARED, "evaluate"},
    "author": {SHARED, "author"},
    "verification": {"verification"},
    "harbor_integration": {SHARED, "execute", "harbor_integration"},
    "coordinate": {
        SHARED,
        "compile",
        "execute",
        "evaluate",
        "author",
        "verification",
        "coordinate",
        "harbor_integration",
    },
}

# Native trusted scripts are explicit composition roots, not lower-stage modules.
NATIVE_COORDINATORS = {
    "native_tasks.invoice-total.tests.main": {"payload.evaluation.evaluator"},
    "native_tasks.checkout-recovery.tests.main": {
        "payload.environment.hooks",
        "payload.evaluation.evaluator",
        "payload.evaluation.scoring",
    },
    "native_tasks.checkout-recovery.tests.fake_bridge": set(),
    "native_tasks.checkout-recovery.tests.calibration_transport": set(),
}
ALLOWED["native_coordinate"] = ALLOWED["coordinate"] | {"benchmark_domain", "native_coordinate"}


# Module (dotted, relative to the package or "verification.") -> stage. The
# longest matching prefix wins; anything unmatched in the package coordinates.
STAGES = {
    "contracts": SHARED,
    "benchmark": SHARED,
    "benchmark_loading": SHARED,
    "profile": SHARED,
    "evidence": SHARED,
    "paths": SHARED,
    "net": SHARED,
    "pinned_source": SHARED,
    "wrapper_audit": SHARED,
    "compile": "compile",
    "execute": "execute",
    "evaluate": "evaluate",
    "author": "author",
    "coordinate": "coordinate",
    "harbor_integration": "harbor_integration",
}


def stage_of(module: str) -> str:
    if module in NATIVE_COORDINATORS:
        return "native_coordinate"
    if module.startswith("native_tasks."):
        return "unclassified_native"
    if module == "payload" or module.startswith("payload."):
        return "benchmark_domain"
    if module.startswith("verification"):
        return "verification"
    match = max(
        (prefix for prefix in STAGES if module == prefix or module.startswith(prefix + ".")), key=len, default=None
    )
    return STAGES[match] if match else "coordinate"


def modules():
    for path in sorted((ROOT / "tasks").rglob("*.py")):
        relative = path.relative_to(ROOT / "tasks").with_suffix("")
        yield ".".join(("native_tasks", *relative.parts)), path, False
    for path in sorted((ROOT / "src" / PACKAGE).rglob("*.py")):
        relative = path.relative_to(ROOT / "src" / PACKAGE).with_suffix("")
        yield ".".join(part for part in relative.parts if part != "__init__"), path, False
    for path in sorted((ROOT / "verification").rglob("*.py")):
        relative = path.relative_to(ROOT / "verification").with_suffix("")
        yield ".".join(("verification", *(part for part in relative.parts if part != "__init__"))), path, True


def imported(path: Path, module: str, verifier: bool) -> set[str]:
    """Resolve stage edges from static and literal dynamic imports, including initializers."""
    qualified = module if verifier or module.startswith("native_tasks.") else PACKAGE + ("." + module if module else "")
    names = set()
    for name in ownership_imports(path.read_text(), qualified, package=path.name == "__init__.py"):
        if name == PACKAGE or name.startswith(PACKAGE + "."):
            names.add(name.removeprefix(PACKAGE).lstrip("."))
        elif name == "verification" or name.startswith(("verification.", "native_tasks.", "payload.")):
            names.add(name)
        elif verifier and (ROOT / "verification" / (name.split(".")[0] + ".py")).exists():
            names.add("verification." + name.split(".")[0])
    return {name for name in names if name}


class StageBoundaryTests(unittest.TestCase):
    def violations(self) -> set[tuple[str, str]]:
        found = set()
        for module, path, verifier in modules():
            source = stage_of(module)
            for name in imported(path, module, verifier):
                if stage_of(name) not in ALLOWED.get(source, set()):
                    found.add((module, name))
        return found

    def test_imports_follow_stage_rules(self):
        self.assertEqual(self.violations(), set(), "Cross-stage import; see docs/ARCHITECTURE.md")

    def test_dynamic_imports_have_explicit_boundaries(self):
        # The selected benchmark loader is the only neutral dynamic-import seam.
        for module, path, _ in modules():
            if stage_of(module) == "coordinate" or module == "benchmark_loading":
                continue
            tree = ast.parse(path.read_text())
            names = {alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names}
            names |= {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
            self.assertFalse(
                any(
                    name and name.startswith("importlib") and name not in {"importlib.metadata", "importlib.resources"}
                    for name in names
                ),
                module,
            )

    def test_every_module_has_a_stage(self):
        # A module outside the stage directories would silently be "coordinate".
        stages = ("compile", "execute", "evaluate", "author", "coordinate", "harbor_integration")
        loose = {module for module, _, verifier in modules() if not verifier and "." not in module}
        shared = {name for name, stage in STAGES.items() if stage == SHARED}
        self.assertEqual(loose - shared - set(stages) - {"__main__", ""}, set())
        native = {module for module, _, _ in modules() if module.startswith("native_tasks.")}
        self.assertEqual(native, set(NATIVE_COORDINATORS), "Every native script needs explicit ownership")
        for stage in stages:
            self.assertTrue((ROOT / "src" / PACKAGE / stage).is_dir(), stage)

    def test_experiments_reach_harbor_bridges_and_staging_only_through_a_run(self):
        owned = {"job_args", "run_job", "staging_dir", "pin_base_image", "start_bridge", "stop_bridge"}
        for module, path, _ in modules():
            if stage_of(module) != "coordinate" or module == "coordinate.runs":
                continue
            for node in ast.walk(ast.parse(path.read_text())):
                if isinstance(node, ast.ImportFrom):
                    self.assertFalse(owned & {alias.name for alias in node.names}, module)


def ownership_imports(source: str, module: str, *, package: bool = False) -> set[str]:
    """Resolve static and literal dynamic imports without importing benchmark code."""
    tree = ast.parse(source)
    parent_parts = module.split(".") if package else module.split(".")[:-1]
    names = set()
    loaders = {"__import__"}
    importlibs = {"importlib"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
            importlibs.update(alias.asname or alias.name for alias in node.names if alias.name == "importlib")
        elif isinstance(node, ast.ImportFrom):
            parent = node.module or ""
            if node.level:
                parent = ".".join([*parent_parts[: len(parent_parts) - node.level + 1], *parent.split(".")]).rstrip(".")
            names.add(parent)
            names.update(f"{parent}.{alias.name}" for alias in node.names)
            if parent == "importlib":
                loaders.update(alias.asname or alias.name for alias in node.names if alias.name == "import_module")
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not node.args:
            continue
        function = node.func
        dynamic = isinstance(function, ast.Name) and function.id in loaders
        dynamic |= (
            isinstance(function, ast.Attribute)
            and isinstance(function.value, ast.Name)
            and function.value.id in importlibs
            and function.attr == "import_module"
        )
        argument = node.args[0]
        if dynamic and isinstance(argument, ast.Constant) and isinstance(argument.value, str):
            names.add(argument.value)
    return names


def ownership_edges(module: str, source: str, *, package: bool = False) -> set[tuple[str, str]]:
    """Forbidden benchmark and infrastructure imports, regardless of stage or import syntax."""
    found = set()
    for name in ownership_imports(source, module, package=package):
        if (name == "payload" or name.startswith("payload.")) and not any(
            name == allowed or name.startswith(allowed + ".") for allowed in NATIVE_COORDINATORS.get(module, set())
        ):
            found.add((module, "benchmark_domain"))
        if any(
            name == prefix or name.startswith(prefix + ".")
            for prefix in ("benchmarks", PACKAGE + ".resources.benchmarks")
        ):
            found.add((module, "benchmarks"))
        if (
            (name == "harbor" or name.startswith("harbor."))
            and not module.startswith("sapi_config_lab.harbor_integration.")
            and module != "sapi_config_lab.harbor_integration"
        ):
            found.add((module, "harbor"))
    return found


class OwnershipTests(unittest.TestCase):
    def test_core_and_verifier_do_not_import_benchmarks_and_only_integration_imports_harbor(self):
        found = set()
        for module, path, verifier in modules():
            qualified = (
                module if verifier or module.startswith("native_tasks.") else PACKAGE + ("." + module if module else "")
            )
            found.update(ownership_edges(qualified, path.read_text(), package=path.name == "__init__.py"))
        self.assertEqual(found, set(), f"Ownership violations: {sorted(found)}")

    def test_native_composition_permissions_are_explicit_and_cannot_flow_back_into_core(self):
        native = "native_tasks.checkout-recovery.tests.main"
        self.assertEqual(stage_of(native), "native_coordinate")
        self.assertFalse(ownership_edges(native, "from payload.environment.hooks import prepare"))
        for module in (native, "native_tasks.checkout-recovery.tests.fake_bridge", "sapi_config_lab.compile.n8n"):
            self.assertTrue(ownership_edges(module, "from payload.undeclared import private"))
            self.assertTrue(ownership_edges(module, "from harbor.models.task import Task"))
        self.assertTrue(ownership_edges("sapi_config_lab.compile.n8n", "from payload.environment.hooks import prepare"))
        self.assertNotIn("native_coordinate", ALLOWED["compile"])
        self.assertNotIn("native_coordinate", ALLOWED["coordinate"])
        self.assertEqual(stage_of("native_tasks.checkout-recovery.tests.unregistered"), "unclassified_native")

    def test_static_relative_and_literal_dynamic_imports_obey_boundaries(self):
        examples = (
            "import benchmarks.new_task.evaluation",
            "from benchmarks import new_task",
            "from importlib import import_module as load\nload('benchmarks.new_task')",
            "import importlib as loader\nloader.import_module('benchmarks.new_task')",
            "__import__('benchmarks.new_task')",
            "from sapi_config_lab.resources.benchmarks import new_task",
            "__import__('sapi_config_lab.resources.benchmarks.new_task.evaluation')",
            "from harbor.models.task.config import TaskConfig",
            "from importlib import import_module as load\nload('harbor.models.task.config')",
        )
        for source in examples:
            with self.subTest(source=source):
                self.assertTrue(ownership_edges("sapi_config_lab.coordinate.loader", source))
                self.assertTrue(ownership_edges("sapi_config_lab", source, package=True))
        self.assertIn(
            "sapi_config_lab.execute.agency",
            ownership_imports("from ..execute import agency", "sapi_config_lab.compile", package=True),
        )
        self.assertFalse(
            ownership_edges("sapi_config_lab.harbor_integration.tasks", "from harbor.models.task import Task")
        )
        self.assertFalse(ownership_edges("sapi_config_lab.compile.n8n", "from sapi_config_lab import contracts"))
