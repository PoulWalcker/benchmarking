"""Assemble disposable Harbor task packages from the scenario registry and shared templates."""

import hashlib
from pathlib import Path
import shutil

from sapi_config_lab.coordinate.evaluation import contract_for
from sapi_config_lab.coordinate.scenarios import Scenario, select_scenarios
from sapi_config_lab.evidence import sha256, write_json
from sapi_config_lab.execute.host import LAB_IMAGE
from sapi_config_lab.paths import CATALOG, workspace_root

# What the lab image copies from src/ and the verifier re-hashes in the container.
RUNTIME_SUFFIXES = (".py", ".js", ".yaml", ".json", ".md")

# A hosted task's evaluator runs on the host; its container must not be able to read it.
# `rm -rf` exits 0 on a missing path, so tests/test_packaging.py checks each still exists.
EVALUATOR_MODULES = (
    "sapi_config_lab/coordinate/evaluation.py",
    "sapi_config_lab/evaluate/task_evaluation.py",
    "sapi_config_lab/evaluate/judge_calibration.py",
    "sapi_config_lab/evaluate/judge-calibration.json",
    "sapi_config_lab/execute/simulator.py",
    "sapi_config_lab/pinned_source.py",
)
HOSTED_TEST = "#!/bin/bash\nset -euo pipefail\npython3 -m sapi_config_lab.coordinate.simulator_worker {action}\n"


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


def generation_prompt(root: Path, scenario: Scenario, definition: dict | None = None) -> str:
    """The exact text a model is asked to answer with YAML."""
    if definition is None:
        extension = scenario.prompt_extension
        return (
            "TASK\n"
            + scenario.task()
            + "\n\nFORMAT\n"
            + (root / "generation/FORMAT.md").read_text()
            + (f"\n\n{extension}\n" + (scenario.directory / "prompt-extension.md").read_text() if extension else "")
            + "\n\nPROFILE\n"
            + (root / "generation/PROFILE.md").read_text()
            + "\n\nOPERATION CATALOG\n"
            + CATALOG.read_text()
        )
    output = "Your workflow output must contain a final_answer string."
    if artifact := scenario.artifact:
        output += f" Also output {artifact.field} as a string; it is submitted verbatim as {artifact.name}."
    return (
        "TASK\n"
        + definition["task"]
        + "\n"
        + definition["completion"]
        + f"\nBuild a workflow with id {scenario.workflow_id}. Use only the catalog below. "
        "Choose your own graph. "
        f"Runtime is {definition['limits']['wall_clock_seconds']} seconds with at most "
        f"{scenario.runtime_model_calls} LLM operations. "
        "Tool results are JSON strings. "
        + output
        + " This is a synthetic simulator. "
        + scenario.authoring_notes()
        + "\n\n"
        + (root / "generation/FORMAT.md").read_text()
        + "\nOPERATION CATALOG\n"
        + scenario.bindings.read_text()
    )


def stage_tasks(
    destination: Path,
    *,
    mode="oracle",
    image=LAB_IMAGE,
    submissions: dict | None = None,
    scenarios: tuple[str, ...] | None = None,
    cases: dict[str, dict] | None = None,
) -> dict:
    """Create immutable input packages for one run; never modify source fixtures."""
    selected = select_scenarios(scenarios)
    if mode not in {"oracle", "generation", "replay"}:
        raise ValueError("Task mode must be oracle, generation or replay")
    if mode == "replay":
        if not isinstance(submissions, dict) or set(submissions) != set(selected):
            raise ValueError("Replay requires exactly one submission for every scenario")
        if any(sha256(item["path"]) != item["sha256"] for item in submissions.values()):
            raise ValueError("Replay submission hash mismatch")
    elif submissions is not None:
        raise ValueError("Submissions require replay mode")
    if cases is not None and not set(cases) <= {name for name, s in selected.items() if s.environment == "fixtures"}:
        raise ValueError("Fixtures given for a scenario that is not staged on fixtures")
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
        hosted = definition.hosted
        upstream = None
        scrub = ""
        if hosted:
            upstream = contract_for(definition).package["definition"]
            scrub = " " + " ".join(f"/app/lab/src/{relative}" for relative in EVALUATOR_MODULES)
        if mode == "generation":
            instruction = generation_prompt(root, definition, upstream)
            dockerfile = (
                f"FROM {image}\nUSER root\nWORKDIR /app\n"
                f"RUN rm -rf /app/lab/benchmarks /app/scenario /app/submission{scrub} "
                "&& mkdir -p /app/submission\n"
            )
        else:
            instruction = (definition.directory / "instruction.md").read_text()
            (task / "solution").mkdir()
            shutil.copyfile(templates / "solve.sh", task / "solution/solve.sh")
            replayed = submissions[scenario] if submissions is not None else None
            source = Path(replayed["path"]) if replayed is not None else definition.config
            shutil.copyfile(source, task / "environment/base.yaml")
            if replayed is not None and sha256(task / "environment/base.yaml") != replayed["sha256"]:
                raise ValueError("Staged replay submission hash mismatch")
            dockerfile = (
                f"FROM {image}\nUSER root\nWORKDIR /app\n"
                "COPY base.yaml /app/scenario/base.yaml\n"
                + (f"RUN rm -rf /app/lab/benchmarks{scrub}\n" if hosted else "")
                + "RUN mkdir -p /app/submission\n"
            )
        (task / "instruction.md").write_text(instruction)
        hashes[scenario] = hashlib.sha256(instruction.encode()).hexdigest()
        (task / "environment/Dockerfile").write_text(dockerfile)
        (task / "task.toml").write_text(
            (templates / "task.toml").read_text().replace('name = "invoice-total"', f'name = "{scenario}"')
        )
        if hosted:
            assert upstream is not None and definition.provenance is not None
            shutil.copyfile(definition.bindings, task / "tests/bindings.yaml")
            if mode == "generation":
                limits = {
                    "scenario": scenario,
                    "runtime_model_calls": definition.runtime_model_calls,
                    "deadline_seconds": upstream["limits"]["wall_clock_seconds"],
                }
                write_json(task / "tests/admission.json", limits)
            else:
                write_json(
                    task / "tests/environment.json",
                    {"scenario": scenario, "challenge": definition.provenance.challenge, "seed": 0},
                )
            (task / "tests/test.sh").write_text(HOSTED_TEST.format(action="admit" if mode == "generation" else "run"))
            continue
        test_script = (templates / "test.sh").read_text().replace("@SCENARIO@", scenario)
        if submissions is not None:
            test_script = test_script.replace(
                "set -uo pipefail",
                "set -uo pipefail\nexport SAPI_EXPECTED_SUBMISSION_SHA256=" + submissions[scenario]["sha256"],
            )
        (task / "tests/test.sh").write_text(test_script)
        write_json(task / "tests/runtime-sources.json", runtime)
        for verifier_source in (root / "verification").glob("*.py"):
            shutil.copyfile(verifier_source, task / "tests" / verifier_source.name)
        chosen = cases.get(scenario) if cases is not None else None
        write_json(task / "tests/cases.json", {scenario: definition.cases() if chosen is None else chosen})
    return hashes
