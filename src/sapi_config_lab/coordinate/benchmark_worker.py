"""Compile, observe and evaluate a selected manifest inside Harbor's trusted verifier."""

from functools import partial
import hashlib
import importlib
import json
import os
from pathlib import Path

from sapi_config_lab.coordinate.backend import N8nBackend
from sapi_config_lab.coordinate.cases import run_case
from sapi_config_lab.coordinate.observe import observe
from sapi_config_lab.evidence import write_json
from sapi_config_lab.profile import read_bindings


def main() -> int:
    metadata = json.loads(Path("/tests/benchmark.json").read_text())
    root, output = Path("/tests/payload"), Path("/logs/verifier")
    output.mkdir(parents=True, exist_ok=True)
    reward = output / "reward.txt"
    reward.write_text("0\n")
    identity = metadata["identity"]
    for relative, expected in metadata["core_files"].items():
        if hashlib.sha256((Path("/tests/core") / relative).read_bytes()).hexdigest() != expected:
            raise ValueError("Trusted runtime differs from staged identity: " + relative)
    for relative, expected in metadata["payload_files"].items():
        path = root / relative
        if path.is_symlink() or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError("Benchmark differs from staged identity: " + relative)
    functions = {}
    for role in ("plan", "evaluate"):
        entry = metadata["entrypoints"][role]
        module = importlib.import_module(entry["module"])
        functions[role] = getattr(module, entry["symbol"])
    submission = Path("/submission/config.yaml")
    options = {
        **metadata["options"],
        "submission": str(submission),
        "evaluation": str(output / "evaluation"),
        "identity": {"benchmark": identity, "core_files": metadata["core_files"]},
    }
    options["mode"] = os.environ.get("SAPI_LLM_MODE", options.get("mode", "stub"))
    options["selected_case"] = os.environ.get("SAPI_CASE_NAME", options.get("selected_case"))
    expected = options.get("submission_sha256") or os.environ.get("SAPI_EXPECTED_SUBMISSION_SHA256")
    if expected and submission.is_file() and hashlib.sha256(submission.read_bytes()).hexdigest() != expected:
        raise ValueError("Container submission hash mismatch")
    try:
        plan = functions["plan"](submission, options)
    except OSError, ValueError, AssertionError, KeyError, TypeError:
        plan = None
    if plan is not None:
        write_json(output / "plan.json", plan)
        backend = N8nBackend(operation_source=(root / metadata["operations"]).read_text())
        observe(
            plan,
            submission,
            output / "evidence",
            runner=partial(run_case, backend=backend),
            bindings=read_bindings(root / metadata["bindings"]),
            bridge_url=os.environ.get("SAPI_BRIDGE_URL"),
        )
    result = functions["evaluate"](output / "evidence", options)
    if any(result.get(key) is not None and type(result[key]) is not bool for key in ("execution", "acceptance")):
        raise ValueError("Benchmark verdict facts must be booleans or null")
    write_json(output / "result.json", result)
    reward.write_text("1\n" if result["acceptance"] else "0\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
