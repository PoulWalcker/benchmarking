"""Reconcile independent native execution evidence with outgoing wrapper records."""

from __future__ import annotations

import copy
import hashlib
import importlib
import importlib.util
import sys
from pathlib import Path

from sapi_config_lab.experiments.replay import read_json, require, sha256
from sapi_config_lab.paths import CATALOG, workspace_root
from sapi_config_lab.runtime.agency import MAX_BODY, build_prompt, canonical_hash
from sapi_config_lab.runtime.composition import default_backend
from sapi_config_lab.core.contracts import CompileOptions
from sapi_config_lab.core.profile import read, read_bindings

OPERATIONS = {"ticket.classify": 2, "research.product": 2, "research.marketing": 2, "research.write": 2}
LIVE_CASES = {
    "invoice-total": {"original-38000", "alternate-values-zero", "maximum-safe-total"},
    "ticket-routing": {"high-three-days", "normal-boundary-two"},
    "competitor-report": {"original-evidence", "unseen-source-markers"},
}


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
            "inputs_sha256": canonical_hash(request["inputs"]),
            "request_sha256": canonical_hash(request),
            "prompt_sha256": hashlib.sha256(build_prompt(request, catalog[operation]).encode()).hexdigest(),
        }
        for row in (attempt, completed):
            require(
                all(row.get(key) == value for key, value in expected.items()),
                "Dispatch/native input or prompt identity mismatch",
            )
        require(
            completed.get("response_sha256") == canonical_hash(call["response"]),
            "Completion differs from native HTTP response",
        )
        require(
            completed.get("output_sha256") == canonical_hash(call["response"]["output"]),
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


def collect_native(
    trials: list[dict],
    submissions: dict,
    *,
    expected_cases: dict[str, set[str]] | None = None,
    cases: dict | None = None,
) -> list[dict]:
    """Re-run acceptance over native artifacts and verify the exact executed inputs."""
    # Same independent verifier used in Harbor; experiments require the checkout.
    verification, provenance = load_verifier()

    cases = read_json(workspace_root() / "verification/cases.json") if cases is None else cases
    selected_cases = LIVE_CASES if expected_cases is None else expected_cases
    native = []
    observed_cases = set()
    for trial in trials:
        scenario = trial["task_name"]
        directory = Path(trial["result_path"]).parent / "verifier"
        acceptance = trial["acceptance"]
        require(
            sha256(directory / "submission.yaml")
            == submissions[scenario]["sha256"]
            == acceptance.get("submission_sha256"),
            "Container/source submission mismatch",
        )
        for row in acceptance.get("cases", []):
            name = row["name"]
            require(
                name in selected_cases[scenario] and (scenario, name) not in observed_cases,
                "Unexpected or duplicate live case",
            )
            observed_cases.add((scenario, name))
            artifact = directory / "cases" / name
            run = read_json(artifact / "case.json")
            config = read_json(artifact / "config.json")
            case = next(case for case in cases[scenario]["positive"] if case["name"] == name)
            fixture = case["inputs"]
            expected = copy.deepcopy(read(Path(submissions[scenario]["path"])))
            expected["workflow"]["inputs"] = fixture
            expected["execution"]["deadline_seconds"] = 600
            require(
                config == expected and row.get("config_sha256") == sha256(artifact / "config.json"),
                "Executed fixture configuration mismatch",
            )
            require(
                row.get("passed") is True
                and row.get("acceptance", {}).get("passed") is (case.get("expected") != "exhausted")
                and run.get("acceptance", {}).get("passed") is (case.get("expected") != "exhausted")
                and run.get("execution", {}).get("succeeded") is (case.get("expected") != "exhausted"),
                "Execution and independent acceptance differ from expected case outcome",
            )
            verification.check_execution(scenario, fixture, run, "live", config=config, case=case)
            metadata = read_json(artifact / "execution.metadata.json")
            persisted = read_json(artifact / "execution.persisted.json")
            execution = read_json(artifact / "execution.json")
            require(
                str(metadata.get("id")) == run["execution_id"]
                and metadata.get("workflowId") == run["workflow_id"]
                and metadata.get("status") == ("error" if case.get("expected") == "exhausted" else "success"),
                "Persisted native identity mismatch",
            )
            require(
                persisted.get("resultData", {}).get("runData")
                == run["run_data"]
                == execution.get("data", {}).get("resultData", {}).get("runData"),
                "Extracted/native/persisted records disagree",
            )
            graph = read_json(artifact / "workflow.json")
            require(
                graph.get("id") == run["workflow_id"] and sha256(artifact / "workflow.json") == run["workflow_sha256"],
                "Imported workflow identity/hash mismatch",
            )
            endpoints = {
                node["parameters"]["url"] for node in graph["nodes"] if node["type"] == "n8n-nodes-base.httpRequest"
            }
            require(len(endpoints) <= 1, "Multiple Agency endpoints")
            bridge = next(iter(endpoints), "http://host.docker.internal:18765")
            compiled = default_backend().compile(config, read_bindings(CATALOG), CompileOptions("live", bridge))
            expected_graph = {**compiled.document, "id": run["workflow_id"], "active": False}
            require(
                expected_graph == graph and compiled.mapping == run["mapping"] == read_json(artifact / "mapping.json"),
                "Saved graph differs from current compiler",
            )
            calls = (
                importlib.import_module(verification.__package__ + ".extensions").verify_refinement(config, run)[
                    "calls"
                ]
                if scenario == "revise-answer"
                else provenance.live_operations(run, graph)
            )
            for call in calls:
                native.append(
                    {
                        "scenario": scenario,
                        "case": name,
                        "submission_sha256": submissions[scenario]["sha256"],
                        "workflow_id": run["workflow_id"],
                        "execution_id": run["execution_id"],
                        **call,
                    }
                )
    expected_pairs = {
        (scenario, name) for scenario in {trial["task_name"] for trial in trials} for name in selected_cases[scenario]
    }
    require(observed_cases == expected_pairs, "Missing required live case")
    return native


def case_budget(scenario: str, case_name: str, config: dict, *, cases: dict | None = None) -> dict:
    """Admit only the named case's independently declared model occurrences."""
    from sapi_config_lab.scenarios import EXPANSION_SCENARIOS, EXTENSION_SCENARIOS

    require(
        scenario in {**EXPANSION_SCENARIOS, **EXTENSION_SCENARIOS}, "Occurrence admission requires a bounded scenario"
    )
    scenario_cases = (read_json(workspace_root() / "verification/cases.json") if cases is None else cases)[scenario]
    require(case_name in scenario_cases["live_cases"], "Case is outside the frozen live cohort")
    fixture = next(case["inputs"] for case in scenario_cases["positive"] if case["name"] == case_name)
    require(config["workflow"]["inputs"] == fixture, "Case admission fixture mismatch")
    verification, _ = load_verifier()
    if scenario == "revise-answer":
        extensions = importlib.import_module(verification.__package__ + ".extensions")
        draft, _ = extensions.reply_roles(config)
        require(
            config["execution"]["refinement"]["max_attempts"] == 3, "Reply admission requires three maximum attempts"
        )
        return {
            "max_attempts": 3,
            "operations": {"reply.generate": 3},
            "model": "gpt-6-astra",
            "occurrences": {
                f"{scenario}/r{config['workflow']['revision']}/{draft}/attempt{number}": "reply.generate"
                for number in range(1, 4)
            },
        }
    roles_module = importlib.import_module(verification.__package__ + ".roles")
    binding = roles_module.bind_roles(scenario, config)
    contract = roles_module.contract_for(scenario)
    roles = [role for role, obligation in contract["roles"].items() if obligation["kind"] == "LLM"]
    if scenario == "priority-support-brief" and fixture["ticket"]["days_overdue"] <= 2:
        roles = ["classify"]
    operations: dict[str, int] = {}
    occurrences = {}
    for role in roles:
        operation = contract["roles"][role]["operation"]
        operations[operation] = operations.get(operation, 0) + 1
        prefix = f"{scenario}/r{config['workflow']['revision']}/{binding[role]}"
        occurrences[prefix] = operation
    return {"max_attempts": len(roles), "operations": operations, "model": "gpt-6-astra", "occurrences": occurrences}
