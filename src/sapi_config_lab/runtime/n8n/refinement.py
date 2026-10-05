"""Lower a bounded whole-DAG refinement into one ordinary native n8n graph."""

from __future__ import annotations

import copy
import json
import uuid

from sapi_config_lab.runtime.contracts import Document, LlmMode
from sapi_config_lab.runtime.n8n.compiler import RESOURCES, compile_n8n
from sapi_config_lab.workflow.profile import SPEC


def compile_refinement(
    config: Document,
    bindings: Document,
    mode: LlmMode,
    bridge_url: str | None,
    timeout: int,
    admission: Document | None = None,
    deadline_at: float | None = None,
) -> tuple[Document, dict[str, str]]:
    """Unroll attempts, retaining native early exit and a single accepted result.

    Each copy keeps logical step IDs inside its envelope. Native names and live
    invocation IDs identify the attempt independently. No step in a later copy
    receives a token after an earlier accepted checkpoint.
    """
    workflow = config["workflow"]
    policy = config["execution"]["refinement"]
    maximum = policy["max_attempts"]
    helpers = (RESOURCES / "operations.js").read_text() + "\n" + (RESOURCES / "runtime-fragment.js").read_text()
    artifact: Document = {"nodes": [], "connections": {}}
    mapping: dict[str, str] = {}

    def connect(source: str, target: str, output: int = 0, port: int = 0) -> None:
        outputs = artifact["connections"].setdefault(source, {"main": [[]]})["main"]
        while len(outputs) <= output:
            outputs.append([])
        outputs[output].append({"node": target, "type": "main", "index": port})

    def code(name: str, body: str, x: int, y: int = 0) -> Document:
        return {
            "id": str(uuid.uuid5(uuid.NAMESPACE_URL, workflow["id"] + "/" + name)),
            "name": name,
            "type": "n8n-nodes-base.code",
            "typeVersion": 2,
            "parameters": {"mode": "runOnceForAllItems", "jsCode": helpers + "\n" + body},
            "position": [x, y],
        }

    for number in range(1, maximum + 1):
        graph, steps = compile_n8n(
            config,
            bindings,
            llm_mode=mode,
            bridge_url=bridge_url,
            request_timeout_seconds=timeout,
            _refinement_attempt=number,
            admission=admission,
            deadline_at=deadline_at,
        )
        names = {n["name"]: f"Attempt {number} / {n['name']}" for n in graph["nodes"]}
        names["Result"] = f"Checkpoint {number}"
        if number == 1:
            artifact.update({key: value for key, value in graph.items() if key not in ("nodes", "connections")})
            names["Demo start"] = "Demo start"
            names["Fixture"] = "Fixture"
        for item in graph["nodes"]:
            if number > 1 and item["name"] == "Demo start":
                continue
            item = copy.deepcopy(item)
            old_name = item["name"]
            name = names[old_name]
            item["name"] = name
            item["id"] = str(uuid.uuid5(uuid.NAMESPACE_URL, workflow["id"] + "/" + name))
            item["position"][1] += (number - 1) * 650
            if old_name == "Fixture":
                if number == 1:
                    item["parameters"]["jsCode"] = (
                        "const ctx = "
                        + json.dumps(
                            {
                                "inputs": workflow["inputs"],
                                "runtime": policy["initial_state"],
                                "steps": {},
                                "statuses": {},
                                "events": {},
                                "simulation": True,
                                "input_source": "fixture",
                                "llm_mode": mode,
                                "refinement": {"max_attempts": maximum, "attempts": [], "accepted_attempt": None},
                            }
                        )
                        + "; ctx.deadline_at_ms = Date.now() + "
                        + str(config["execution"]["deadline_seconds"] * 1000)
                        + "; return [{json:ctx}];"
                    )
                    if admission is not None:
                        assert deadline_at is not None
                        item["parameters"]["jsCode"] = item["parameters"]["jsCode"].replace(
                            "; return [{json:ctx}];",
                            "; ctx.admission = "
                            + json.dumps(admission)
                            + "; ctx.simulation = false; ctx.input_source = 'event'; ctx.deadline_at_ms = Math.min(ctx.deadline_at_ms, "
                            + str(deadline_at * 1000)
                            + "); return [{json:ctx}];",
                        )
                else:
                    item["parameters"]["jsCode"] = (
                        helpers
                        + "\nconst ctx = mergeEnvelopes($input.all());\n"
                        + (
                            "checkDeadline(ctx); need(ctx.continue_refinement === true, 'Unexpected refinement attempt');\n"
                            "ctx.steps = {}; ctx.statuses = {}; ctx.events = {}; return [{json:ctx}];"
                        )
                    )
            elif old_name == "Result":
                body = "const ctx = mergeEnvelopes($input.all()); checkDeadline(ctx);\n"
                body += (
                    "const accepted = same(resolveRef("
                    + json.dumps(policy["until"]["ref"])
                    + ", ctx), "
                    + json.dumps(policy["until"]["eq"])
                    + ");\n"
                )
                body += (
                    "const attempt = {number:"
                    + str(number)
                    + ", runtime:clone(ctx.runtime), output:resolveValue("
                    + json.dumps(workflow["output"])
                    + ",ctx), steps:clone(ctx.steps), statuses:clone(ctx.statuses), trace:Object.values(ctx.events), accepted};\n"
                )
                body += (
                    "ctx.refinement.attempts.push(attempt); if (accepted) ctx.refinement.accepted_attempt = "
                    + str(number)
                    + ";\n"
                )
                body += "ctx.continue_refinement = !accepted && " + str(number) + " < " + str(maximum) + ";\n"
                body += (
                    "if (ctx.continue_refinement) ctx.runtime = resolveValue("
                    + json.dumps(policy["carry"])
                    + ",ctx);\nreturn [{json:ctx}];"
                )
                item = code(name, body, item["position"][0], item["position"][1])
            elif item["type"].endswith(".code"):
                source = item["parameters"]["jsCode"]
                # Restore's native node reference must name this attempt's Prepare.
                for before, after in names.items():
                    source = source.replace("$(" + json.dumps(before) + ")", "$(" + json.dumps(after) + ")")
                item["parameters"]["jsCode"] = source
            artifact["nodes"].append(item)
        for source, outputs in graph["connections"].items():
            if number > 1 and source == "Demo start":
                continue
            for output, edges in enumerate(outputs["main"]):
                for edge in edges:
                    connect(names[source], names[edge["node"]], output, edge["index"])
        mapping.update({f"attempt{number}:{step}": names[native] for step, native in steps.items()})
        checkpoint = names["Result"]
        if number < maximum:
            guard = f"Continue {number}"
            artifact["nodes"].append(
                {
                    "id": str(uuid.uuid5(uuid.NAMESPACE_URL, workflow["id"] + "/" + guard)),
                    "name": guard,
                    "type": "n8n-nodes-base.if",
                    "typeVersion": 2.2,
                    "position": [2300, (number - 1) * 650],
                    "parameters": {
                        "conditions": {
                            "options": {
                                "caseSensitive": True,
                                "leftValue": "",
                                "typeValidation": "strict",
                                "version": 2,
                            },
                            "conditions": [
                                {
                                    "id": f"continue-{number}",
                                    "leftValue": "={{ $json.continue_refinement }}",
                                    "rightValue": "",
                                    "operator": {"type": "boolean", "operation": "true", "singleValue": True},
                                }
                            ],
                            "combinator": "and",
                        },
                        "options": {},
                    },
                }
            )
            connect(checkpoint, guard)
            connect(guard, f"Attempt {number + 1} / Fixture")
            connect(guard, "Result", 1)
        else:
            connect(checkpoint, "Result")
    metadata = {
        "simulation": True,
        "input_source": "fixture",
        "llm_mode": mode,
        "spec_revision": SPEC,
        "workflow_ref": {"id": workflow["id"], "revision": workflow["revision"]},
    }
    if admission is not None:
        metadata.update(simulation=False, input_source="event", admission=admission)
    body = "const ctx = mergeEnvelopes($input.all()); checkDeadline(ctx);\n"
    body += "need(ctx.refinement.accepted_attempt !== null, 'Refinement exhausted without an accepted result');\n"
    body += "const accepted = ctx.refinement.attempts[ctx.refinement.accepted_attempt - 1];\n"
    body += (
        "return [{json:{..."
        + json.dumps(metadata)
        + ", output:accepted.output, steps:accepted.steps, statuses:accepted.statuses, trace:accepted.trace, refinement:ctx.refinement}}];"
    )
    artifact["nodes"].append(code("Result", body, 2650))
    return artifact, mapping
