"""Independent generic refinement history and native sequential-attempt verification."""

from collections.abc import Callable
import copy
import math
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING or __package__:
    from .contracts import equal, require
    from .n8n_provenance import check_provenance, one_run, ordered, rows
else:
    from contracts import equal, require
    from n8n_provenance import check_provenance, one_run, ordered, rows


def resolve(value: Any, context: dict) -> Any:
    """Resolve recorded workflow references independently of the runtime."""
    if isinstance(value, dict):
        if set(value) == {"ref"}:
            result = context
            for part in value["ref"].split("."):
                require(isinstance(result, dict) and part in result, "Missing refinement reference: " + value["ref"])
                result = result[part]
            return result
        return {key: resolve(item, context) for key, item in value.items()}
    if isinstance(value, list):
        return [resolve(item, context) for item in value]
    return value


def json_value(value: Any) -> Any:
    """Preserve JSON types and JavaScript object order for the runtime's stringify comparison."""
    if value is None:
        return ("null",)
    if isinstance(value, bool):
        return ("boolean", value)
    if isinstance(value, (int, float)):
        try:
            number = float(value)
        except OverflowError:
            number = math.inf
        return ("number", number) if math.isfinite(number) else ("null",)
    if isinstance(value, str):
        return ("string", value.encode("utf-16-le", errors="surrogatepass"))
    if isinstance(value, list):
        return ("array", tuple(json_value(item) for item in value))
    require(isinstance(value, dict), "Refinement predicate requires JSON values")
    # ECMAScript enumerates array-index keys before ordinary insertion-ordered keys.
    indexes = sorted(
        key
        for key in value
        if len(key) <= 10 and key.isascii() and key.isdecimal() and str(int(key)) == key and int(key) < 2**32 - 1
    )
    indexes.sort(key=int)
    keys = [*indexes, *(key for key in value if key not in indexes)]
    return ("object", tuple((json_value(key), json_value(value[key])) for key in keys))


def check_refinement_history(
    config: dict, attempts: list[dict], *, check_steps: Callable[[dict, dict], None]
) -> dict[str, Any]:
    """Recompute carry, stopping and output after an independent check of each attempt."""
    policy = config["execution"]["refinement"]
    workflow = config["workflow"]
    require(
        policy["exhausted"] == "failed" and policy["output_policy"] == "last_accepted_only",
        "Unsupported refinement output policy",
    )
    ids = [step["id"] for step in workflow["steps"]]
    maximum = policy["max_attempts"]
    require(type(maximum) is int and maximum > 0, "Invalid refinement attempt ceiling")
    require(isinstance(attempts, list) and 1 <= len(attempts) <= maximum, "Wrong refinement attempt count")
    runtime = copy.deepcopy(policy["initial_state"])
    accepted_attempt = None
    for number, attempt in enumerate(attempts, 1):
        require(accepted_attempt is None, "Refinement continued after acceptance")
        equal(attempt.get("number"), number, "Missing or reordered attempt")
        equal(attempt.get("runtime"), runtime, "Attempt did not consume actual prior output and feedback")
        require(isinstance(attempt.get("steps"), dict), "Missing attempt step results")
        equal(set(attempt["steps"]), set(ids), "Missing or extra attempt output")
        check_steps(config, attempt)
        context = {"inputs": workflow["inputs"], "runtime": runtime, "steps": attempt["steps"]}
        accepted = json_value(resolve({"ref": policy["until"]["ref"]}, context)) == json_value(policy["until"]["eq"])
        equal(attempt.get("statuses"), dict.fromkeys(ids, "completed"), "Incomplete attempt")
        equal(
            attempt.get("output"), resolve(workflow["output"], context), "Attempt output differs from declared output"
        )
        equal(attempt.get("accepted"), accepted, "Attempt acceptance differs from independently checked predicate")
        if accepted:
            accepted_attempt = number
        runtime = resolve(policy["carry"], context)
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


def refinement_model_calls(config: dict) -> dict[str, str]:
    """Reserve each declared model occurrence once per bounded attempt."""
    workflow = config["workflow"]
    return {
        f"{workflow['id']}/r{workflow['revision']}/{step['id']}/attempt{number}": step["uses"]
        for number in range(1, config["execution"]["refinement"]["max_attempts"] + 1)
        for step in workflow["steps"]
        if step["kind"] == "LLM"
    }


def verify_refinement(
    config: dict, run: dict, *, check_steps: Callable[[dict, dict], None], mode: str = "live"
) -> dict[str, Any]:
    """Check native sequential refinement; callers independently check steps and reconcile paid calls."""
    require(mode in {"stub", "live"}, "Unsupported refinement mode")
    check_provenance(run)
    workflow = config["workflow"]
    declared = workflow["steps"]
    by_id = {step["id"]: step for step in declared}
    require(bool(by_id) and len(by_id) == len(declared), "Missing or duplicate refinement occurrence")
    dependencies = workflow["dependencies"]
    successors = {}
    incoming = set()
    for source, target in dependencies:
        require(source in by_id and target in by_id and source != target, "Invalid refinement dependency")
        require(source not in successors and target not in incoming, "Expected sequential refinement region")
        successors[source] = target
        incoming.add(target)
    roots = set(by_id) - incoming
    require(len(roots) == 1, "Expected one sequential refinement root")
    ids = []
    current = roots.pop()
    while current not in ids:
        ids.append(current)
        if current not in successors:
            break
        current = successors[current]
    require(len(ids) == len(by_id) and len(dependencies) == len(ids) - 1, "Expected sequential refinement region")
    steps = [by_id[sid] for sid in ids]
    require(not any(step.get("when") for step in steps), "Conditional refinement verification is unsupported")
    workflow = config["workflow"]
    maximum = config["execution"]["refinement"]["max_attempts"]
    mapping = run.get("mapping", {})
    equal(
        set(mapping),
        {f"attempt{number}:{sid}" for number in range(1, maximum + 1) for sid in ids},
        "Wrong refinement execution map",
    )
    checkpoints = [number for number in range(1, maximum + 1) if f"Checkpoint {number}" in run["run_data"]]
    equal(checkpoints, list(range(1, len(checkpoints) + 1)), "Noncontiguous native attempts")
    require(bool(checkpoints), "No native refinement checkpoint")
    _, last = one_run(run, f"Checkpoint {checkpoints[-1]}")
    history = last.get("refinement", {})
    attempts = history.get("attempts")
    verdict = check_refinement_history(config, attempts, check_steps=check_steps)
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
        require(isinstance(trace, list) and len(trace) == len(steps), "Missing logical attempt trace")
        events = {event["step_id"]: event for event in trace}
        equal(set(events), set(ids), "Wrong attempt trace occurrences")
        previous = fixture
        completed = []
        for step in steps:
            sid, operation, kind = step["id"], step["uses"], step["kind"]
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
                    resolve(
                        step["with"],
                        {"inputs": workflow["inputs"], "runtime": attempt["runtime"], "steps": attempt["steps"]},
                    ),
                    "Request lost declared input lineage",
                )
                _, response = one_run(run, http)
                equal(response.get("status"), "completed", "Native Agency call did not complete")
                equal(response.get("invocation_id"), invocation, "Agency response identity changed")
                equal(response.get("output"), attempt["steps"][sid], "Agency draft differs from checkpoint")
                calls.append({"step_id": sid, "http_node": http, "request": request, "response": response})
            else:
                edge(native, previous)
            _, envelope = one_run(run, native)
            completed.append(sid)
            equal(envelope.get("inputs"), workflow["inputs"], "Operation input loss")
            equal(envelope.get("runtime"), attempt["runtime"], "Operation carry changed")
            equal(envelope.get("deadline_at_ms"), deadline, "Operation deadline changed")
            equal(
                envelope.get("steps"),
                {key: attempt["steps"][key] for key in completed},
                "Native output lineage changed",
            )
            equal(envelope.get("statuses"), dict.fromkeys(completed, "completed"), "Native status lineage changed")
            equal(envelope.get("events"), {key: events[key] for key in completed}, "Native trace lineage changed")
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
            resolve(
                config["execution"]["refinement"]["carry"],
                {"inputs": workflow["inputs"], "runtime": attempt["runtime"], "steps": attempt["steps"]},
            )
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
        # Result present but without rows: it executed and threw, leaking no draft.
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
