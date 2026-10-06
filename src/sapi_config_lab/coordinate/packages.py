"""Assemble disposable Harbor packages from a single set of owned sources."""

import hashlib
import json
from pathlib import Path
import shutil

from sapi_config_lab.evidence import sha256, write_json
from sapi_config_lab.paths import CATALOG, workspace_root

from sapi_config_lab.coordinate.scenarios import select_scenarios


# What the lab image copies from src/ and the verifier re-hashes in the container.
RUNTIME_SUFFIXES = (".py", ".js", ".yaml", ".json", ".md")


def runtime_sources(root: Path) -> dict:
    """The runtime a package's verifier expects to have executed its plan."""
    src = root / "src"
    return {
        "suffixes": list(RUNTIME_SUFFIXES),
        "files": {
            str(path.relative_to(src)): sha256(path)
            for path in sorted(src.rglob("*"))
            if path.is_file() and "__pycache__" not in path.parts and path.suffix in RUNTIME_SUFFIXES
        },
    }


def stage_tasks(
    destination: Path,
    *,
    mode="oracle",
    image="sapi-config-lab-n8n:2.41.5",
    submissions=None,
    scenarios: tuple[str, ...] | None = None,
) -> dict:
    """Create immutable input packages for one run; never modify source fixtures.

    Oracle/live packages contain reference YAML and solve.sh. Generation packages
    contain only prompts and hidden verifier inputs; no reference or solution.
    Copies here are Harbor's distribution format, not independently maintained code.
    """
    selected = select_scenarios(scenarios)
    if mode not in {"oracle", "generation", "replay"}:
        raise ValueError("Task mode must be oracle, generation or replay")
    if mode == "replay":
        if not isinstance(submissions, dict) or set(submissions) != set(selected):
            raise ValueError("Replay requires exactly one submission for every scenario")
        for item in submissions.values():
            if (
                set(item) not in ({"path", "sha256"}, {"path", "sha256", "cases_path", "cases_sha256"})
                or sha256(item["path"]) != item["sha256"]
            ):
                raise ValueError("Replay submission hash mismatch")
            if "cases_path" in item and sha256(item["cases_path"]) != item["cases_sha256"]:
                raise ValueError("Replay fixture hash mismatch")
    elif submissions is not None:
        raise ValueError("Submissions require replay mode")
    root = workspace_root()
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=False)
    templates = root / "harbor/templates"
    hashes = {}
    runtime = runtime_sources(root)
    for scenario, definition in selected.items():
        task = destination / scenario
        (task / "environment").mkdir(parents=True)
        (task / "tests").mkdir()
        if mode == "generation":
            extension = definition.prompt_extension
            instruction = (
                "TASK\n"
                + definition.task()
                + "\n\nFORMAT\n"
                + (root / "generation/FORMAT.md").read_text()
                + (
                    f"\n\n{extension}\n" + (definition.directory / "prompt-extension.md").read_text()
                    if extension
                    else ""
                )
                + "\n\nPROFILE\n"
                + (root / "docs/PROFILE.md").read_text()
                + "\n\nOPERATION CATALOG\n"
                + CATALOG.read_text()
            )
            dockerfile = (
                f"FROM {image}\nUSER root\nWORKDIR /app\n"
                "RUN rm -rf /app/lab/benchmarks /app/scenario /app/submission "
                "&& mkdir -p /app/submission\n"
            )
        else:
            instruction = (definition.directory / "instruction.md").read_text()
            (task / "solution").mkdir()
            shutil.copyfile(templates / "solve.sh", task / "solution/solve.sh")
            source = Path(submissions[scenario]["path"]) if mode == "replay" else definition.config
            shutil.copyfile(source, task / "environment/base.yaml")
            if mode == "replay" and sha256(task / "environment/base.yaml") != submissions[scenario]["sha256"]:
                raise ValueError("Staged replay submission hash mismatch")
            dockerfile = (
                f"FROM {image}\nUSER root\nWORKDIR /app\n"
                "COPY base.yaml /app/scenario/base.yaml\nRUN mkdir -p /app/submission\n"
            )
        (task / "instruction.md").write_text(instruction)
        hashes[scenario] = hashlib.sha256(instruction.encode()).hexdigest()
        (task / "environment/Dockerfile").write_text(dockerfile)
        (task / "task.toml").write_text(
            (templates / "task.toml").read_text().replace('name = "invoice-total"', f'name = "{scenario}"')
        )
        test_script = (templates / "test.sh").read_text().replace("@SCENARIO@", scenario)
        if mode == "replay":
            test_script = test_script.replace(
                "set -uo pipefail",
                "set -uo pipefail\nexport SAPI_EXPECTED_SUBMISSION_SHA256=" + submissions[scenario]["sha256"],
            )
        (task / "tests/test.sh").write_text(test_script)
        write_json(task / "tests/runtime-sources.json", runtime)
        for verifier_source in (root / "verification").glob("*.py"):
            shutil.copyfile(verifier_source, task / "tests" / verifier_source.name)
        case_path = task / "tests/cases.json"
        if mode == "replay" and "cases_path" in submissions[scenario]:
            private_cases = Path(submissions[scenario]["cases_path"])
            if set(json.loads(private_cases.read_text())) != {scenario}:
                raise ValueError("Replay fixture scenario mismatch")
            shutil.copyfile(private_cases, case_path)
            if sha256(case_path) != submissions[scenario]["cases_sha256"]:
                raise ValueError("Staged replay fixture hash mismatch")
        else:
            write_json(case_path, {scenario: definition.cases()})
    return hashes
