"""Positive materialization of a selected benchmark and the generic workflow runtime."""

import json
from pathlib import Path
import tempfile

import sapi_config_lab
from sapi_config_lab.benchmark import Benchmark
from sapi_config_lab.evidence import json_text, sha256
from sapi_config_lab.harbor_integration.tasks import stage_benchmark
import verification

# Explicit generic runtime closure; no host configuration, provider, ledger or legacy dispatch module.
CORE_FILES = (
    "__init__.py",
    "contracts.py",
    "evidence.py",
    "paths.py",
    "profile.py",
    "pinned_source.py",
    "net.py",
    "compile/__init__.py",
    "compile/n8n.py",
    "compile/refinement.py",
    "compile/runtime-fragment.js",
    "evaluate/__init__.py",
    "evaluate/records.py",
    "execute/__init__.py",
    "execute/n8n.py",
    "coordinate/__init__.py",
    "coordinate/backend.py",
    "coordinate/cases.py",
    "coordinate/observe.py",
    "coordinate/benchmark_worker.py",
)
VERIFIER_FILES = (
    "__init__.py",
    "contracts.py",
    "fixture.py",
    "verify.py",
    "roles.py",
    "n8n_provenance.py",
    "refinement.py",
    "rubric.py",
    "rubric_facts.py",
)


def stage_selected(
    benchmark: Benchmark,
    destination: Path,
    root: Path,
    options: dict,
    *,
    instruction: bytes,
    submission: bytes | None = None,
    oracle: bool = True,
    cases: dict | None = None,
) -> None:
    package = Path(sapi_config_lab.__file__).resolve().parent
    verifier = Path(verification.__file__).resolve().parent
    files = {"sapi_config_lab/" + relative: package / relative for relative in CORE_FILES}
    files.update({"verification/" + relative: verifier / relative for relative in VERIFIER_FILES})
    stage_benchmark(
        benchmark,
        destination,
        options,
        core_files=files,
        instruction=instruction,
        submission=submission,
        oracle=oracle,
        host_records={"cases.json": json_text({benchmark.name: cases}).encode()} if cases is not None else None,
    )


def validate_selected(benchmark: Benchmark, task: Path, root: Path, selected: dict) -> None:
    """Rebuild the positive package from current sources and admitted selection before paid dispatch."""
    metadata = json.loads((task / "tests/benchmark.json").read_text())
    options = metadata["options"]
    if (
        set(options) - {"mode", "deadline_seconds", "cases", "submission_sha256", "judge_mode", "judge_model"}
        or options.get("mode") != "stub"
    ):
        raise ValueError("Unexpected staged selection options")
    from sapi_config_lab.profile import read

    deadline = read(selected["path"])["execution"]["deadline_seconds"]
    if options.get("deadline_seconds") != deadline:
        raise ValueError("Staged workflow deadline changed")
    if "cases" in options and options["cases"] != selected.get("cases"):
        raise ValueError("Staged fixture selection changed")
    if "submission_sha256" in options and options["submission_sha256"] != selected["sha256"]:
        raise ValueError("Staged replay identity changed")
    if sha256(task / "solution/config.yaml") != selected["sha256"]:
        raise ValueError("Staged submission hash mismatch")
    if "cases_sha256" in selected and sha256(task / "tests/cases.json") != selected["cases_sha256"]:
        raise ValueError("Staged fixture hash mismatch")
    with tempfile.TemporaryDirectory() as temporary:
        expected = Path(temporary) / benchmark.name
        stage_selected(
            benchmark,
            expected,
            root,
            options,
            instruction=(benchmark.directory / "instruction.md").read_bytes(),
            submission=Path(selected["path"]).read_bytes(),
            cases=selected.get("cases"),
        )

        def inventory(directory):
            return {
                path.relative_to(directory).as_posix(): sha256(path) for path in directory.rglob("*") if path.is_file()
            }

        if any(path.is_symlink() for path in task.rglob("*")) or inventory(task) != inventory(expected):
            raise ValueError("Staged versioned package differs from current sources and selection")
