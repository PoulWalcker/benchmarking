"""Assemble disposable Harbor packages from a single set of owned sources."""

import hashlib
import json
from pathlib import Path
import shutil

from sapi_config_lab.core.evidence import sha256, write_json
from sapi_config_lab.paths import CATALOG, workspace_root

from sapi_config_lab.core.scenarios import BASELINE_SCENARIOS, select_scenarios

SCENARIOS = BASELINE_SCENARIOS


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
    cases = json.loads((root / "verification/cases.json").read_text())
    descriptions = json.loads((root / "generation/tasks.json").read_text())
    descriptions.update(json.loads((root / "generation/lifecycle-task.json").read_text()))
    cases.update(json.loads((root / "generation/lifecycle-cases.json").read_text()))
    hashes = {}
    for scenario, filename in selected.items():
        task = destination / scenario
        (task / "environment").mkdir(parents=True)
        (task / "tests").mkdir()
        if mode == "generation":
            instruction = (
                "TASK\n"
                + descriptions[scenario]
                + "\n\nFORMAT\n"
                + (root / "generation/FORMAT.md").read_text()
                + (
                    "\n\nTASK-SPECIFIC REFINEMENT EXTENSION\n" + (root / "generation/REFINEMENT.md").read_text()
                    if scenario == "revise-answer"
                    else ""
                )
                + (
                    "\n\nTASK-SPECIFIC LIFECYCLE EXTENSION\n" + (root / "generation/LIFECYCLE.md").read_text()
                    if scenario == "daily-digest"
                    else ""
                )
                + "\n\nPROFILE\n"
                + (root / "docs/PROFILE.md").read_text()
                + "\n\nOPERATION CATALOG\n"
                + CATALOG.read_text()
            )
            dockerfile = (
                f"FROM {image}\nUSER root\nWORKDIR /app\n"
                "RUN rm -rf /app/lab/configs /app/scenario /app/submission "
                "&& mkdir -p /app/submission\n"
            )
        else:
            instruction = (root / "harbor/tasks" / scenario / "instruction.md").read_text()
            (task / "solution").mkdir()
            shutil.copyfile(templates / "solve.sh", task / "solution/solve.sh")
            source = Path(submissions[scenario]["path"]) if mode == "replay" else root / "configs" / filename
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
            write_json(case_path, {scenario: cases[scenario]})
    return hashes
