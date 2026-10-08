"""Stage explicitly selected benchmark descriptors through their declared native Harbor materials."""

from contextlib import suppress
import hashlib
import json
from pathlib import Path
from typing import Any

from sapi_config_lab.benchmark import Benchmark
from sapi_config_lab.benchmark_loading import freeze_identity, load_entrypoints
from sapi_config_lab.coordinate.benchmark_authoring import generation_prompt
from sapi_config_lab.coordinate.benchmark_packages import stage_selected
from sapi_config_lab.evidence import sha256
from sapi_config_lab.execute.n8n import execution_ceiling
from sapi_config_lab.harbor_integration.tasks import validate_config
from sapi_config_lab.profile import Invalid, read

AUTHOR_AGENT = "sapi_config_lab.harbor_integration.yaml_agent:WrapperYamlAgent"
UPLOAD_ONLY_AGENTS = frozenset({"oracle", "nop", AUTHOR_AGENT})
CATALOG_VARIANTS = ("full", "scenario")
AUTHORED_DEADLINE_SECONDS = 120
LIVE_DEADLINE_SECONDS = 600
VERIFIER_OVERHEAD_SECONDS = 120


def selected_cases(benchmark: Benchmark) -> dict | None:
    """Read an optional declared private selection record; never discover undeclared fixture files."""
    path = benchmark.config.get("cases")
    if path is None:
        return None
    files = {item.destination: item.source for item in benchmark.trusted}
    if path not in files:
        raise ValueError("Case selection must be declared trusted material")
    return json.loads(files[path].read_text())


def runtime_grant_seconds(benchmark: Benchmark) -> int:
    """Keep the bridge available through the native phases; Harbor owns their enforcement."""
    files = {item.destination: item.source for item in benchmark.files}
    native = validate_config(files[benchmark.harbor_task].read_text())
    return int(native.environment.build_timeout_sec + native.agent.timeout_sec + native.verifier.timeout_sec)


def declared_deadline(config: Any) -> int | None:
    execution = config.get("execution") if isinstance(config, dict) else None
    value = execution.get("deadline_seconds") if isinstance(execution, dict) else None
    return value if type(value) is int and value > 0 else None


def planned_config(benchmark: Benchmark, mode: str, submissions: dict | None) -> Any:
    if mode == "replay":
        assert submissions is not None
        with suppress(Invalid):
            return read(submissions[benchmark.name]["path"])
        return None
    config = read(benchmark.reference.source)
    if mode == "generation":
        config["execution"]["deadline_seconds"] = AUTHORED_DEADLINE_SECONDS
    return config


def verifier_bounds(
    benchmarks: tuple[Benchmark, ...],
    mode: str = "oracle",
    submissions: dict | None = None,
    cases: dict[str, dict] | None = None,
) -> dict[str, int]:
    """Admit each observation plan into its declared native verifier phase."""
    bounds = {}
    for benchmark in benchmarks:
        config = planned_config(benchmark, mode, submissions)
        deadline = declared_deadline(config)
        if deadline is None:
            bounds[benchmark.name] = VERIFIER_OVERHEAD_SECONDS
            continue
        options = {"mode": "stub", "deadline_seconds": deadline}
        if cases and benchmark.name in cases:
            options["cases"] = cases[benchmark.name]
        if "prepare" in benchmark.entrypoints:
            bound = execution_ceiling(benchmark.config["deadline_seconds"], bound=False)
        else:
            if mode == "replay":
                assert submissions is not None
                path = Path(submissions[benchmark.name]["path"])
            else:
                path = benchmark.reference.source
            plan = load_entrypoints(benchmark, freeze_identity(benchmark, options)).plan(path, options)
            bound = max(
                len(plan["entries"]) * execution_ceiling(deadline, bound=False),
                execution_ceiling(LIVE_DEADLINE_SECONDS, bound=False),
            )
        bounds[benchmark.name] = bound + VERIFIER_OVERHEAD_SECONDS
    return bounds


def scenario_catalog(benchmark: Benchmark) -> str:
    """Preserve the reference-operation subset experiment's exact catalog bytes."""
    files = {item.destination: item.source for item in benchmark.public}
    used = {step["uses"] for step in read(benchmark.reference.source)["workflow"]["steps"]}
    kept, section, keep = [], "", True
    for line in files[benchmark.bindings].read_text().splitlines(keepends=True):
        if line[:1].isalpha():
            section, keep = line.split(":", 1)[0], True
        elif section == "operations" and line.startswith("  ") and line[2:3].isalpha():
            keep = line.strip().removesuffix(":") in used
        if keep:
            kept.append(line)
    return "".join(kept)


def stage_tasks(
    destination: Path,
    *,
    root: Path,
    benchmarks: tuple[Benchmark, ...],
    mode="oracle",
    submissions: dict | None = None,
    cases: dict[str, dict] | None = None,
    catalog: str = "full",
    judge_model: str | None = None,
) -> dict:
    """Materialize positive native packages from the selected frozen benchmark contracts."""
    selected = {item.name: item for item in benchmarks}
    if not selected or len(selected) != len(benchmarks):
        raise ValueError("Unknown, duplicate, or empty scenario selection")
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
    if cases is not None and not set(cases) <= {
        name for name, item in selected.items() if selected_cases(item) is not None
    }:
        raise ValueError("Cases given for a benchmark without a declared selection record")
    bounds = verifier_bounds(benchmarks, mode, submissions, cases)
    for name, benchmark in selected.items():
        files = {item.destination: item.source for item in benchmark.files}
        native = validate_config(files[benchmark.harbor_task].read_text())
        if bounds[name] > native.verifier.timeout_sec:
            raise ValueError(f"{name}: its observation plan exceeds the native verifier phase")
    destination.mkdir(parents=True, exist_ok=False)
    hashes = {}
    for name, benchmark in selected.items():
        files = {item.destination: item.source for item in benchmark.files}
        instruction = (
            generation_prompt(root, benchmark, catalog).encode()
            if mode == "generation"
            else files["instruction.md"].read_bytes()
        )
        chosen = (cases or {}).get(name)
        options: dict[str, Any] = {
            "mode": "stub",
            "deadline_seconds": declared_deadline(planned_config(benchmark, mode, submissions)),
        }
        if judge_model is not None and benchmark.budgets.judge_calls:
            options.update(judge_mode="codex", judge_model=judge_model)
        if chosen is not None:
            options["cases"] = chosen
        if mode == "generation" and "prepare" in benchmark.entrypoints:
            options["admission"] = True
        replayed = Path(submissions[name]["path"]).read_bytes() if submissions else None
        if replayed is not None:
            options["submission_sha256"] = hashlib.sha256(replayed).hexdigest()
        stage_selected(
            benchmark,
            destination / name,
            root,
            options,
            instruction=instruction,
            submission=replayed,
            oracle=mode != "generation",
            cases=chosen if chosen is not None else selected_cases(benchmark),
        )
        hashes[name] = hashlib.sha256(instruction).hexdigest()
    return hashes
