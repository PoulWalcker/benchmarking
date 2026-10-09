"""Compile, observe and evaluate explicit task callbacks inside Harbor's trusted verifier."""

from functools import partial
import hashlib
import json
import os
from pathlib import Path

import yaml

from sapi_config_lab.contracts import CompileOptions, RunBinding
from sapi_config_lab.coordinate.backend import N8nBackend
from sapi_config_lab.coordinate.cases import run_case
from sapi_config_lab.coordinate.observe import observe
from sapi_config_lab.evaluate.records import validate_result
from sapi_config_lab.evidence import write_json
from sapi_config_lab.profile import Invalid, Unsupported, check, read, read_bindings


def admit(metadata: dict, root: Path, submission: Path, options: dict) -> dict:
    """Compile without execution and preserve the unpaid authoring admission gate."""
    report = {
        "schema": "sapi-lab-admission/v1",
        "scenario": metadata["name"],
        "mode": "stub",
        "passed": False,
        "submission_sha256": hashlib.sha256(submission.read_bytes()).hexdigest() if submission.exists() else None,
    }
    try:
        config = read(submission)
        N8nBackend(operation_source=(root / metadata["operations"]).read_text()).compile(
            config,
            read_bindings(root / metadata["bindings"]),
            CompileOptions(llm_mode="live", bridge_url="http://localhost:1", operation_url="http://localhost:2/tools"),
        )
        cap = metadata["budgets"]["runtime_model_calls"]
        calls = sum(step["kind"] == "LLM" for step in config["workflow"]["steps"])
        check(cap is None or calls <= cap, "Runtime cap exceeded")
        check(config["execution"]["deadline_seconds"] == options["deadline_seconds"], "Original deadline required")
        report["passed"] = True
    except (Invalid, Unsupported, OSError, UnicodeDecodeError) as error:
        report["error_type"] = type(error).__name__
        report["error"] = str(error)
    return report


def run_task(
    root: Path,
    output: Path,
    submission: Path,
    options: dict,
    functions: dict,
    *,
    operations: str = "operations.js",
    bindings: str = "bindings.yaml",
) -> int:
    """Compose explicit trusted callbacks over the same recorded observation pipeline."""
    output.mkdir(parents=True, exist_ok=True)
    context = {
        "root": root,
        "output": output,
        "evidence": output / "evidence",
        "submission": submission,
        "plan": None,
        "options": options,
    }
    binding = functions["prepare"](context) if "prepare" in functions else RunBinding()
    if not isinstance(binding, RunBinding):
        raise ValueError("Benchmark prepare must return a RunBinding")
    failure: dict = {"status": "missing_submission", "output": None}
    try:
        plan = functions["plan"](submission, options)
    except (OSError, ValueError, AssertionError, KeyError, TypeError, yaml.YAMLError) as error:
        plan = None
        if submission.is_file():
            failure = {"status": "error", "output": None, "error": {"type": type(error).__name__}}
    context["plan"] = plan
    if plan is not None:
        write_json(output / "plan.json", plan)
        backend = N8nBackend(operation_source=(root / operations).read_text())
        observe(
            plan,
            submission,
            output / "evidence",
            runner=partial(
                run_case,
                backend=backend,
                deadline_at=binding.deadline_at,
                operation_token=binding.operation_token,
                operation_url=binding.operation_url,
            ),
            bindings=read_bindings(root / bindings),
            bridge_url=os.environ.get("SAPI_BRIDGE_URL"),
        )
    if "snapshot" in functions:
        records = sorted((output / "evidence/cases").glob("*/case.json"))
        context["record"] = (
            json.loads(records[0].read_text())
            if len(records) == 1
            else failure
            if plan is None
            else {"status": "unknown", "output": None}
        )
        functions["snapshot"](context)
    result = validate_result(functions["evaluate"](output / "evidence", options))
    write_json(output / "result.json", result)
    projected = result.get("harbor_reward", None if result["acceptance"] is None else int(result["acceptance"]))
    if projected is not None:
        (output / "reward.txt").write_text(str(projected) + "\n")
    return 0
