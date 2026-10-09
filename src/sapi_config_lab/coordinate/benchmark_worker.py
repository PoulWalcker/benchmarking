"""Compile, observe and evaluate a selected manifest inside Harbor's trusted verifier."""

from functools import partial
import hashlib
import importlib
import json
import os
from pathlib import Path

import yaml

from sapi_config_lab.contracts import CompileOptions, RunBinding
from sapi_config_lab.coordinate.backend import N8nBackend
from sapi_config_lab.coordinate.cases import run_case
from sapi_config_lab.coordinate.observe import observe
from sapi_config_lab.evaluate.records import NOT_EVALUATED, validate_result
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


def main() -> int:
    metadata = json.loads(Path("/tests/benchmark.json").read_text())
    root, output = Path("/tests/payload"), Path("/logs/verifier")
    output.mkdir(parents=True, exist_ok=True)
    reward = output / "reward.txt"
    identity = metadata["identity"]
    for relative, expected in metadata["core_files"].items():
        if hashlib.sha256((Path("/tests/core") / relative).read_bytes()).hexdigest() != expected:
            raise ValueError("Trusted runtime differs from staged identity: " + relative)
    for relative, expected in metadata["payload_files"].items():
        path = root / relative
        if path.is_symlink() or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError("Benchmark differs from staged identity: " + relative)
    functions = {}
    for role, entry in metadata["entrypoints"].items():
        module = importlib.import_module(entry["module"])
        functions[role] = getattr(module, entry["symbol"])
    submission = Path("/submission/config.yaml")
    options = {
        **metadata["options"],
        "submission": str(submission),
        "evaluation": str(output / "evaluation"),
        "identity": {"benchmark": identity, "core_files": metadata["core_files"]},
        "config": metadata.get("config", {}),
    }
    options["mode"] = os.environ.get("SAPI_LLM_MODE", options.get("mode", "stub"))
    options["selected_case"] = os.environ.get("SAPI_CASE_NAME", options.get("selected_case"))
    if options["mode"] == "live" and "prepare" not in functions:
        options["deadline_seconds"] = 600
    metadata["runtime_options"] = {
        key: options[key] for key in ("mode", "selected_case", "deadline_seconds") if key in options
    }
    metadata["submission_sha256"] = (
        hashlib.sha256(submission.read_bytes()).hexdigest() if submission.is_file() else None
    )
    write_json(output / "benchmark.json", metadata)
    expected = options.get("submission_sha256") or os.environ.get("SAPI_EXPECTED_SUBMISSION_SHA256")
    if expected and submission.is_file() and hashlib.sha256(submission.read_bytes()).hexdigest() != expected:
        raise ValueError("Container submission hash mismatch")
    if "prepare" in functions and (options.get("admission") or os.environ.get("SAPI_HOSTED_ADMISSION") == "1"):
        report = admit(metadata, root, submission, options)
        (output / "evaluation").mkdir(exist_ok=True)
        write_json(output / "evaluation/report.json", report)
        write_json(output / "result.json", NOT_EVALUATED)
        reward.write_text("1\n" if report["passed"] else "0\n")
        return 0
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
        backend = N8nBackend(operation_source=(root / metadata["operations"]).read_text())
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
            bindings=read_bindings(root / metadata["bindings"]),
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
        reward.write_text(str(projected) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
