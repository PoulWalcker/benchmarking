"""Assemble disposable Harbor task packages from the scenario registry and shared templates."""

from contextlib import suppress
import hashlib
from pathlib import Path
import shutil
from string import Template
from typing import Any

import yaml

from sapi_config_lab.coordinate.hosted_worker import HTTP_TIMEOUT_SECONDS
from sapi_config_lab.coordinate.providers import ENVIRONMENTS, EVALUATORS, host_only_modules
from sapi_config_lab.coordinate.scenarios import SCENARIOS, Scenario, select_scenarios
from sapi_config_lab.evidence import sha256, write_json
from sapi_config_lab.execute.host import LAB_IMAGE
from sapi_config_lab.execute.n8n import execution_ceiling
from sapi_config_lab.paths import workspace_root
from sapi_config_lab.profile import Invalid, read

# What the lab image copies from src/ and the verifier re-hashes in the container.
RUNTIME_SUFFIXES = (".py", ".js", ".yaml", ".json", ".md")

HOSTED_TEST = "#!/bin/bash\nset -euo pipefail\npython3 -m sapi_config_lab.coordinate.hosted_worker {action}\n"

# task.toml keeps Harbor's shared verifier environment; that is safe only for agents that place
# the submission file and run no command in the task container.
AUTHOR_AGENT = "sapi_config_lab.author.agent:WrapperYamlAgent"
UPLOAD_ONLY_AGENTS = frozenset({"oracle", "nop", AUTHOR_AGENT})

# Generation catalog arms: the default full catalog, or the experiment's scenario subset.
CATALOG_VARIANTS = ("full", "scenario")

# What a staged verifier may run, mirrored from verification/verify.py (tests/test_packaging.py compares the two):
# one wrong-result artifact and three invalid definitions beside the cases, and this deadline on a live case.
VERIFIER_PROBES = 4
LIVE_DEADLINE_SECONDS = 600
# generation/FORMAT.md requires this deadline of every authored definition.
AUTHORED_DEADLINE_SECONDS = 120
# Interpreter start, planning and evaluation around the executions.
VERIFIER_OVERHEAD_SECONDS = 120
# Container start, uploads and teardown around one trial; Harbor start and collection around one job.
TRIAL_OVERHEAD_SECONDS = 120
JOB_OVERHEAD_SECONDS = 300


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


def task_toml(root: Path, scenario: Scenario) -> str:
    """Harbor settings: only a checked name and bounded integers reach the TOML."""
    return Template((root / "harbor/templates/task.toml").read_text()).substitute(name=scenario.name, **scenario.harbor)


def declared_deadline(config: Any) -> int | None:
    """A definition's deadline when the profile would accept it."""
    execution = config.get("execution") if isinstance(config, dict) else None
    deadline = execution.get("deadline_seconds") if isinstance(execution, dict) else None
    return deadline if isinstance(deadline, int) and not isinstance(deadline, bool) and deadline > 0 else None


def verifier_seconds(config: Any, cases: dict) -> int:
    """The longest a fixture verifier can run: each planned execution, in sequence, to its ceiling."""
    deadline = declared_deadline(config)
    live = execution_ceiling(LIVE_DEADLINE_SECONDS, bound=False)  # a live grant runs one case at this deadline
    if deadline is None:
        return live + VERIFIER_OVERHEAD_SECONDS  # the compiler rejects this deadline before anything reaches n8n
    if "lifecycle" in config:
        # Each case is Callback-tested then Cron-ticked, the mutation only Callback-tested; each run stops at its deadline.
        return (2 * len(cases["positive"]) + 1) * execution_ceiling(deadline, bound=True) + VERIFIER_OVERHEAD_SECONDS
    planned = len(cases["positive"]) + len(cases["negative"]) + VERIFIER_PROBES
    return max(planned * execution_ceiling(deadline, bound=False), live) + VERIFIER_OVERHEAD_SECONDS


def hosted_verifier_seconds(wall_clock_seconds: int, evaluation_seconds: int, *, admit: bool) -> int:
    """Admission only compiles; a run waits on /begin, the deadline-bound workflow and /finish."""
    return (
        0 if admit else 2 * HTTP_TIMEOUT_SECONDS + wall_clock_seconds + evaluation_seconds
    ) + VERIFIER_OVERHEAD_SECONDS


def runtime_grant_seconds(scenario: Scenario) -> int:
    """Allow Harbor setup and environment startup before the actual bounded model-execution window."""
    execution = ENVIRONMENTS[scenario.environment].limit_seconds(scenario) if scenario.hosted else LIVE_DEADLINE_SECONDS
    setup = scenario.harbor["build_timeout_sec"] + scenario.harbor["agent_timeout_sec"]
    return (
        setup
        + TRIAL_OVERHEAD_SECONDS
        + JOB_OVERHEAD_SECONDS
        + execution
        + (HTTP_TIMEOUT_SECONDS if scenario.hosted else 0)
    )


def planned_config(scenario: Scenario, mode: str, submissions: dict | None) -> Any:
    """The definition a fixture verifier plans: reference, replayed, or authored at the format's deadline."""
    if mode == "replay":
        assert submissions is not None
        with suppress(Invalid):  # the verifier rejects unreadable YAML before anything executes
            return read(submissions[scenario.name]["path"])
        return None
    config = read(scenario.config)
    if mode == "generation":
        config["execution"]["deadline_seconds"] = AUTHORED_DEADLINE_SECONDS
    return config


def verifier_bounds(
    scenarios: tuple[str, ...] | None,
    mode: str = "oracle",
    submissions: dict | None = None,
    cases: dict[str, dict] | None = None,
) -> dict[str, int]:
    """Seconds each staged verifier may need for the definition it will plan."""
    bounds = {}
    for name, scenario in select_scenarios(scenarios).items():
        if scenario.hosted:
            limit = ENVIRONMENTS[scenario.environment].limit_seconds(scenario)
            bounds[name] = hosted_verifier_seconds(
                limit, EVALUATORS[scenario.evaluator].timeout_seconds, admit=mode == "generation"
            )
        else:
            chosen = (cases or {}).get(name)
            config = planned_config(scenario, mode, submissions)
            bounds[name] = verifier_seconds(config, scenario.cases() if chosen is None else chosen)
    return bounds


def job_seconds(bounds: dict[str, int], attempts: int = 1) -> int:
    """Outer limit of one serial `harbor run`: each trial's build and agent limits plus its verifier estimate."""
    trials = sum(
        SCENARIOS[name].harbor["build_timeout_sec"]
        + SCENARIOS[name].harbor["agent_timeout_sec"]
        + bound
        + TRIAL_OVERHEAD_SECONDS
        for name, bound in bounds.items()
    )
    return attempts * trials + JOB_OVERHEAD_SECONDS


def scenario_catalog(scenario: Scenario) -> str:
    """Experiment variant: the catalog's bytes without operations the scenario's reference never uses."""
    used = {step["uses"] for step in read(scenario.config)["workflow"]["steps"]}
    kept, section, keep = [], "", True
    for line in scenario.bindings.read_text().splitlines(keepends=True):
        if line[:1].isalpha():
            section, keep = line.split(":", 1)[0], True
        elif section == "operations" and line.startswith("  ") and line[2:3].isalpha():
            keep = line.strip().removesuffix(":") in used
        if keep:
            kept.append(line)
    text = "".join(kept)
    full = yaml.safe_load(scenario.bindings.read_text())
    if yaml.safe_load(text) != {**full, "operations": {k: v for k, v in full["operations"].items() if k in used}}:
        raise ValueError(f"{scenario.name}: the catalog cannot be cut to its operations without changing them")
    return text


def generation_prompt(root: Path, scenario: Scenario, catalog: str = "full") -> str:
    """The exact text a model is asked to answer with YAML; `catalog` selects the operation-catalog experiment arm."""
    if catalog not in CATALOG_VARIANTS or (catalog != "full" and scenario.hosted):
        raise ValueError("A reduced catalog applies to fixture scenarios only: " + scenario.name)
    if not scenario.hosted:
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
            + (scenario.bindings.read_text() if catalog == "full" else scenario_catalog(scenario))
        )
    provider = ENVIRONMENTS[scenario.environment]
    output = "Your workflow output must contain a final_answer string."
    if artifact := scenario.artifact:
        output += f" Also output {artifact.field} as a string; it is submitted verbatim as {artifact.name}."
    return (
        "TASK\n"
        + provider.task(scenario)
        + f"\nBuild a workflow with id {scenario.workflow_id}. Use only the catalog below. "
        "Choose your own graph. "
        f"Runtime is {provider.limit_seconds(scenario)} seconds with at most "
        f"{scenario.runtime_model_calls} LLM operations. "
        "Tool results are JSON strings. "
        + output
        + " "
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
    catalog: str = "full",
) -> dict:
    """Create immutable input packages for one run; never modify source fixtures."""
    selected = select_scenarios(scenarios)
    if catalog != "full" and mode != "generation":
        raise ValueError("A catalog variant changes generation prompts only")
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
    for name, required in verifier_bounds(tuple(selected), mode, submissions, cases).items():
        if required > selected[name].harbor["verifier_timeout_sec"]:
            raise ValueError(
                f"{name}: its verifier may run {required}s, more than harbor.verifier_timeout_sec "
                f"{selected[name].harbor['verifier_timeout_sec']}s"
            )
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
        scrub = ""
        if hosted:
            scrub = " " + " ".join(f"/app/lab/src/{relative}" for relative in host_only_modules())
        if mode == "generation":
            instruction = generation_prompt(root, definition, catalog)
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
        (task / "task.toml").write_text(task_toml(root, definition))
        shutil.copyfile(definition.bindings, task / "tests/bindings.yaml")
        if hosted:
            limits = {
                "scenario": scenario,
                "runtime_model_calls": definition.runtime_model_calls,
                "deadline_seconds": ENVIRONMENTS[definition.environment].limit_seconds(definition),
            }
            write_json(task / "tests/admission.json", limits)
            if mode != "generation":
                write_json(task / "tests/environment.json", {"scenario": scenario, "seed": 0})
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
        # The deadline the verifier timeout was sized for; the verifier refuses a definition that exceeds it.
        deadline = declared_deadline(planned_config(definition, mode, submissions))
        write_json(task / "tests/budget.json", {"deadline_seconds": deadline})
        for verifier_source in (root / "verification").glob("*.py"):
            shutil.copyfile(verifier_source, task / "tests" / verifier_source.name)
        if (definition.directory / "evaluation").is_dir():
            shutil.copytree(definition.directory / "evaluation", task / "tests/evaluation" / scenario)
        chosen = cases.get(scenario) if cases is not None else None
        write_json(task / "tests/cases.json", {scenario: definition.cases() if chosen is None else chosen})
    return hashes
