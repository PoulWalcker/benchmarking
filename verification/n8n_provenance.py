"""n8n evidence adapter: bind authored claims to native node execution records."""

import json
from typing import TYPE_CHECKING

if TYPE_CHECKING or __package__:
    from .contracts import Rejected, WorkflowObservation, equal, require
    from .roles import RoleContract, bind_roles, resolve
else:  # Standalone Harbor distribution.
    from contracts import Rejected, WorkflowObservation, equal, require
    from roles import RoleContract, bind_roles, resolve


def rows(record: dict) -> list[dict]:
    return [
        item["json"]
        for channel in record.get("data", {}).get("main", [])
        for item in (channel or [])
        if isinstance(item, dict) and "json" in item
    ]


def one_run(run: dict, node: str) -> tuple[dict, dict]:
    records = run.get("run_data", {}).get(node, [])
    require(len(records) == 1, f"Expected exactly one real n8n execution of {node}")
    record = records[0]
    require(not record.get("error"), f"n8n node {node} failed")
    values = rows(record)
    require(len(values) == 1, f"Expected one envelope from {node}")
    return record, values[0]


def check_provenance(run: dict) -> None:
    if "report_schema" in run:
        require(run["report_schema"] == "sapi-lab-execution/v1", "Unsupported execution report schema")
        execution = run.get("execution")
        evidence = run.get("evidence")
        if not isinstance(execution, dict) or not isinstance(evidence, dict):
            raise Rejected("Missing engine evidence metadata")
        engine = execution.get("engine")
        native = evidence.get("engine")
        require(isinstance(engine, dict) and engine.get("name") == "n8n", "Unsupported execution engine")
        if not isinstance(native, dict) or native.get("kind") != "n8n":
            raise Rejected("Unsupported execution evidence adapter")
        require(native.get("source") == "engine_execution_records", "Evidence is not native engine records")
    require(run.get("import_exit_code") == 0, "Workflow was not imported successfully into n8n")
    require(bool(run.get("workflow_id")), "Missing imported workflow ID")
    require(bool(run.get("execution_id")), "Missing actual n8n execution ID")
    require(run.get("n8n_version") == "2.41.5", "Actual n8n version differs from the pinned task environment")
    require(isinstance(run.get("run_data"), dict) and run["run_data"], "Missing n8n node execution records")


def observe_execution(
    scenario: str, inputs: dict, run: dict, mode: str = "stub", *, config: dict | None = None, contract: RoleContract
) -> tuple[WorkflowObservation, dict[str, dict]]:
    check_provenance(run)
    require(run.get("status") == "success", "n8n runtime did not succeed")
    require(run.get("execute_exit_code") == 0, "n8n execute returned a failure")
    require(run.get("persisted_status") == "success", "n8n persisted execution did not succeed")
    require(run.get("result_node_present") is True, "No actual Result node execution")
    _, final = one_run(run, "Result")
    require("output" in final and isinstance(final.get("trace"), list), "Incomplete Result output")
    # Compare the adapter extraction with the raw node record, then verify the raw value.
    equal(run["output"], final["output"], "Adapter output differs from raw Result")
    equal(run["result"], final, "Adapter Result envelope differs from raw n8n record")
    require(final.get("llm_mode") == mode, "Wrong LLM execution mode")
    require(
        final.get("spec_revision") == "06ddd3333109cea8a2cb3071609070d7a3c0d3ff", "Wrong source specification revision"
    )
    require(final.get("workflow_ref", {}).get("id") == scenario, "Wrong logical workflow identity")
    require(
        type(final["workflow_ref"].get("revision")) is int and final["workflow_ref"]["revision"] > 0,
        "Missing logical definition revision",
    )
    trace = final["trace"]
    require(len(trace) == len(contract["roles"]), "Wrong number of logical operation events")
    events = {}
    for event in trace:
        require(isinstance(event, dict), "Invalid logical event")
        sid = event.get("step_id")
        require(isinstance(sid, str) and sid not in events, "Repeated or missing logical occurrence")
        events[sid] = event
    require(config is not None, "Submitted graph required for occurrence acceptance")
    assert config is not None
    roles = bind_roles(scenario, config, contract)
    equal(set(events), set(roles.values()), "Missing or extra logical occurrence")
    obligations = {roles[role]: value for role, value in contract["roles"].items()}
    for sid, event in events.items():
        equal(event.get("operation"), obligations[sid]["operation"], "Occurrence operation mismatch")
    states = {}
    records = {}
    mapping = run.get("mapping", {})
    equal(set(mapping), set(events), "Execution map has missing or extra occurrences")
    require(len(set(mapping.values())) == len(mapping), "Execution map aliases distinct occurrences")
    for sid, event in events.items():
        operation = event["operation"]
        require(isinstance(sid, str) and sid in mapping, "Logical step is missing from execution map")
        record, envelope = one_run(run, mapping[sid])
        equal(envelope.get("inputs"), inputs, f"Input loss at {operation}")
        equal(envelope.get("events", {}).get(sid), event, f"Trace disagrees with executed node {operation}")
        require(envelope.get("statuses", {}).get(sid) == event.get("status"), "Step status disagrees with node data")
        require(event.get("status") in {"completed", "skipped"}, "Invalid logical status")
        is_present = sid in envelope.get("steps", {})
        require(
            is_present == (event["status"] == "completed"),
            "Skipped operation produced a result or completed operation lost its result",
        )
        states[sid] = envelope
        records[sid] = record
        if obligations[sid]["kind"] == "LLM":
            require(event.get("implementation") == mode, "LLM mode not proven by logical event")
            if mode == "live" and event["status"] == "completed":
                require(bool(event.get("invocation_id")), "Live call has no invocation ID")
    if mode == "live":
        live_operations(run)
    else:
        require(
            not any(name.startswith("Agency ") and values for name, values in run["run_data"].items()),
            "Stub execution reached Agency",
        )
    equal(
        final.get("statuses"),
        {event["step_id"]: event["status"] for event in events.values()},
        "Final status map lost or changed an operation",
    )
    expected_steps = {sid: states[sid]["steps"][sid] for sid, event in events.items() if event["status"] == "completed"}
    equal(final.get("steps"), expected_steps, "Final envelope lost or changed intermediate data")

    observation = WorkflowObservation(final, events, states, roles)
    if config is not None:
        check_graph_evidence(config, inputs, run, observation, mode)
    return observation, records


def check_operation_order(
    scenario: str, inputs: dict, records: dict[str, dict], roles: dict[str, str], contract: RoleContract
) -> None:
    """Check every independent role edge using native execution timing."""
    for first, second in contract["edges"]:
        ordered(records[roles[first]], records[roles[second]], roles[first], roles[second])


def ordered(first: dict, second: dict, first_name: str, second_name: str) -> None:
    for item in (first, second):
        require(type(item.get("startTime")) in (int, float), "Missing n8n node start time")
        require(
            type(item.get("executionTime")) in (int, float) and item["executionTime"] >= 0,
            "Missing n8n node execution duration",
        )
    require(
        first["startTime"] + first["executionTime"] <= second["startTime"],
        f"{second_name} began before {first_name} completed",
    )


def check_graph_evidence(config: dict, inputs: dict, run: dict, observation: WorkflowObservation, mode: str) -> None:
    """Prove each graph edge, merge token, argument lineage, and terminal Result."""
    workflow = config["workflow"]
    equal(
        observation.final["workflow_ref"],
        {"id": workflow["id"], "revision": workflow["revision"]},
        "Result definition revision differs from submitted graph",
    )
    steps = {step["id"]: step for step in workflow["steps"]}
    predecessors = {sid: [first for first, second in workflow["dependencies"] if second == sid] for sid in steps}
    ancestors: dict[str, set[str]] = {}

    def before(sid: str) -> set[str]:
        if sid not in ancestors:
            ancestors[sid] = set(predecessors[sid])
            for parent in predecessors[sid]:
                ancestors[sid].update(before(parent))
        return ancestors[sid]

    def sources(record: dict, expected: list[str]) -> None:
        actual = record.get("source")
        if not isinstance(actual, list) or len(actual) != len(expected):
            raise Rejected("Missing native dependency input")
        equal(
            sorted(item.get("previousNode", "") for item in actual), sorted(expected), "Wrong native dependency source"
        )
        require(
            all(item.get("previousNodeOutput") == 0 and item.get("previousNodeRun") == 0 for item in actual),
            "Wrong native dependency channel",
        )

    def incoming(target: str, parents: list[str], join: str) -> None:
        target_record, _ = one_run(run, target)
        native_parents = [run["mapping"][sid] for sid in parents] or ["Fixture"]
        if len(native_parents) > 1:
            joins = run["run_data"].get(join, [])
            require(len(joins) == 1 and not joins[0].get("error"), "Missing native all-terminal join")
            merged = joins[0]
            sources(merged, native_parents)
            values = rows(merged)
            require(len(values) == len(native_parents), "Join lost a terminal input envelope")
            for parent in native_parents:
                record, envelope = one_run(run, parent)
                require(sum(value == envelope for value in values) == 1, "Join lost or duplicated predecessor envelope")
                ordered(record, merged, parent, join)
            sources(target_record, [join])
            ordered(merged, target_record, join, target)
        else:
            sources(target_record, native_parents)
            record, _ = one_run(run, native_parents[0])
            ordered(record, target_record, native_parents[0], target)

    for sid, step in steps.items():
        envelope = observation.states[sid]
        expected_ids = before(sid) | {sid}
        expected_events = {key: observation.events[key] for key in expected_ids}
        expected_statuses = {key: event["status"] for key, event in expected_events.items()}
        expected_values = {
            key: observation.final["steps"][key] for key in expected_ids if expected_statuses[key] == "completed"
        }
        equal(envelope.get("events"), expected_events, "Occurrence envelope lost or gained event lineage")
        equal(envelope.get("statuses"), expected_statuses, "Occurrence envelope lost or gained status lineage")
        equal(envelope.get("steps"), expected_values, "Occurrence envelope lost or gained output lineage")
        expected_status = "completed"
        if step.get("when"):
            decision = resolve(
                {"ref": step["when"]["ref"]}, inputs, observation.final["steps"], observation.final["statuses"]
            )
            try:
                equal(decision, step["when"]["eq"], "Guard mismatch")
            except Rejected:
                expected_status = "skipped"
        equal(observation.events[sid]["status"], expected_status, "Observed status disagrees with submitted guard")
        equal(observation.events[sid].get("actor"), step.get("actor"), "Observed actor differs from submitted role")
        entry = "Prepare " + sid if step["kind"] == "LLM" and mode == "live" else run["mapping"][sid]
        incoming(entry, predecessors[sid], "Join " + sid)
        if expected_status == "completed":
            arguments = resolve(step["with"], inputs, observation.final["steps"], observation.final["statuses"])
            if step["kind"] == "LLM" and mode == "live":
                _, prepared = one_run(run, entry)
                equal(
                    prepared.get("request", {}).get("inputs"), arguments, "Native request has wrong occurrence inputs"
                )
    terminal = [sid for sid in steps if not any(sid in parents for parents in predecessors.values())]
    incoming("Result", terminal, "Join final")
    allowed_nodes = {"Demo start", "Fixture", "Result", *run["mapping"].values()}
    allowed_nodes.update("Join " + sid for sid, parents in predecessors.items() if len(parents) > 1)
    if len(terminal) > 1:
        allowed_nodes.add("Join final")
    if mode == "live":
        for sid, step in steps.items():
            if step["kind"] == "LLM":
                allowed_nodes.update({"Prepare " + sid, "Guard " + sid})
                if observation.events[sid]["status"] == "completed":
                    allowed_nodes.add("Agency " + sid)
    equal(set(run["run_data"]), allowed_nodes, "Unexpected or absent native graph node")
    equal(
        observation.final["output"],
        resolve(workflow["output"], inputs, observation.final["steps"], observation.final["statuses"]),
        "Result differs from submitted output lineage",
    )


def check_rejection(run: dict, case: dict, config: dict, contract: RoleContract) -> None:
    check_provenance(run)
    require(run.get("status") == "error", "Invalid input was accepted or failed before execution")
    require(not run.get("result_node_present"), "Invalid input produced a successful Result")
    require(not any(rows(record) for record in run["run_data"].get("Result", [])), "Invalid input reached Result")
    error_text = json.dumps(run.get("error"), ensure_ascii=False)
    accepted_diagnostics = [case["error"]] + ([case["schema_error"]] if case.get("schema_error") else [])
    require(
        any(message in error_text for message in accepted_diagnostics),
        "Failure was not the expected domain/schema validation error",
    )
    if case.get("role"):
        binding = bind_roles(config["workflow"]["id"], config, contract)
        require(case["role"] in binding, "Unknown rejecting role")
        step_ids = [binding[case["role"]]]
        step = next(step for step in config["workflow"]["steps"] if step["id"] == step_ids[0])
        require(step["uses"] == case["operation"], "Rejecting role operation mismatch")
    else:
        step_ids = [step["id"] for step in config["workflow"]["steps"] if step["uses"] == case["operation"]]
    require(len(step_ids) == 1, "Missing or ambiguous rejecting occurrence")
    node = run.get("mapping", {}).get(step_ids[0])
    require(
        node in run["run_data"] and any(record.get("error") for record in run["run_data"][node]),
        "Expected operation did not fail inside n8n",
    )


def live_operations(run: dict, compiled: dict | None = None) -> list[dict]:
    """Bind each live trace event to native Prepare/Guard/HTTP/Restore records, for audit reconciliation."""
    calls = []
    _, final = one_run(run, "Result")
    expected_http = set()
    for event in final.get("trace", []):
        if event.get("implementation") != "live":
            continue
        call = _bind_live_operation(run, compiled, final, event)
        if call is not None:
            calls.append(call)
            expected_http.add(call["http_node"])
    observed_http = {
        name for name, records in run.get("run_data", {}).items() if name.startswith("Agency ") and records
    }
    equal(observed_http, expected_http, "Unmatched native Agency execution")
    if compiled is not None:
        graph_http = {node["name"] for node in compiled["nodes"] if node["type"] == "n8n-nodes-base.httpRequest"}
        require(expected_http <= graph_http, "Native HTTP missing from compiled graph")
        require(
            set(run["run_data"]) <= {node["name"] for node in compiled["nodes"]},
            "Native node missing from compiled graph",
        )
    return calls


def failed_nodes(run: dict) -> list[dict]:
    """Return native node errors and their execution start times."""
    return [
        {
            "node": node,
            "name": record["error"].get("name"),
            "message": record["error"].get("message"),
            "startTime": record.get("startTime"),
        }
        for node, records in run.get("run_data", {}).items()
        for record in records
        if record.get("error")
    ]


def executed_live_operations(run: dict, compiled: dict | None) -> list[dict]:
    """Bind executed live calls from Restore envelopes when Result was not reached."""
    calls = []
    for name, records in run.get("run_data", {}).items():
        if not name.startswith("Agency ") or not records:
            continue
        sid = name.removeprefix("Agency ")
        _, envelope = one_run(run, sid + " [LLM LIVE]")
        event = envelope.get("events", {}).get(sid)
        require(
            isinstance(event, dict) and event.get("step_id") == sid and event.get("implementation") == "live",
            "Missing native live Restore event",
        )
        call = _bind_live_operation(run, compiled, envelope, event)
        require(call is not None, "Executed Agency occurrence was skipped")
        assert call is not None
        calls.append(call)
    equal(
        {call["http_node"] for call in calls},
        {name for name, records in run.get("run_data", {}).items() if name.startswith("Agency ") and records},
        "Unmatched native Agency execution",
    )
    if compiled is not None:
        require(
            set(run.get("run_data", {})) <= {node["name"] for node in compiled["nodes"]},
            "Native node missing from compiled graph",
        )
    return calls


def _bind_live_operation(run: dict, compiled: dict | None, final: dict, event: dict) -> dict | None:
    """Bind one occurrence using the native live chain."""
    sid = event["step_id"]
    prepare, guard, http = "Prepare " + sid, "Guard " + sid, "Agency " + sid
    restored = run.get("mapping", {}).get(sid)
    require(restored == sid + " [LLM LIVE]", "Live Restore mapping differs from compiled convention")
    prep_record, prepared = one_run(run, prepare)
    guard_record, guarded = one_run(run, guard)
    restore_record, envelope = one_run(run, restored)
    equal(guarded, prepared, "Native Guard changed Prepare request")
    equal(
        guard_record.get("source"),
        [{"previousNode": prepare, "previousNodeOutput": 0, "previousNodeRun": 0}],
        "Guard did not receive native Prepare",
    )
    equal(envelope.get("events", {}).get(sid), event, "Native Restore event differs from Result")
    if event["status"] == "skipped":
        channels = guard_record.get("data", {}).get("main", [])
        require(len(channels) == 2 and not channels[0] and bool(channels[1]), "Native false Guard branch missing")
        require(prepared.get("request") is None, "Skipped operation prepared an Agency request")
        ordered(prep_record, guard_record, prepare, guard)
        ordered(guard_record, restore_record, guard, restored)
        require(
            prepared.get("should_run") is False and not run["run_data"].get(http),
            "Skipped operation reached Agency",
        )
        equal(
            restore_record.get("source"),
            [{"previousNode": guard, "previousNodeOutput": 1, "previousNodeRun": 0}],
            "Skipped Restore did not use false Guard branch",
        )
        return None
    require(event["status"] == "completed" and prepared.get("should_run") is True, "Live operation did not execute")
    channels = guard_record.get("data", {}).get("main", [])
    require(len(channels) == 2 and bool(channels[0]) and not channels[1], "Native IF branch does not prove dispatch")
    request = prepared.get("request")
    if not isinstance(request, dict):
        raise Rejected("Missing native prepared request")
    equal(request.get("operation"), event["operation"], "Native operation mismatch")
    equal(request.get("actor"), event.get("actor"), "Native actor mismatch")
    invocation = request.get("invocation_id")
    equal(invocation, event.get("invocation_id"), "Native invocation mismatch")
    ref = final.get("workflow_ref", {})
    equal(
        invocation,
        f"{ref.get('id')}/r{ref.get('revision')}/{sid}/{run.get('workflow_id')}/{run.get('execution_id')}",
        "Invocation is not bound to native execution identity",
    )
    http_record, response = one_run(run, http)
    equal(
        http_record.get("source"),
        [{"previousNode": guard, "previousNodeOutput": 0, "previousNodeRun": 0}],
        "HTTP did not use native true Guard branch",
    )
    equal(
        restore_record.get("source"),
        [{"previousNode": http, "previousNodeOutput": 0, "previousNodeRun": 0}],
        "Restore did not receive native HTTP output",
    )
    require(response.get("status") == "completed", "Native HTTP completion missing")
    equal(response.get("invocation_id"), invocation, "Native HTTP invocation mismatch")
    equal(
        response.get("output"),
        envelope.get("steps", {}).get(sid),
        "Native HTTP response differs from Restore result",
    )
    for first, second in ((prep_record, guard_record), (guard_record, http_record), (http_record, restore_record)):
        require(
            type(first.get("startTime")) in (int, float)
            and type(first.get("executionTime")) in (int, float)
            and type(second.get("startTime")) in (int, float),
            "Missing native live timing",
        )
        require(
            first["startTime"] + first["executionTime"] <= second["startTime"], "Native live node ordering mismatch"
        )
    if compiled is not None:
        nodes = {node["name"]: node for node in compiled["nodes"]}
        for name, kind in ((prepare, "code"), (guard, "if"), (http, "httpRequest"), (restored, "code")):
            require(
                nodes.get(name, {}).get("type") == "n8n-nodes-base." + kind,
                "Native live node missing from compiled graph",
            )
        for source, target in ((prepare, guard), (guard, http), (http, restored)):
            require(
                {"node": target, "type": "main", "index": 0}
                in compiled["connections"].get(source, {}).get("main", [[]])[0],
                "Compiled live connection missing",
            )
        require(nodes[http].get("retryOnFail", False) is False, "Compiled HTTP retries are not permitted")
        require(nodes[http]["parameters"]["options"]["timeout"] == 190000, "Unexpected live HTTP timeout")
    return {"step_id": sid, "http_node": http, "request": request, "response": response}
