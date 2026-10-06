"""Stage boundaries: compile, execute, evaluate, author and coordinate.

AGENTS.md states the rules; this test is their enforcement. Every Python module in
the package and the independent verifier is assigned to exactly one stage, and an
import is allowed only along the edges in ALLOWED. KNOWN_VIOLATIONS lists the
edges that still break a rule; it may only shrink, so fixing one means deleting
its line here.
"""

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
    "coordinate": {SHARED, "compile", "execute", "evaluate", "author", "verification", "coordinate"},
}

# Module (dotted, relative to the package or "verification.") -> stage. The
# longest matching prefix wins; anything unmatched in the package coordinates.
STAGES = {
    "contracts": SHARED,
    "profile": SHARED,
    "evidence": SHARED,
    "paths": SHARED,
    "core.contracts": SHARED,
    "core.profile": SHARED,
    "core.evidence": SHARED,
    "runtime.n8n.compiler": "compile",
    "runtime.n8n.refinement": "compile",
    "runtime.n8n.execution": "execute",
    "runtime.agency": "execute",
    "runtime.ui_n8n": "execute",
    "core.host": "execute",
    "runtime.task_evaluation": "evaluate",
    "interfaces.review_export": "evaluate",
    "interfaces.benchmark_series": "evaluate",
    "runtime.rebuilder": "author",
    "interfaces.generation.agent": "author",
    "compile": "compile",
    "execute": "execute",
    "evaluate": "evaluate",
    "author": "author",
    "coordinate": "coordinate",
}

KNOWN_VIOLATIONS = {
    ("interfaces.generation.agent", "interfaces.generation.common"),
    ("runtime.n8n.execution", "runtime.execution"),
    ("runtime.rebuilder", "runtime.agency"),
    ("runtime.rebuilder", "runtime.lifecycle"),
    ("runtime.task_evaluation", "runtime.autowfbench"),
    ("verification.lifecycle_submission", "core.profile"),
    ("verification.lifecycle_submission", "core.provenance"),
    ("verification.lifecycle_submission", "paths"),
    ("verification.lifecycle_submission", "runtime.lifecycle"),
    ("verification.verify", "core.scenarios"),
    ("verification.verify", "runtime.execution"),
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

    def test_imports_follow_stage_rules(self):
        found = self.violations()
        self.assertEqual(found - KNOWN_VIOLATIONS, set(), "New cross-stage import; see AGENTS.md")
        self.assertEqual(KNOWN_VIOLATIONS - found, set(), "Fixed violation still listed; delete it here")

    def test_nothing_outside_coordinate_reaches_for_modules_dynamically(self):
        # importlib hides an edge from the check above, so only coordination may use it.
        for module, path, _ in modules():
            if stage_of(module) == "coordinate":
                continue
            tree = ast.parse(path.read_text())
            names = {alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names}
            names |= {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
            self.assertFalse(
                any(name and name.startswith("importlib") and name != "importlib.metadata" for name in names), module
            )

    def test_every_module_has_a_stage(self):
        stages = {stage_of(module) for module, _, _ in modules()}
        self.assertLessEqual(stages, set(ALLOWED))
