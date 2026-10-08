"""Stage boundaries: compile, execute, evaluate, author and coordinate.

AGENTS.md states the rules; this test is their enforcement. Every Python module in
the package and the independent verifier is assigned to exactly one stage, and an
import is allowed only along the edges in ALLOWED. KNOWN_VIOLATIONS lists the
edges that still break a rule; it may only shrink, so fixing one means deleting
its line here.
"""

import ast
import json
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
    "harbor_integration": {SHARED, "harbor_integration"},
    "coordinate": {SHARED, "compile", "execute", "evaluate", "author", "verification", "coordinate"},
}

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

KNOWN_VIOLATIONS: set[tuple[str, str]] = set()

# Modules named after a benchmark provider; stage and shared code reaches them only through coordination.
PROVIDER_MODULES = {"execute.autowfbench", "evaluate.autowfbench", "evaluate.judge_calibration"}

# Core modules that name a provider only as data, never as code; this may only shrink.
PROVIDER_NAMED_AS_DATA = {
    "coordinate.cli": "help text gives a provenance source name as an example",
    "coordinate.provenance": "the source manifest pins provenance/autowfbench-source.json",
}


def stage_of(module: str) -> str:
    if module.startswith("verification"):
        return "verification"
    match = max(
        (prefix for prefix in STAGES if module == prefix or module.startswith(prefix + ".")), key=len, default=None
    )
    return STAGES[match] if match else "coordinate"


def modules():
    for path in sorted((ROOT / "src" / PACKAGE).rglob("*.py")):
        relative = path.relative_to(ROOT / "src" / PACKAGE).with_suffix("")
        yield ".".join(part for part in relative.parts if part != "__init__"), path, False
    for path in sorted((ROOT / "verification").glob("*.py")):
        yield "verification." + path.stem, path, True


def is_module(name: str) -> bool:
    parts = name.removeprefix(PACKAGE).lstrip(".").split(".")
    base = ROOT / "src" / PACKAGE if name.startswith(PACKAGE) else ROOT
    target = base.joinpath(*parts)
    return target.with_suffix(".py").exists() or (target / "__init__.py").exists()


def imported(path: Path, module: str, verifier: bool) -> set[str]:
    """Package-relative names this module imports, including function-level imports."""
    names = set()
    package = module.split(".")[:-1] if path.name != "__init__.py" else module.split(".")
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            candidates = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = package[: len(package) - node.level + 1]
                parent = ".".join([*base, node.module] if node.module else base)
            else:
                parent = node.module or ""
            # `from package.core import profile` imports the submodule, not the package.
            candidates = [
                f"{parent}.{alias.name}" if is_module(f"{parent}.{alias.name}") else parent for alias in node.names
            ]
        else:
            continue
        for name in candidates:
            if name == PACKAGE or name.startswith(PACKAGE + "."):
                names.add(name.removeprefix(PACKAGE).lstrip("."))
            elif verifier and (ROOT / "verification" / (name.split(".")[0] + ".py")).exists():
                names.add("verification." + name.split(".")[0])
            elif name.startswith("verification"):
                names.add(name)
    return {name for name in names if name}


class StageBoundaryTests(unittest.TestCase):
    def violations(self) -> set[tuple[str, str]]:
        found = set()
        for module, path, verifier in modules():
            source = stage_of(module)
            for name in imported(path, module, verifier):
                if stage_of(name) not in ALLOWED[source]:
                    found.add((module, name))
        return found

    def test_fixture_machinery_does_not_select_business_rules_by_benchmark_name(self):
        names = {path.parent.name[3:] for path in (ROOT / "benchmarks").glob("*/scenario.json")}
        for relative in (
            "verification/verify.py",
            "verification/rubric_facts.py",
            "src/sapi_config_lab/coordinate/generate.py",
        ):
            constants = {
                node.value
                for node in ast.walk(ast.parse((ROOT / relative).read_text()))
                if isinstance(node, ast.Constant) and isinstance(node.value, str)
            }
            self.assertFalse(constants & names, relative)

    def test_imports_follow_stage_rules(self):
        found = self.violations()
        self.assertEqual(found - KNOWN_VIOLATIONS, set(), "New cross-stage import; see AGENTS.md")
        self.assertEqual(KNOWN_VIOLATIONS - found, set(), "Fixed violation still listed; delete it here")

    def test_dynamic_imports_have_explicit_boundaries(self):
        # The selected benchmark loader is the only neutral dynamic-import seam.
        for module, path, _ in modules():
            if stage_of(module) == "coordinate" or module == "benchmark_loading":
                continue
            tree = ast.parse(path.read_text())
            names = {alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names}
            names |= {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
            self.assertFalse(
                any(name and name.startswith("importlib") and name != "importlib.metadata" for name in names), module
            )

    def test_every_module_has_a_stage(self):
        # A module outside the stage directories would silently be "coordinate".
        stages = ("compile", "execute", "evaluate", "author", "coordinate", "harbor_integration")
        loose = {module for module, _, verifier in modules() if not verifier and "." not in module}
        shared = {name for name, stage in STAGES.items() if stage == SHARED}
        self.assertEqual(loose - shared - set(stages) - {"__main__", ""}, set())
        for stage in stages:
            self.assertTrue((ROOT / "src" / PACKAGE / stage).is_dir(), stage)

    def test_only_authoring_and_coordination_import_harbor(self):
        # Harbor is an optional extra; compile, execute and evaluate must work without it.
        for module, path, _ in modules():
            if module == "author.agent" or stage_of(module) in {"coordinate", "harbor_integration"}:
                continue
            for node in ast.walk(ast.parse(path.read_text())):
                if isinstance(node, ast.ImportFrom) and not node.level:
                    self.assertFalse((node.module or "").split(".")[0] == "harbor", module)
                if isinstance(node, ast.Import):
                    self.assertFalse(any(a.name.split(".")[0] == "harbor" for a in node.names), module)

    def test_only_coordination_and_providers_import_provider_modules(self):
        # A provider is named where its code lives; everything else stays provider-neutral.
        for module, path, verifier in modules():
            if module in PROVIDER_MODULES or stage_of(module) == "coordinate":
                continue
            self.assertFalse(imported(path, module, verifier) & PROVIDER_MODULES, module)

    def test_core_modules_reach_providers_only_through_the_provider_table(self):
        # A new provider is a table entry in coordinate.providers; the rest of coordination stays unaware of it.
        for module, path, verifier in modules():
            if verifier or module == "coordinate.providers" or stage_of(module) not in {"coordinate", SHARED}:
                continue
            self.assertFalse(imported(path, module, verifier) & PROVIDER_MODULES, module)
            named = "autowfbench" in path.read_text().lower()
            self.assertEqual(named, module in PROVIDER_NAMED_AS_DATA, module)

    def test_experiments_reach_harbor_bridges_and_staging_only_through_a_run(self):
        owned = {"harbor_run_args", "collect_jobs", "staging_dir", "pin_base_image", "start_bridge", "stop_bridge"}
        for module, path, _ in modules():
            if stage_of(module) != "coordinate" or module == "coordinate.runs":
                continue
            for node in ast.walk(ast.parse(path.read_text())):
                if isinstance(node, ast.ImportFrom):
                    self.assertFalse(owned & {alias.name for alias in node.names}, module)


# These mixed legacy modules own benchmark behavior today; moving them is later work.
BENCHMARK_IMPLEMENTATIONS = {
    "sapi_config_lab.coordinate.providers",
    "sapi_config_lab.execute.autowfbench",
    "sapi_config_lab.evaluate.autowfbench",
    "sapi_config_lab.evaluate.judge_calibration",
    "sapi_config_lab.evaluate.operational",
    "verification.business",
    "verification.scenario_business",
    "verification.fixture_evaluators",
    "verification.fixture_freshness",
    "verification.fixture_prose",
    "verification.roles",
    "verification.extensions",
    "verification.lifecycle",
    "verification.lifecycle_submission",
}


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
    """Edges requiring removal or an explicitly frozen legacy exception."""
    found = set()
    for name in ownership_imports(source, module, package=package):
        if name == "benchmarks" or name.startswith("benchmarks."):
            found.add((module, "benchmarks"))
        for implementation in BENCHMARK_IMPLEMENTATIONS:
            if name == implementation or name.startswith(implementation + "."):
                found.add((module, implementation))
        if (
            (name == "harbor" or name.startswith("harbor."))
            and not module.startswith("sapi_config_lab.harbor_integration.")
            and module != "sapi_config_lab.harbor_integration"
        ):
            found.add((module, "harbor"))
    return found


class MigrationOwnershipTests(unittest.TestCase):
    def test_no_additional_benchmark_or_harbor_imports(self):
        baseline = ROOT / "evidence/migration-01-baseline/legacy-imports.json"
        frozen = {tuple(edge) for edge in json.loads(baseline.read_text())["edges"]}
        found = set()
        for module, path, verifier in modules():
            qualified = module if verifier else PACKAGE + ("." + module if module else "")
            found.update(ownership_edges(qualified, path.read_text(), package=path.name == "__init__.py"))
        self.assertFalse(found - frozen, f"New ownership violations: {sorted(found - frozen)}")
        # The snapshot is immutable evidence; removed edges need not remain in source.

    def test_new_core_cannot_import_benchmarks_or_legacy_business(self):
        examples = (
            "import benchmarks.new_task.evaluation",
            "from benchmarks import new_task",
            "from sapi_config_lab.execute import autowfbench",
            "from ..execute.autowfbench import start_environment",
            "from verification import business",
            "from sapi_config_lab.coordinate.providers import ENVIRONMENTS",
            "from importlib import import_module as load\nload('benchmarks.new_task')",
            "import importlib as loader\nloader.import_module('verification.business')",
            "__import__('benchmarks.new_task')",
        )
        for source in examples:
            with self.subTest(source=source):
                self.assertTrue(ownership_edges("sapi_config_lab.core.loader", source))
        self.assertTrue(ownership_edges("sapi_config_lab.core", "from ..execute import autowfbench", package=True))

    def test_harbor_is_reached_only_through_new_integration(self):
        source = "from harbor.models.task.config import TaskConfig"
        self.assertTrue(ownership_edges("sapi_config_lab.core.runner", source))
        self.assertFalse(ownership_edges("sapi_config_lab.harbor_integration.tasks", source))
        self.assertFalse(ownership_edges("sapi_config_lab.core.runner", "from sapi_config_lab.core import contracts"))
