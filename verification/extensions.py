"""Independent business and native evidence checks for bounded extensions.

These checks do not import the compiler, controller, or operation handlers. A
logical history alone never proves an attempt ran; native records are checked
separately from the independently recomputed reply-review predicate.
"""

import copy
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING or __package__:
    from .contracts import Rejected, equal, require
    from .n8n_provenance import check_provenance, one_run, ordered, rows
else:  # Standalone Harbor distribution.
    from contracts import Rejected, equal, require
    from n8n_provenance import check_provenance, one_run, ordered, rows


def reply_roles(config: dict) -> tuple[str, str]:
    workflow = config["workflow"]
    steps = workflow["steps"]
    require(len(steps) == 2, "Reply refinement requires exactly draft and check occurrences")
    by_operation = {step["uses"]: step for step in steps}
    require(set(by_operation) == {"reply.generate", "reply.check"}, "Wrong reply refinement operations")
    draft, check = by_operation["reply.generate"], by_operation["reply.check"]
    require(draft["kind"] == "LLM" and check["kind"] == "Script", "Wrong refinement operation kinds")
    require(draft["id"] != check["id"], "Duplicate refinement occurrence")
    require(not any(step.get("when") for step in steps), "Reply refinement occurrences cannot be conditional")
    equal(workflow["dependencies"], [[draft["id"], check["id"]]], "Wrong reply refinement dependency")
    equal(
        draft["with"],
        {
            "ticket": {"ref": "inputs.ticket"},
            "previous": {"ref": "runtime.previous"},
            "feedback": {"ref": "runtime.feedback"},
        },
        "Draft lost declared runtime or ticket origin",
    )
    equal(
        check["with"],
        {
            "draft": {"ref": "steps." + draft["id"]},
            "order_id": {"ref": "inputs.required_order_id"},
            "max_characters": {"ref": "inputs.max_characters"},
        },
        "Review lost draft or constraint origin",
    )
    equal(workflow["output"], {"ref": "steps." + draft["id"]}, "Wrong refinement output origin")
    refinement = config["execution"]["refinement"]
    require(len(refinement["region"]) == 2, "Duplicate refinement region occurrence")
    equal(set(refinement["region"]), {draft["id"], check["id"]}, "Wrong refinement region")
    equal(refinement["initial_state"], {"previous": None, "feedback": []}, "Wrong initial refinement state")
    equal(refinement["until"], {"ref": "steps." + check["id"] + ".pass", "eq": True}, "Wrong stop predicate")
    equal(
        refinement["carry"],
        {"previous": {"ref": "steps." + draft["id"]}, "feedback": {"ref": "steps." + check["id"] + ".errors"}},
        "Wrong refinement carry origin",
    )
    require(refinement["exhausted"] == "failed", "Exhaustion must fail")
    require(refinement["output_policy"] == "last_accepted_only", "Unaccepted drafts cannot be final outputs")
    return draft["id"], check["id"]


def review_reply(text: str, order_id: str, max_characters: int) -> dict[str, Any]:
    """The task's review rule, independently stated in Python (UTF-16 length)."""
    require(isinstance(text, str) and bool(text), "Missing reply text")
    require(isinstance(order_id, str) and bool(order_id), "Missing required order ID")
    require(type(max_characters) is int and max_characters > 0, "Invalid reply length limit")
    errors = []
    if order_id not in text:
        errors.append("Include the order ID")
    if len(text.encode("utf-16-le", errors="surrogatepass")) // 2 > max_characters:
        errors.append("Shorten the reply")
    return {"pass": not errors, "errors": errors}


def check_refinement_history(config: dict, attempts: list[dict]) -> dict[str, Any]:
    """Validate content and carry only; this function does not prove execution."""
    draft_id, check_id = reply_roles(config)
    inputs = config["workflow"]["inputs"]
    maximum = config["execution"]["refinement"]["max_attempts"]
    require(type(maximum) is int and maximum > 0, "Invalid refinement attempt ceiling")
    require(isinstance(attempts, list) and 1 <= len(attempts) <= maximum, "Wrong refinement attempt count")
    runtime: dict[str, Any] = {"previous": None, "feedback": []}
    accepted_attempt = None
    for number, attempt in enumerate(attempts, 1):
        require(accepted_attempt is None, "Refinement continued after acceptance")
        equal(attempt.get("number"), number, "Missing or reordered attempt")
        equal(attempt.get("runtime"), runtime, "Attempt did not consume actual prior draft and feedback")
        require(isinstance(attempt.get("steps"), dict), "Missing attempt step results")
        equal(set(attempt["steps"]), {draft_id, check_id}, "Missing or extra attempt output")
        draft = attempt["steps"][draft_id]
        require(isinstance(draft, dict) and set(draft) == {"text"}, "Wrong draft output shape")
        review = review_reply(draft["text"], inputs["required_order_id"], inputs["max_characters"])
        equal(attempt["steps"][check_id], review, "Dishonest independent reply review")
        equal(attempt.get("statuses"), {draft_id: "completed", check_id: "completed"}, "Incomplete attempt")
        equal(attempt.get("output"), draft, "Attempt output differs from real draft")
        equal(attempt.get("accepted"), review["pass"], "Attempt acceptance differs from independent review")
        if review["pass"]:
            accepted_attempt = number
        runtime = {"previous": draft, "feedback": review["errors"]}
    require(
        accepted_attempt is not None or len(attempts) == maximum, "Refinement stopped before acceptance or exhaustion"
    )
    return {
        "accepted_attempt": accepted_attempt,
        "attempt_count": len(attempts),
        "maximum_attempts": maximum,
        "output": attempts[-1]["output"] if accepted_attempt is not None else None,
        "exhausted": accepted_attempt is None,
    }


def verify_refinement(config: dict, run: dict, *, mode: str = "live") -> dict[str, Any]:
    """Check the reply task against one n8n execution, including failed exhaustion.

    Returned calls still require independent bridge/wrapper audit reconciliation.
    A checkpoint history or a controller's success flag is insufficient evidence.
    """
    require(mode in {"stub", "live"}, "Unsupported refinement mode")
    check_provenance(run)
    draft, check = reply_roles(config)
    workflow = config["workflow"]
    maximum = config["execution"]["refinement"]["max_attempts"]
    mapping = run.get("mapping", {})
    equal(
        set(mapping),
        {f"attempt{number}:{sid}" for number in range(1, maximum + 1) for sid in (draft, check)},
        "Wrong refinement execution map",
    )
    checkpoints = [number for number in range(1, maximum + 1) if f"Checkpoint {number}" in run["run_data"]]
    equal(checkpoints, list(range(1, len(checkpoints) + 1)), "Noncontiguous native attempts")
    require(bool(checkpoints), "No native refinement checkpoint")
    _, last = one_run(run, f"Checkpoint {checkpoints[-1]}")
    history = last.get("refinement", {})
    attempts = history.get("attempts")
    verdict = check_refinement_history(config, attempts)
    equal(len(attempts), len(checkpoints), "History claims unexecuted attempts")
    equal(history.get("max_attempts"), maximum, "Native attempt ceiling changed")
    equal(history.get("accepted_attempt"), verdict["accepted_attempt"], "Native acceptance index changed")
    equal(run.get("refinement"), history, "Adapter history differs from native checkpoints")
    allowed = {"Demo start", "Result"}
    calls = []
    deadline = None

    def edge(target: str, source: str, channel: int = 0, *, failed: bool = False) -> dict:
        allowed.add(target)
        source_record, _ = one_run(run, source)
        if failed:
            records = run["run_data"].get(target, [])
            require(len(records) == 1 and records[0].get("error"), "Expected native Result error")
            target_record = records[0]
        else:
            target_record, _ = one_run(run, target)
        equal(
            target_record.get("source"),
            [{"previousNode": source, "previousNodeOutput": channel, "previousNodeRun": 0}],
            "Wrong native refinement edge: " + target,
        )
        ordered(source_record, target_record, source, target)
        return target_record

    for number, attempt in enumerate(attempts, 1):
        prefix = f"Attempt {number} / "
        fixture = "Fixture" if number == 1 else prefix + "Fixture"
        edge(fixture, "Demo start" if number == 1 else f"Continue {number - 1}")
        _, initial = one_run(run, fixture)
        equal(initial.get("inputs"), workflow["inputs"], "Native fixture input changed")
        equal(initial.get("runtime"), attempt["runtime"], "Native attempt consumed different carry")
        for key in ("steps", "statuses", "events"):
            equal(initial.get(key), {}, "Attempt failed to reset local " + key)
        require(initial.get("llm_mode") == mode, "Wrong refinement LLM mode")
        if number == 1:
            deadline = initial.get("deadline_at_ms")
            require(type(deadline) in (int, float), "Missing shared native deadline")
        equal(initial.get("deadline_at_ms"), deadline, "Attempt reset the workflow deadline")
        trace = attempt.get("trace")
        require(isinstance(trace, list) and len(trace) == 2, "Missing logical attempt trace")
        events = {event["step_id"]: event for event in trace}
        equal(set(events), {draft, check}, "Wrong attempt trace occurrences")
        previous = fixture
        for sid, operation, kind in ((draft, "reply.generate", "LLM"), (check, "reply.check", "Script")):
            step = next(item for item in workflow["steps"] if item["id"] == sid)
            native = prefix + sid + (f" [LLM {mode.upper()}]" if kind == "LLM" else "")
            equal(mapping[f"attempt{number}:{sid}"], native, "Incorrect native attempt mapping")
            event = events[sid]
            equal(event.get("operation"), operation, "Wrong attempt operation")
            equal(event.get("actor"), step.get("actor"), "Wrong attempt actor")
            equal(event.get("status"), "completed", "Incomplete attempt event")
            if kind == "LLM":
                equal(event.get("implementation"), mode, "Wrong attempt implementation")
            if kind == "LLM" and mode == "live":
                prepare, guard, http = (prefix + name + sid for name in ("Prepare ", "Guard ", "Agency "))
                edge(prepare, previous)
                edge(guard, prepare)
                edge(http, guard)
                edge(native, http)
                _, prepared = one_run(run, prepare)
                guard_record, guarded = one_run(run, guard)
                equal(guarded, prepared, "Guard changed native request")
                channels = guard_record["data"]["main"]
                require(len(channels) == 2 and bool(channels[0]) and not channels[1], "Missing true dispatch branch")
                equal(prepared.get("should_run"), True, "Draft dispatch was not admitted")
                request = prepared.get("request", {})
                invocation = f"{workflow['id']}/r{workflow['revision']}/{sid}/attempt{number}/{run['workflow_id']}/{run['execution_id']}"
                equal(request.get("invocation_id"), invocation, "Wrong attempt invocation identity")
                equal(event.get("invocation_id"), invocation, "Trace invocation differs from actual request")
                equal(request.get("operation"), operation, "Wrong request operation")
                equal(request.get("actor"), step.get("actor"), "Wrong request actor")
                equal(
                    request.get("inputs"),
                    {"ticket": workflow["inputs"]["ticket"], **attempt["runtime"]},
                    "Draft request lost ticket or actual feedback lineage",
                )
                _, response = one_run(run, http)
                equal(response.get("status"), "completed", "Native Agency call did not complete")
                equal(response.get("invocation_id"), invocation, "Agency response identity changed")
                equal(response.get("output"), attempt["steps"][draft], "Agency draft differs from checkpoint")
                calls.append({"step_id": sid, "http_node": http, "request": request, "response": response})
            else:
                edge(native, previous)
            _, envelope = one_run(run, native)
            ids = [draft] if sid == draft else [draft, check]
            equal(envelope.get("inputs"), workflow["inputs"], "Operation input loss")
            equal(envelope.get("runtime"), attempt["runtime"], "Operation carry changed")
            equal(envelope.get("deadline_at_ms"), deadline, "Operation deadline changed")
            equal(envelope.get("steps"), {key: attempt["steps"][key] for key in ids}, "Native output lineage changed")
            equal(envelope.get("statuses"), {key: "completed" for key in ids}, "Native status lineage changed")
            equal(envelope.get("events"), {key: events[key] for key in ids}, "Native trace lineage changed")
            previous = native
        checkpoint = f"Checkpoint {number}"
        edge(checkpoint, previous)
        _, state = one_run(run, checkpoint)
        continuing = not attempt["accepted"] and number < maximum
        equal(state.get("continue_refinement"), continuing, "Wrong native continuation decision")
        equal(state.get("deadline_at_ms"), deadline, "Checkpoint deadline changed")
        equal(
            state.get("refinement"),
            {
                "max_attempts": maximum,
                "attempts": attempts[:number],
                "accepted_attempt": number if attempt["accepted"] else None,
            },
            "Checkpoint history changed",
        )
        equal(
            state.get("runtime"),
            {"previous": attempt["steps"][draft], "feedback": attempt["steps"][check]["errors"]}
            if continuing
            else attempt["runtime"],
            "Checkpoint failed to carry actual draft and feedback",
        )
        if number < maximum:
            guard = f"Continue {number}"
            guard_record = edge(guard, checkpoint)
            _, guarded = one_run(run, guard)
            equal(guarded, state, "Continuation guard changed checkpoint")
            channels = guard_record["data"]["main"]
            require(
                len(channels) == 2 and bool(channels[0]) == continuing and bool(channels[1]) != continuing,
                "Wrong native continuation channel",
            )

    last_number = len(attempts)
    parent = f"Continue {last_number}" if last_number < maximum else f"Checkpoint {last_number}"
    edge("Result", parent, 1 if last_number < maximum else 0, failed=verdict["exhausted"])
    equal(set(run["run_data"]), allowed, "Unexpected or missing native attempt nodes")
    if verdict["exhausted"]:
        require(
            run.get("status") == "error" and run.get("persisted_status") == "error",
            "Exhaustion was not a native failure",
        )
        require(not rows(run["run_data"]["Result"][0]), "Exhausted workflow emitted a Result")
        # The adapter records node presence even when Result executed and threw.
        # Presence proves execution; successful rows/output would leak a draft.
        equal(run.get("result_node_present"), True, "Failed native Result execution was not recorded")
        require(run.get("output") is None and run.get("result") is None, "Unaccepted output escaped exhaustion")
        require(
            "Refinement exhausted without an accepted result" in str(run["run_data"]["Result"][0]["error"]),
            "Wrong native failure",
        )
    else:
        require(
            run.get("status") == "success"
            and run.get("persisted_status") == "success"
            and run.get("execute_exit_code") == 0,
            "Accepted refinement did not succeed natively",
        )
        _, final = one_run(run, "Result")
        equal(
            final.get("workflow_ref"),
            {"id": workflow["id"], "revision": workflow["revision"]},
            "Wrong accepted revision",
        )
        equal(final.get("llm_mode"), mode, "Wrong accepted mode")
        equal(final.get("spec_revision"), "06ddd3333109cea8a2cb3071609070d7a3c0d3ff", "Wrong pinned specification")
        for key in ("output", "steps", "statuses", "trace"):
            equal(final.get(key), attempts[-1][key], "Final Result is not the accepted attempt: " + key)
        equal(final.get("refinement"), history, "Result changed attempt history")
        equal(run.get("result"), final, "Adapter Result differs from native Result")
        equal(run.get("output"), final["output"], "Adapter output differs from accepted draft")
        equal(run.get("result_node_present"), True, "Native Result was not recorded")
    return {**verdict, "engine_provenance_verified": True, "calls": calls}


def refinement_corruptions(config: dict, run: dict, *, mode: str) -> list[str]:
    """Probe native-record deletion and invented carry on accepted or exhausted runs."""
    draft, _ = reply_roles(config)
    probes = []
    missing = copy.deepcopy(run)
    missing["run_data"].pop(missing["mapping"]["attempt1:" + draft])
    probes.append(("missing-native-draft", missing))
    invented = copy.deepcopy(run)
    rows(invented["run_data"]["Fixture"][0])[0]["runtime"] = {"previous": {"text": "fabricated"}, "feedback": []}
    probes.append(("invented-initial-carry", invented))
    rejected = []
    for name, candidate in probes:
        try:
            verify_refinement(config, candidate, mode=mode)
        except Rejected:
            rejected.append(name)
        else:
            raise Rejected("Accepted refinement evidence corruption: " + name)
    return rejected
