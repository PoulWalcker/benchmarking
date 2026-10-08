"""Reconcile independent native execution evidence with outgoing wrapper records."""

from __future__ import annotations

import hashlib
from pathlib import Path

from sapi_config_lab.contracts import CompileOptions
from sapi_config_lab.coordinate.replay import read_json, require
from sapi_config_lab.evidence import digest, sha256
from sapi_config_lab.execute.agency import MAX_BODY, build_prompt
from sapi_config_lab.profile import read_bindings


def reconcile_dispatches(native: list[dict], audit: list[dict], model: str, *, bindings: Path) -> list[dict]:
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
    catalog = read_bindings(bindings)
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


def collect_native(
    trials: list[dict], submissions: dict, cohorts: dict[str, set[str]], bridge_url: str, benchmarks: dict
) -> list[dict]:
    """Re-check native artifacts of live trials and return each observed model call."""
    from verification import n8n_provenance as provenance
    from verification import verify as verification

    native: list[dict] = []
    observed = set()
    for trial in trials:
        scenario = trial["task_name"]
        verifier = Path(trial["result_path"]).parent / "verifier"
        directory = verifier / "evidence"
        submission = submissions[scenario]
        benchmark = benchmarks[scenario]
        from sapi_config_lab.benchmark_loading import freeze_identity, load_entrypoints
        from sapi_config_lab.coordinate.backend import N8nBackend

        options = {"mode": "live", "deadline_seconds": 600, "selected_case": next(iter(cohorts[scenario]))}
        if "cases" in submission:
            options["cases"] = submission["cases"]
        frozen = freeze_identity(benchmark, options)
        planned = load_entrypoints(benchmark, frozen).plan(Path(submission["path"]), options)
        manifest = read_json(directory / "observation.json")
        require(manifest.get("plan_sha256") == digest(planned), "Recorded observation plan differs")
        require(sha256(directory / "submission.yaml") == submission["sha256"], "Recorded submission differs")
        require(
            [row["name"] for row in manifest["entries"]] == [entry["name"] for entry in planned["entries"]],
            "Recorded observations differ",
        )
        for row in manifest["entries"]:
            artifact = directory / "cases" / row["name"]
            require(row["files"] == verification.inventory(artifact), "Recorded native files changed")
            verification.check_case_record(directory / "cases" / row["name"], row["files"], row["name"])
        files = {item.destination: item.source for item in benchmark.files}
        backend = N8nBackend(operation_source=files[benchmark.operations].read_text())
        for entry in planned["entries"]:
            name = entry["name"]
            require(name in cohorts[scenario] and (scenario, name) not in observed, "Unexpected or duplicate live case")
            observed.add((scenario, name))
            artifact = directory / "cases" / name
            run = read_json(artifact / "case.json")
            graph = read_json(artifact / "workflow.json")
            compiled = backend.compile(
                entry["config"],
                read_bindings(files[benchmark.bindings]),
                CompileOptions("live", bridge_url, **planned.get("compile_options", {})),
            )
            require(
                {**compiled.document, "id": run["workflow_id"], "active": False} == graph
                and compiled.mapping == run["mapping"],
                "Saved graph differs from selected compiler sources",
            )
            calls = provenance.live_operations(run, graph)
            native.extend({"scenario": scenario, "case": name, **call} for call in calls)
    expected = {(scenario, name) for scenario in {t["task_name"] for t in trials} for name in cohorts[scenario]}
    require(observed == expected, "Missing required live case")
    return native
