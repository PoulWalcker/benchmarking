"""Independent lifecycle audit; controller records never substitute for native runs."""

from collections.abc import Callable
from datetime import UTC, datetime
import hashlib
from itertools import pairwise
import json
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

if TYPE_CHECKING or __package__:
    from .contracts import WorkflowObservation, equal, require
    from .n8n_provenance import check_graph_evidence, check_provenance, live_operations, one_run

else:  # Standalone Harbor distribution.
    from contracts import WorkflowObservation, equal, require
    from n8n_provenance import check_graph_evidence, check_provenance, live_operations, one_run


def _hash(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    return hashlib.sha256(payload.encode()).hexdigest()


def native_lifecycle(config: dict, run: dict, admission: dict, *, mode: str) -> dict:
    """Check admitted native workflow identity, deadline and graph evidence without business rules."""
    check_provenance(run)
    require(run.get("status") == "success" and run.get("persisted_status") == "success", "Native run did not succeed")
    equal(run.get("execute_exit_code"), 0, "Native process failed")
    _, final = one_run(run, "Result")
    equal(run.get("result"), final, "Adapter Result changed")
    equal(run.get("output"), final.get("output"), "Adapter output changed")
    equal(final.get("admission"), admission, "Native admission changed")
    equal(final.get("input_source"), "event", "Lifecycle run was only a fixture simulation")
    equal(final.get("simulation"), False, "Lifecycle run was not admitted")
    equal(final.get("llm_mode"), mode, "Wrong lifecycle LLM mode")
    equal(final.get("spec_revision"), config["spec_revision"], "Wrong pinned specification")
    equal(run.get("input", {}).get("activation"), admission, "Adapter event identity changed")
    workflow = config["workflow"]
    steps = workflow["steps"]
    events = {event["step_id"]: event for event in final.get("trace", [])}
    equal(len(final.get("trace", [])), len(steps), "Incomplete workflow trace")
    equal(set(events), {step["id"] for step in steps}, "Workflow trace occurrences changed")
    equal(set(run.get("mapping", {})), set(events), "Wrong native mapping")
    _, fixture = one_run(run, "Fixture")
    equal(fixture.get("admission"), admission, "Fixture event identity changed")
    equal(fixture.get("inputs"), workflow["inputs"], "Fixture input changed")
    require(type(fixture.get("deadline_at_ms")) in (float, int), "Missing lifecycle deadline")
    states = {}
    for step in steps:
        sid = step["id"]
        _, state = one_run(run, run["mapping"][sid])
        equal(events[sid].get("operation"), step["uses"], "Wrong native operation")
        equal(events[sid].get("status"), "completed", "Operation incomplete")
        equal(events[sid].get("implementation"), mode if step["kind"] == "LLM" else "script", "Wrong implementation")
        equal(state.get("inputs"), workflow["inputs"], "Workflow input loss")
        equal(state.get("admission"), admission, "Operation event identity changed")
        equal(state.get("deadline_at_ms"), fixture["deadline_at_ms"], "Operation reset deadline")
        states[sid] = state
    observation = WorkflowObservation(final, events, states, {})
    check_graph_evidence(config, workflow["inputs"], run, observation, mode)
    equal(final.get("statuses"), dict.fromkeys(events, "completed"), "Final status changed")
    equal(final.get("steps"), {sid: states[sid]["steps"][sid] for sid in events}, "Final step data changed")
    return {
        "observation": (observation, final),
        "calls": live_operations(run) if mode == "live" else [],
        "deadline_at_ms": fixture["deadline_at_ms"],
    }


def verify_lifecycle(
    snapshot: dict, *, acceptance: Callable[..., dict], mode: str = "live", require_wall_clock: bool = True
) -> dict:
    """Audit lifecycle facts with an explicitly supplied independent native decision."""
    equal(snapshot.get("schema"), "sapi-lab-lifecycle/v1", "Unsupported lifecycle evidence")
    definitions, events = snapshot["definitions"], snapshot["events"]
    transitions = snapshot["transitions"]
    require(bool(definitions), "Empty lifecycle evidence")
    equal(
        [row.get("sequence") for row in transitions], list(range(1, len(transitions) + 1)), "Transition order changed"
    )
    require(all(type(row.get("at")) in (float, int) for row in transitions), "Missing lifecycle wall clock")
    require(all(a["at"] <= b["at"] for a, b in pairwise(transitions)), "Lifecycle timestamps reversed")

    def matching(kind: str, field: str, value: str) -> list[dict]:
        return [row for row in transitions if row["kind"] == kind and row["data"].get(field) == value]

    def single(kind: str, field: str, value: str) -> dict:
        found = matching(kind, field, value)
        require(len(found) == 1, f"Missing or repeated {kind}: {value}")
        return found[0]

    def before(first: dict, second: dict) -> None:
        require(first["sequence"] < second["sequence"], "Lifecycle transition precedes its cause")

    for key, definition in definitions.items():
        ref, config = definition["workflow_ref"], definition["config"]
        equal(key, f"{ref['id']}@{ref['revision']}", "Definition key differs from revision")
        equal(config["activation"]["workflow_ref"], ref, "Definition activation revision changed")
        equal(
            {name: config["workflow"][name] for name in ("id", "revision")}, ref, "Definition workflow revision changed"
        )
        equal(definition["sha256"], _hash(config), "Immutable definition bytes changed")
        origin = "fork_persisted" if definition["forked_from"] else "registered"
        persisted = single(origin, "definition", key)
        equal(persisted["data"]["sha256"], definition["sha256"], "Persisted definition hash changed")

    calls: list[dict] = []
    executions: list[dict] = []
    passed_tests: dict[str, tuple[str, dict]] = {}
    native_ids = set()
    for key, event in events.items():
        admission = event["admission"]
        equal(
            key,
            _hash({field: admission[field] for field in ("rule_id", "event_id")}),
            "Event deduplication key changed",
        )
        definition = definitions[event["definition"]]
        config = definition["config"]
        equal(admission["workflow_ref"], definition["workflow_ref"], "Event executed another revision")
        callback = admission["kind"] == "Callback"
        require(callback or admission["kind"] == "Cron", "Unknown event kind")
        equal(admission["purpose"], "test" if callback else "scheduled", "Wrong event purpose")
        equal(
            admission["rule_id"],
            config["lifecycle"]["test"]["rule_id"] if callback else config["activation"]["rule_id"],
            "Wrong event rule",
        )
        admitted = single("event_admitted", "event", key)
        equal(admitted["data"]["admission"], admission, "Admission transition differs from event")
        if event["state"] in {"dismissed", "skipped_overlap", "skipped_missed"}:
            require(
                "record" not in event and not matching("execution_reserved", "event", key),
                "Dismissed/overlap event executed",
            )
            if event["state"] == "dismissed":
                equal(event["condition"], False, "True event was dismissed")
            elif event["state"] == "skipped_missed":
                require(
                    not callback and event.get("clock_source") == "system", "Missed run lacks system-clock admission"
                )
                skipped = single("missed_run_skipped", "event", key)
                before(admitted, skipped)
                require(skipped["at"] >= event["scheduled_at"] + 60, "On-time Cron was reported missed")
            else:
                running = [
                    other
                    for other in events
                    if other != key
                    and events[other]["admission"]["kind"] == "Cron"
                    and events[other]["admission"]["workflow_ref"]["id"] == admission["workflow_ref"]["id"]
                    and any(
                        row["sequence"] < admitted["sequence"] for row in matching("execution_reserved", "event", other)
                    )
                    and not any(
                        row["sequence"] < admitted["sequence"] for row in matching("cron_result", "event", other)
                    )
                ]
                require(bool(running), "Overlap skip has no in-flight Cron")
            continue
        if event["state"] in {"queued", "running", "native_complete"}:
            raise AssertionError("Lifecycle evidence is incomplete")
        reserved = single("execution_reserved", "event", key)
        before(admitted, reserved)
        equal(reserved["data"]["definition"], event["definition"], "Reservation revision changed")
        equal(reserved["data"]["deadline_at"], event["deadline_at"], "Reservation deadline changed")
        if event["state"] == "unknown":
            require(snapshot["families"][admission["workflow_ref"]["id"]]["suspended"], "Unknown event did not suspend")
            require("decision" not in event, "Unknown native outcome received an acceptance decision")
            continue
        require(event["state"] in {"passed", "failed"}, "Unknown lifecycle event status")
        run = event["record"]
        identity = (run.get("workflow_id"), run.get("execution_id"))
        require(identity not in native_ids, "One native execution was reused for multiple events")
        native_ids.add(identity)
        native = native_lifecycle(config, run, admission, mode=mode)
        checked = {**native, "passed": acceptance(config, run, admission, mode=mode)["passed"]}
        equal(checked["deadline_at_ms"], event["deadline_at"] * 1000, "Native deadline differs from admission")
        completed = matching("native_complete", "event", key) + matching("native_recovered", "event", key)
        require(len(completed) == 1, "Missing or repeated native completion")
        before(reserved, completed[0])
        outcome = single("test_result" if callback else "cron_result", "event", key)
        before(completed[0], outcome)
        decision = event["decision"]
        equal(outcome["data"]["decision"], decision, "Persisted decision changed")
        equal(decision.get("verifier"), config["lifecycle"]["test"]["verifier"], "Wrong lifecycle verifier")
        equal(decision.get("passed"), checked["passed"], "Controller acceptance differs from native business data")
        equal(event["state"], "passed" if checked["passed"] else "failed", "Event status differs from acceptance")
        require(
            isinstance(decision.get("findings"), list) and bool(decision["findings"]) != checked["passed"],
            "Missing rejection findings or contradictory success",
        )
        if callback and checked["passed"]:
            passed_tests[event["definition"]] = (key, outcome)
        if not callback:
            releases = [
                row
                for row in transitions
                if row["kind"] in {"released", "replacement_activated"}
                and row["data"].get("definition") == event["definition"]
                and row["sequence"] < admitted["sequence"]
            ]
            require(bool(releases), "Cron ran an untested or unreleased revision")
            family = snapshot["families"][admission["workflow_ref"]["id"]]
            schedule = family["schedule"]
            minute, hour, *_ = schedule["schedule"].split()
            date, zone_name = admission["event_id"].split("@")
            equal(zone_name, schedule["timezone"], "Cron timezone changed")
            due = datetime.fromisoformat(date)
            equal((due.hour, due.minute), (int(hour), int(minute)), "Cron event was not due")
            if require_wall_clock:
                equal(event.get("clock_source"), "system", "Injected clock cannot prove real Cron activation")
                actual = datetime.fromtimestamp(admitted["at"], UTC).astimezone(ZoneInfo(zone_name))
                equal(actual.strftime("%Y-%m-%dT%H:%M"), date, "Cron was not admitted at its real due minute")
                equal(
                    datetime.fromtimestamp(event["scheduled_at"], UTC)
                    .astimezone(ZoneInfo(zone_name))
                    .strftime("%Y-%m-%dT%H:%M"),
                    date,
                    "Scheduled minute differs from event identity",
                )
        executions.append(
            {
                "event": key,
                "definition": event["definition"],
                "workflow_id": identity[0],
                "execution_id": identity[1],
                "accepted": checked["passed"],
            }
        )
        calls.extend({"event": key, "definition": event["definition"], **call} for call in checked["calls"])

    for row in transitions:
        if row["kind"] in {"released", "release_pending"}:
            definition = row["data"]["definition"]
            require(definition in passed_tests, "Released definition has no accepted native test")
            event_key, outcome = passed_tests[definition]
            equal(row["data"]["test_event"], event_key, "Release retargeted another test")
            before(outcome, row)
        elif row["kind"] == "restored":
            equal(row["data"]["replayed"], False, "Restore replayed archived work")
            before(single("archived", "definition", row["data"]["definition"]), row)
            require(
                not any(
                    other["sequence"] > row["sequence"]
                    and other["kind"] == "event_admitted"
                    and other["data"]["admission"]["workflow_ref"]
                    == definitions[row["data"]["definition"]]["workflow_ref"]
                    for other in transitions
                ),
                "Restore caused an archived revision to execute again",
            )
        elif row["kind"] == "replacement_activated":
            pending = single("release_pending", "definition", row["data"]["definition"])
            before(pending, row)
            before(single("cron_result", "event", row["data"]["finished_event"]), row)

    for key, rebuild in snapshot["rebuilds"].items():
        source = definitions[rebuild["source"]]
        failed = events[rebuild["test_event"]]
        require(
            failed["state"] == "failed" and failed["admission"]["kind"] == "Callback",
            "Rebuild has no rejected native test",
        )
        equal(rebuild["findings"], failed["decision"], "Rebuild lost actual rejection findings")
        equal(rebuild["source"], failed["definition"], "Rebuild used a different source revision")
        queued = single("rebuild_queued", "rebuild", key)
        before(single("test_result", "event", rebuild["test_event"]), queued)
        require(
            type(rebuild["number"]) is int
            and 1 <= rebuild["number"] <= source["config"]["lifecycle"]["on_test_fail"]["max_rebuilds"],
            "Rebuild ceiling exceeded",
        )
        if rebuild["state"] == "completed":
            reserved = single("rebuild_reserved", "rebuild", key)
            before(queued, reserved)
            persisted = single("fork_persisted", "rebuild", key)
            before(reserved, persisted)
            target_key = persisted["data"]["definition"]
            target = definitions[target_key]
            equal(target["workflow_ref"], rebuild["target"], "Rebuild installed the wrong revision")
            equal(target["forked_from"], rebuild["source"], "Fork lost source identity")
            equal(target["sha256"], rebuild["candidate_sha256"], "Fork differs from rebuilt candidate")
            for field in ("schema", "spec_revision", "lifecycle", "execution"):
                equal(target["config"][field], source["config"][field], "Rebuild relaxed frozen policy: " + field)
            for field in ("inputs", "acceptance"):
                equal(
                    target["config"]["workflow"][field],
                    source["config"]["workflow"][field],
                    "Rebuild changed task: " + field,
                )
            archived = single("archived", "definition", rebuild["source"])
            equal(archived["data"]["replacement"], target_key, "Archive has wrong replacement")
            before(persisted, archived)
            following = [
                row
                for row in transitions
                if row["kind"] == "event_admitted"
                and row["data"]["admission"]["workflow_ref"] == target["workflow_ref"]
                and row["data"]["admission"]["kind"] == "Callback"
            ]
            require(len(following) == 1, "Fork needs exactly one new Callback test")
            before(archived, following[0])
        else:
            require(rebuild["state"] == "unknown", "Unfinished rebuild is not final evidence")
            require(snapshot["families"][rebuild["target"]["id"]]["suspended"], "Unknown rebuild did not suspend")

    for family_id, family in snapshot["families"].items():
        owned_rebuilds = [item for item in snapshot["rebuilds"].values() if item["target"]["id"] == family_id]
        equal(
            sorted(item["number"] for item in owned_rebuilds),
            list(range(1, len(owned_rebuilds) + 1)),
            "Duplicate or skipped rebuild attempt",
        )
        equal(family["rebuild_count"], len(owned_rebuilds), "Rebuild counter changed")
        if family["suspended"]:
            require(family["active"] is None and family["pending"] is None, "Suspended lifecycle retained Cron release")
            require(
                any(item["family"] == family_id and item["state"] == "pending" for item in snapshot["adhoc"]),
                "Suspension has no Adhoc work item",
            )
        elif family["active"] is not None:
            require(family["active"] in passed_tests, "Active revision was never independently accepted")
            equal(definitions[family["active"]]["status"], "released", "Active definition is not released")
            releases = [
                row
                for row in transitions
                if row["kind"] in {"released", "replacement_activated"}
                and definitions[row["data"]["definition"]]["workflow_ref"]["id"] == family_id
            ]
            require(bool(releases), "Active revision has no release transaction")
            equal(family["active"], releases[-1]["data"]["definition"], "Cron retarget differs from latest release")
        require(family["cron_event"] is None, "A Cron run remains in flight")
        require(family["pending"] is None, "A replacement remains pending")
    statuses = {}
    for row in transitions:
        kind, data = row["kind"], row["data"]
        if kind in {"registered", "fork_persisted", "restored"}:
            statuses[data["definition"]] = "draft"
        elif kind == "test_result":
            statuses[data["definition"]] = "tested" if data["decision"]["passed"] else "rejected"
        elif kind in {"released", "replacement_activated"}:
            statuses[data["definition"]] = "released"
        elif kind == "archived":
            statuses[data["definition"]] = "archived"
    equal(
        {key: definition["status"] for key, definition in definitions.items()},
        statuses,
        "Definition status differs from lifecycle transitions",
    )
    return {
        "passed": True,
        "native_executions": executions,
        "calls": calls,
        "wall_clock_cron_verified": require_wall_clock
        and any(events[row["event"]]["admission"]["kind"] == "Cron" for row in executions),
        "model_authorship_verified": False,
    }
