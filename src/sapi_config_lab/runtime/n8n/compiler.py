"""Compile the supported profile to n8n JSON; never execute workflows."""

import json
import math
import uuid
from pathlib import Path
from urllib.parse import urlparse
from sapi_config_lab.core.contracts import Document, LlmMode
from sapi_config_lab.core.profile import SPEC, Unsupported, check, validate

RESOURCES = Path(__file__).resolve().parent


def capability_errors(cfg: Document, *, admitted: bool = False) -> list[str]:
    reasons = []
    if cfg["execution"]["concurrency"] == "required_parallel":
        reasons.append("E_PARALLEL: actual overlap requires a dispatch/join runtime")
    if "lifecycle" in cfg and not admitted:
        reasons.append("E_LIFECYCLE: WBS/fork/archive/test/release need a persistent orchestrator")
    if cfg["activation"]["kind"] == "Cron" and not admitted:
        reasons.append("E_ACTIVATION: this demo compiler only injects Callback fixtures")
    return reasons


def compile_n8n(
    cfg: Document,
    bindings: Document,
    *,
    llm_mode: LlmMode = "stub",
    bridge_url: str | None = None,
    request_timeout_seconds: int = 190,
    _refinement_attempt: int | None = None,
    admission: Document | None = None,
    deadline_at: float | None = None,
    operation_url: str | None = None,
    operation_token: str | None = None,
) -> tuple[Document, dict[str, str]]:
    """Compile the closed DAG profile to n8n; compilation does not execute it.

    bridge_url is a deployment base URL or the full /v1/agency/execute URL.
    Live mode retains fixture activation; workflow deadlines remain explicit in
    cfg and each HTTP timeout is capped at that deadline. No automatic retries.
    """
    check(llm_mode in ("stub", "live"), "llm_mode must be stub or live")
    check(type(request_timeout_seconds) is int and request_timeout_seconds > 0, "Invalid HTTP timeout")
    if llm_mode == "live":
        parsed = urlparse(bridge_url or "")
        check(
            parsed.scheme in ("http", "https")
            and parsed.netloc
            and not parsed.username
            and not parsed.password
            and not parsed.query
            and not parsed.fragment,
            "Live mode needs a bridge_url without credentials or query parameters",
        )
        check(
            parsed.path.rstrip("/") in ("", "/v1/agency/execute"),
            "bridge_url must be a base URL or /v1/agency/execute endpoint",
        )
        endpoint = (bridge_url or "").rstrip("/")
        if not parsed.path.rstrip("/"):
            endpoint += "/v1/agency/execute"
    order, deps = validate(cfg, bindings)
    if admission is not None:
        check("lifecycle" in cfg, "Event admission requires a lifecycle definition")
        check(set(admission) == {"kind", "rule_id", "event_id", "workflow_ref", "purpose"}, "Invalid admission fields")
        check(admission["workflow_ref"] == cfg["activation"]["workflow_ref"], "Admission revision differs")
        check(isinstance(admission["event_id"], str) and bool(admission["event_id"]), "Invalid event ID")
        rule = cfg["lifecycle"]["test"]["rule_id"] if admission["kind"] == "Callback" else cfg["activation"]["rule_id"]
        purpose = "test" if admission["kind"] == "Callback" else "scheduled"
        check(
            admission["kind"] in ("Callback", "Cron")
            and admission["rule_id"] == rule
            and admission["purpose"] == purpose,
            "Invalid lifecycle admission",
        )
        check(deadline_at is not None and math.isfinite(deadline_at), "Lifecycle admission needs a deadline")
    reasons = capability_errors(cfg, admitted=admission is not None)
    if reasons:
        raise Unsupported("; ".join(reasons))
    used_bindings = [bindings[step["uses"]] for step in cfg["workflow"]["steps"]]
    if "refinement" in cfg["execution"] and any(x.get("transport") == "http" for x in used_bindings):
        raise Unsupported("HTTP tool operations are not supported inside refinement")
    if "refinement" in cfg["execution"] and _refinement_attempt is None:
        from sapi_config_lab.runtime.n8n.refinement import compile_refinement

        return compile_refinement(cfg, bindings, llm_mode, bridge_url, request_timeout_seconds, admission, deadline_at)
    if any(binding.get("transport") == "http" for binding in used_bindings):
        parsed_tool = urlparse(operation_url or "")
        check(
            parsed_tool.scheme in ("http", "https")
            and bool(parsed_tool.netloc)
            and not parsed_tool.username
            and not parsed_tool.password
            and not parsed_tool.query
            and not parsed_tool.fragment,
            "HTTP operations need a credential-free operation_url",
        )
        check(isinstance(operation_token, str) and bool(operation_token), "HTTP operations need an access token")
    w = cfg["workflow"]
    nodes: list[Document] = []
    connections: Document = {}
    mapping: dict[str, str] = {}
    ops = (RESOURCES / "operations.js").read_text()
    helpers = (RESOURCES / "runtime-fragment.js").read_text()
    stepmap = {s["id"]: s for s in w["steps"]}

    def node(name, kind, params, x, y=0, notes=""):
        versions = {"code": 2, "merge": 3.2, "manualTrigger": 1, "if": 2.2, "httpRequest": 4.2}
        nodes.append(
            {
                "id": str(uuid.uuid5(uuid.NAMESPACE_URL, w["id"] + "/" + name)),
                "name": name,
                "type": "n8n-nodes-base." + kind,
                "typeVersion": versions[kind],
                "parameters": params,
                "position": [x, y],
                "notes": notes,
            }
        )
        return name

    def code(name, source, x, y=0, notes=""):
        return node(name, "code", {"mode": "runOnceForAllItems", "jsCode": source}, x, y, notes)

    def connect(source, target, index=0, output=0):
        outputs = connections.setdefault(source, {"main": [[]]})["main"]
        while len(outputs) <= output:
            outputs.append([])
        outputs[output].append({"node": target, "type": "main", "index": index})

    node("Demo start", "manualTrigger", {}, 0, notes="SIMULATION: inject a Callback fixture; no real event admission.")
    initial = {
        "inputs": w["inputs"],
        "steps": {},
        "statuses": {},
        "events": {},
        "simulation": True,  # Compatibility marker: fixture activation, not simulated n8n.
        "input_source": "fixture",
        "llm_mode": llm_mode,
    }
    if deadline_at is not None:
        check(math.isfinite(deadline_at), "Invalid absolute deadline")
        initial["deadline_at_ms"] = deadline_at * 1000
    if admission is not None:
        assert deadline_at is not None
        initial.update(simulation=False, input_source="event", admission=admission, deadline_at_ms=deadline_at * 1000)
    code("Fixture", "return [{json: " + json.dumps(initial, ensure_ascii=False) + "}];", 220)
    connect("Demo start", "Fixture")
    levels: dict[str, int] = {}
    for sid in order:
        levels[sid] = 1 + max([levels[p] for p in deps[sid]] or [0])
    at_level: dict[int, int] = {}
    for sid in order:
        step = stepmap[sid]
        level = levels[sid]
        row = at_level.get(level, 0)
        at_level[level] = row + 1
        x, y = 250 + level * 460, row * 260
        binding = bindings[step["uses"]]
        step_json, binding_json = json.dumps(step, ensure_ascii=False), json.dumps(binding, ensure_ascii=False)
        tool_transport = binding.get("transport") == "http"
        if tool_transport or (step["kind"] == "LLM" and llm_mode == "live"):
            # Exclusive IF paths reconverge at Restore. The false path carries
            # the envelope and never reaches HTTP Request, even for a root step.
            name, guard = "Prepare " + sid, "Guard " + sid
            http = ("Tool " if tool_transport else "Agency ") + sid
            restored = sid + (" [HTTP]" if tool_transport else " [LLM LIVE]")
            prepare_function = "prepareTool" if tool_transport else "prepareAgency"
            restore_function = "restoreTool" if tool_transport else "restoreAgency"
            attempt_path = f"attempt{_refinement_attempt}/" if _refinement_attempt is not None else ""
            invocation_prefix = json.dumps(f"{w['id']}/r{w['revision']}/{sid}/{attempt_path}")
            source = ops + "\n" + helpers + "\nconst ctx = mergeEnvelopes($input.all());\n"
            source += f'return [{{json: {prepare_function}({step_json}, ctx, {binding_json}, {invocation_prefix} + String($workflow.id) + "/" + String($execution.id))}}];'
            code(name, source, x - 80, y)
            node(
                guard,
                "if",
                {
                    "conditions": {
                        "options": {"caseSensitive": True, "leftValue": "", "typeValidation": "strict", "version": 2},
                        "conditions": [
                            {
                                "id": str(uuid.uuid5(uuid.NAMESPACE_URL, w["id"] + "/guard/" + sid)),
                                "leftValue": "={{ $json.should_run }}",
                                "rightValue": "",
                                "operator": {"type": "boolean", "operation": "true", "singleValue": True},
                            }
                        ],
                        "combinator": "and",
                    },
                    "options": {},
                },
                x + 60,
                y,
            )
            node(
                http,
                "httpRequest",
                {
                    "method": "POST",
                    "url": operation_url if tool_transport else endpoint,
                    **(
                        {
                            "sendHeaders": True,
                            "headerParameters": {
                                "parameters": [
                                    {"name": "Authorization", "value": "={{ 'Bearer ' + $env.SAPI_OPERATION_TOKEN }}"}
                                ]
                            },
                        }
                        if operation_token is not None
                        else {}
                    ),
                    "sendBody": True,
                    "specifyBody": "json",
                    "jsonBody": "={{ JSON.stringify($json.request) }}",
                    "options": {
                        "timeout": (
                            "={{ Math.max(1, Math.min("
                            + str(min(request_timeout_seconds, 185) * 1000)
                            + ", $json.envelope.deadline_at_ms - Date.now())) }}"
                            if _refinement_attempt is not None or deadline_at is not None
                            else min(request_timeout_seconds, cfg["execution"]["deadline_seconds"]) * 1000
                        ),
                        "redirect": {"redirect": {"followRedirects": False}},
                        "response": {"response": {"responseFormat": "json", "neverError": False}},
                    },
                },
                x + 200,
                y - 70,
                "Live Agency transport. HTTP/status/schema errors fail the run; no retries.",
            )
            source = ops + "\n" + helpers + "\n"
            source += "need($input.all().length === 1, 'Agency must return exactly one response item');\n"
            source += f"const prepared = $({json.dumps(name)}).first().json;\n"
            source += (
                f"return [{{json: {restore_function}({step_json}, prepared, $input.first().json, {binding_json})}}];"
            )
            code(restored, source, x + 320, y)
            connect(name, guard)
            connect(guard, http)
            connect(guard, restored, output=1)
            connect(http, restored)
            mapping[sid] = restored
        else:
            name = sid + (" [LLM STUB]" if step["kind"] == "LLM" else "")
            source = ops + "\n" + helpers + "\nconst ctx = mergeEnvelopes($input.all());\n"
            source += "return [{json: applyStep(" + step_json + ", ctx, " + binding_json + ")}];"
            code(name, source, x, y, "Guarded operation may be skipped; the wrapper still returns one item.")
            mapping[sid] = name
        predecessors = [mapping[p] for p in deps[sid]] or ["Fixture"]
        if len(predecessors) > 1:
            merge = node("Join " + sid, "merge", {"mode": "append", "numberInputs": len(predecessors)}, x - 180, y)
            for i, predecessor in enumerate(predecessors):
                connect(predecessor, merge, i)
            connect(merge, name)
        else:
            connect(predecessors[0], name)
    terminal = [sid for sid in order if not any(sid in incoming for incoming in deps.values())]
    predecessors = [mapping[sid] for sid in terminal]
    x = 710 + max(levels.values()) * 460
    if len(predecessors) > 1:
        node("Join final", "merge", {"mode": "append", "numberInputs": len(predecessors)}, x - 180)
        for i, predecessor in enumerate(predecessors):
            connect(predecessor, "Join final", i)
        predecessors = ["Join final"]
    source = ops + "\n" + helpers + "\nconst ctx = mergeEnvelopes($input.all());\n"
    metadata = {
        "simulation": True,  # Compatibility marker: fixture activation, not simulated n8n.
        "input_source": "fixture",
        "llm_mode": llm_mode,
        "spec_revision": SPEC,
        "workflow_ref": {"id": w["id"], "revision": w["revision"]},
    }
    if admission is not None:
        metadata.update(simulation=False, input_source="event", admission=admission)
    source += (
        "return [{json: {..."
        + json.dumps(metadata)
        + ", output: resolveValue("
        + json.dumps(w["output"])
        + ", ctx), trace: Object.values(ctx.events), steps: ctx.steps, statuses: ctx.statuses}}];"
    )
    code("Result", source, x)
    for predecessor in predecessors:
        connect(predecessor, "Result")
    artifact = {
        "name": w["id"] + " — LAB " + llm_mode.upper(),
        "nodes": nodes,
        "connections": connections,
        "settings": {"executionOrder": "v1", "executionTimeout": cfg["execution"]["deadline_seconds"]},
        "active": False,
        "pinData": {},
        "tags": [],
    }
    return artifact, mapping


def compile_demo(cfg, bindings):
    """Backward-compatible stub compiler used by the narrow local JS tests."""
    return compile_n8n(cfg, bindings)
