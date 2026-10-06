"""Reconcile independent native execution evidence with outgoing wrapper records."""

from __future__ import annotations

import hashlib
import importlib
import importlib.util
import sys
from pathlib import Path

from sapi_config_lab.evidence import sha256
from sapi_config_lab.coordinate.replay import read_json, require
from sapi_config_lab.paths import CATALOG, workspace_root
from sapi_config_lab.evidence import digest
from sapi_config_lab.execute.agency import MAX_BODY, WRAPPER_MODEL, build_prompt
from sapi_config_lab.coordinate.backend import default_backend
from sapi_config_lab.contracts import CompileOptions
from sapi_config_lab.profile import read_bindings


def reconcile_dispatches(native: list[dict], audit: list[dict], model: str) -> list[dict]:
    """Require ordered, one-to-one native request/dispatch/completion/response proof."""
    attempts = [row for row in audit if row.get("event") == "dispatch_attempt"]
    completions = [row for row in audit if row.get("event") == "completion"]
    replies = [row for row in audit if row.get("event") == "agency_response"]
    require(len(audit) == len(native) * 3, "Unmatched or failed outgoing audit records")
    require(
        len(attempts) == len(completions) == len(replies) == len(native),
        "Missing dispatch/completion/native correspondence",
    )
    ids = [row["request"]["invocation_id"] for row in native]
    require(len(set(ids)) == len(ids), "Duplicate native invocation")
    for records in (attempts, completions, replies):
        require(
            sorted(row.get("invocation_id", "") for row in records) == sorted(ids),
            "Unmatched or duplicate audit invocation",
        )
    catalog = read_bindings(CATALOG)
    table = []
    for index, attempt in enumerate(attempts):
        invocation = attempt["invocation_id"]
        call = next(row for row in native if row["request"]["invocation_id"] == invocation)
        completed = next(row for row in completions if row["invocation_id"] == invocation)
        reply = next(row for row in replies if row["invocation_id"] == invocation)
        require(audit[index * 3 : index * 3 + 3] == [attempt, completed, reply], "Outgoing audit sequence mismatch")
        require(
            all(row.get("schema") == "sapi-lab-dispatch/v1" for row in (attempt, completed, reply)),
            "Unsupported audit schema",
        )
        request = call["request"]
        operation = request["operation"]
        expected = {
            "attempt": index + 1,
            "invocation_id": invocation,
            "operation": operation,
            "inputs_sha256": digest(request["inputs"]),
            "request_sha256": digest(request),
            "prompt_sha256": hashlib.sha256(build_prompt(request, catalog[operation]).encode()).hexdigest(),
        }
        for row in (attempt, completed):
            require(
                all(row.get(key) == value for key, value in expected.items()),
                "Dispatch/native input or prompt identity mismatch",
            )
        require(
            completed.get("response_sha256") == digest(call["response"]),
            "Completion differs from native HTTP response",
        )
        require(
            completed.get("output_sha256") == digest(call["response"]["output"]),
            "Completion differs from native output",
        )
        require(
            reply.get("operation") == operation
            and reply.get("inputs_sha256") == expected["inputs_sha256"]
            and reply.get("status") == "completed"
            and reply.get("http_status") == 200
            and reply.get("model") == model,
            "Agency HTTP reply is not confirmed",
        )
        wrapper = completed.get("wrapper", {})
        require(
            wrapper.get("ok") is True
            and type(wrapper.get("exit_code")) is int
            and wrapper["exit_code"] == 0
            and wrapper.get("model") == model,
            "Wrapper/model completion missing",
        )
        require(
            type(wrapper.get("response_bytes")) is int and 0 < wrapper["response_bytes"] <= MAX_BODY,
            "Wrapper response size missing",
        )
        for key in ("response_sha256", "output_text_sha256", "stderr_sha256"):
            value = wrapper.get(key)
            require(
                isinstance(value, str) and len(value) == 64 and all(c in "0123456789abcdef" for c in value),
                "Wrapper evidence hash missing",
            )
        require(
            all(type(row.get("timestamp_unix")) in (float, int) for row in (attempt, completed, reply)),
            "Audit time missing",
        )
        require(
            attempt["timestamp_unix"] <= completed["timestamp_unix"] <= reply["timestamp_unix"],
            "Audit time order mismatch",
        )
        table.append(
            {
                **{key: value for key, value in call.items() if key not in ("request", "response")},
                **expected,
                "response_sha256": completed["response_sha256"],
                "dispatch_confirmed": True,
                "wrapper_completion": wrapper,
                "provider_call_count": None,
            }
        )
    return table


def load_verifier():
    """Load this checkout's verifier as a private package without changing sys.path."""
    root = workspace_root() / "verification"
    name = "_sapi_live_verification"
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(
            name, root / "__init__.py", submodule_search_locations=[str(root)]
        )
        if spec is None or spec.loader is None:
            raise RuntimeError("Independent verifier could not be loaded")
        package = importlib.util.module_from_spec(spec)
        sys.modules[name] = package
        spec.loader.exec_module(package)
    require(
        Path(sys.modules[name].__file__ or "").resolve() == (root / "__init__.py").resolve(),
        "Verifier checkout identity mismatch",
    )
    return importlib.import_module(name + ".verify"), importlib.import_module(name + ".n8n_provenance")


def collect_native(trials: list[dict], submissions: dict, cohorts: dict[str, set[str]]) -> list[dict]:
    """Re-check native artifacts of live trials and return each observed model call."""
    verification, provenance = load_verifier()
    native = []
    observed = set()
    for trial in trials:
        scenario = trial["task_name"]
        verifier = Path(trial["result_path"]).parent / "verifier"
        directory = verifier / "evidence"
        acceptance = trial["acceptance"]
        submission = submissions[scenario]
        require(
            sha256(directory / "submission.yaml") == submission["sha256"] == acceptance.get("submission_sha256"),
            "Container/source submission mismatch",
        )
        for row in acceptance.get("cases", []):
            name = row["name"]
            require(name in cohorts[scenario] and (scenario, name) not in observed, "Unexpected or duplicate live case")
            observed.add((scenario, name))
            artifact = directory / "cases" / name
            run = read_json(artifact / "case.json")
            config = read_json(artifact / "config.json")
            case = next(case for case in submission["cases"]["positive"] if case["name"] == name)
            # The same plan the container ran; re-checks inventory, inputs and native records.
            planned = verification.plan(scenario, Path(submission["path"]), submission["cases"], "live", name)
            verification.read_evidence(directory, planned)
            require(
                row.get("config_sha256") == sha256(artifact / "config.json"), "Executed fixture configuration mismatch"
            )
            succeeded = case.get("expected") != "exhausted"
            require(
                row.get("passed") is True
                and row.get("acceptance", {}).get("passed") is succeeded
                and read_json(verifier / "evaluation/cases" / name / "acceptance.json").get("passed") is succeeded
                and run.get("execution", {}).get("succeeded") is succeeded,
                "Execution and independent acceptance differ from expected case outcome",
            )
            verification.check_execution(scenario, case["inputs"], run, "live", config=config, case=case)
            graph = read_json(artifact / "workflow.json")
            endpoints = {
                node["parameters"]["url"] for node in graph["nodes"] if node["type"] == "n8n-nodes-base.httpRequest"
            }
            require(len(endpoints) <= 1, "Multiple Agency endpoints")
            bridge = next(iter(endpoints), "http://host.docker.internal:18765")
            compiled = default_backend().compile(config, read_bindings(CATALOG), CompileOptions("live", bridge))
            require(
                {**compiled.document, "id": run["workflow_id"], "active": False} == graph
                and compiled.mapping == run["mapping"],
                "Saved graph differs from current compiler",
            )
            calls = (
                importlib.import_module(verification.__package__ + ".extensions").verify_refinement(config, run)[
                    "calls"
                ]
                if "refinement" in config["execution"]
                else provenance.live_operations(run, graph)
            )
            for call in calls:
                native.append(
                    {
                        "scenario": scenario,
                        "case": name,
                        "submission_sha256": submission["sha256"],
                        "workflow_id": run["workflow_id"],
                        "execution_id": run["execution_id"],
                        **call,
                    }
                )
    expected = {(scenario, name) for scenario in {t["task_name"] for t in trials} for name in cohorts[scenario]}
    require(observed == expected, "Missing required live case")
    return native


def live_cohort(scenario: str, submission: dict) -> list[str]:
    """The cases the verifier plans for a live run of this submission."""
    verification, _ = load_verifier()
    planned = verification.plan(scenario, Path(submission["path"]), submission["cases"], "live")
    return [entry["name"] for entry in planned["entries"]]


def case_budget(scenario: str, case_name: str, config: dict, cases: dict) -> dict:
    """A grant for exactly the model calls the verifier expects for this case."""
    fixture = next(case["inputs"] for case in cases["positive"] if case["name"] == case_name)
    require(config["workflow"]["inputs"] == fixture, "Case grant fixture mismatch")
    verification, _ = load_verifier()
    calls = verification.expected_model_calls(scenario, config, fixture)
    operations: dict[str, int] = {}
    for operation in calls.values():
        operations[operation] = operations.get(operation, 0) + 1
    return {"max_attempts": len(calls), "operations": operations, "model": WRAPPER_MODEL, "occurrences": calls}
