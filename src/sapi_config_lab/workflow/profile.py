"""Small closed-profile feasibility prototype, not a production SAPi runtime."""

from __future__ import annotations
import math
import re
from collections.abc import Iterator, Sequence
from typing import Any, cast
from pathlib import Path
import yaml

SPEC = "06ddd3333109cea8a2cb3071609070d7a3c0d3ff"
ID = re.compile(r"^[a-z][a-z0-9_-]*$")


class Invalid(ValueError):
    pass


class Unsupported(ValueError):
    pass


def check(ok: object, message: str) -> None:
    if not ok:
        raise Invalid(message)


class UniqueLoader(yaml.SafeLoader):
    pass


def unique_mapping(loader: UniqueLoader, node: yaml.MappingNode, deep: bool = False) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        location = f"line {key_node.start_mark.line + 1}, column {key_node.start_mark.column + 1}"
        check(isinstance(key, str), f"YAML mapping key at {location}: must be a string")
        check(key not in result, f"Duplicate YAML key: {key} ({location})")
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, unique_mapping)


def read(path: str | Path) -> Any:
    try:
        return yaml.load(Path(path).read_text(), Loader=UniqueLoader)
    except yaml.YAMLError as error:
        mark = getattr(error, "problem_mark", None)
        location = f" at line {mark.line + 1}, column {mark.column + 1}" if mark else ""
        raise Invalid(f"{path}: invalid YAML{location}: {getattr(error, 'problem', str(error))}") from error


def mapping(value: object, label: str) -> dict[str, Any]:
    check(isinstance(value, dict), f"{label}: must be a mapping")
    result = cast(dict[str, Any], value)
    check(all(isinstance(key, str) for key in result), f"{label}: field names must be strings")
    return result


def keys(value: object, required: Sequence[str], optional: Sequence[str], label: str) -> dict[str, Any]:
    result = mapping(value, label)
    check(set(required) <= result.keys(), f"{label}: missing fields {sorted(set(required) - result.keys())}")
    check(
        result.keys() <= set(required) | set(optional),
        f"{label}: unknown fields {sorted(result.keys() - set(required) - set(optional))}",
    )
    return result


def string(value: object, label: str) -> str:
    check(isinstance(value, str) and bool(value), f"{label}: must be a nonempty string")
    return cast(str, value)


def string_list(value: object, label: str) -> list[str]:
    check(isinstance(value, list), f"{label}: must be a list of strings")
    result = cast(list[str], value)
    for index, item in enumerate(result):
        string(item, f"{label}[{index}]")
    check(len(set(result)) == len(result), f"{label}: duplicate entries")
    return result


def json_value(value: object, label: str, parents: frozenset[int] = frozenset()) -> None:
    """YAML inputs must be finite JSON trees, including mappings used as literals."""
    if isinstance(value, (dict, list)):
        check(id(value) not in parents, f"{label}: recursive YAML aliases are not supported")
        ancestors = parents | {id(value)}
        if isinstance(value, dict):
            for key, child in mapping(value, label).items():
                json_value(child, f"{label}.{key}", ancestors)
        else:
            for index, child in enumerate(value):
                json_value(child, f"{label}[{index}]", ancestors)
    else:
        check(type(value) in (str, int, float, bool, type(None)), f"{label}: must be a JSON value")
        if isinstance(value, float):
            check(math.isfinite(value), f"{label}: number must be finite")


def references(value: Any, label: str = "value") -> Iterator[tuple[str, bool, str]]:
    if isinstance(value, dict):
        check(not ("ref" in value and "optional_ref" in value), f"{label}: ref and optional_ref are mutually exclusive")
        for key in ("ref", "optional_ref"):
            if key in value:
                yield string(value[key], f"{label}.{key}"), key == "optional_ref", f"{label}.{key}"
        for key, child in value.items():
            if key not in ("ref", "optional_ref"):
                yield from references(child, f"{label}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from references(child, f"{label}[{index}]")


def validate_schema(schema: Any, label: str) -> None:
    """Validate the supported JSON Schema subset instead of ignoring keywords."""
    allowed = {
        "type",
        "properties",
        "required",
        "additionalProperties",
        "enum",
        "items",
        "minLength",
        "minItems",
        "maxItems",
        "minimum",
    }
    schema = mapping(schema, label)
    check("type" in schema, f"{label}.type: schema needs type")
    check(schema.keys() <= allowed, f"{label}: unsupported schema keywords {schema.keys() - allowed}")
    kinds = schema["type"] if isinstance(schema["type"], list) else [schema["type"]]
    check(
        kinds
        and all(
            isinstance(t, str) and t in ("object", "array", "string", "integer", "number", "boolean", "null")
            for t in kinds
        ),
        f"{label}.type: invalid schema type",
    )
    applicable = {
        "properties": {"object"},
        "required": {"object"},
        "additionalProperties": {"object"},
        "items": {"array"},
        "minItems": {"array"},
        "maxItems": {"array"},
        "minLength": {"string"},
        "minimum": {"integer", "number"},
    }
    for keyword, target_types in applicable.items():
        check(
            keyword not in schema or bool(set(kinds) & target_types),
            f"{label}: {keyword} does not apply to schema type",
        )
    if "object" in kinds:
        check(isinstance(schema.get("properties"), dict), f"{label}: object properties required")
        required = string_list(schema.get("required"), f"{label}.required")
        check(
            set(required) <= schema["properties"].keys(),
            f"{label}: invalid required properties",
        )
        check(schema.get("additionalProperties") is False, f"{label}: closed objects required")
        for name, child in schema["properties"].items():
            validate_schema(child, f"{label}.properties.{name}")
    if "array" in kinds:
        check("items" in schema, f"{label}: array items required")
        validate_schema(schema["items"], f"{label}.items")
    if "enum" in schema:
        check(isinstance(schema["enum"], list) and schema["enum"], f"{label}: invalid enum")
    for field in ("minLength", "minItems", "maxItems"):
        if field in schema:
            check(type(schema[field]) is int and schema[field] >= 0, f"{label}: invalid {field}")
    if "minimum" in schema:
        check(
            type(schema["minimum"]) in (int, float) and math.isfinite(schema["minimum"]),
            f"{label}.minimum: invalid minimum",
        )
    if "minItems" in schema and "maxItems" in schema:
        check(schema["minItems"] <= schema["maxItems"], f"{label}: contradictory array length bounds")


def validate_bindings(value: object) -> dict[str, Any]:
    bindings = mapping(value, "bindings.operations")
    for name, binding in bindings.items():
        label = f"bindings.operations.{name}"
        op = keys(
            binding,
            ["kind", "inputs", "outputs", "implementation"],
            ["prompt", "output_contract", "input_schema", "output_schema", "transport", "max_attempts"],
            label,
        )
        check(op["kind"] in ("Script", "LLM"), f"{label}.kind: unknown operation kind")
        string(op["implementation"], f"{label}.implementation")
        if "transport" in op:
            check(op["transport"] == "http" and op["kind"] == "Script", f"{label}: invalid transport")
            check(op["outputs"] == ["result_json"], f"{label}: HTTP tools return result_json")
        if "max_attempts" in op:
            check(
                op.get("transport") == "http" and type(op["max_attempts"]) is int and 1 <= op["max_attempts"] <= 3,
                f"{label}: invalid bounded tool attempts",
            )
        for field in ("prompt", "output_contract"):
            if field in op:
                string(op[field], f"{label}.{field}")
        for direction in ("input", "output"):
            fields = string_list(op[direction + "s"], f"{label}.{direction}s")
            schema = op.get(direction + "_schema")
            check(
                (op["kind"] != "LLM" and "transport" not in op) or schema is not None,
                f"{label}: LLM {direction}_schema required",
            )
            if schema is not None:
                validate_schema(schema, f"{label}.{direction}_schema")
                check(
                    schema["type"] == "object"
                    and set(schema["properties"]) == set(fields)
                    and set(schema["required"]) == set(fields),
                    f"{label}.{direction}_schema: schema fields differ from binding",
                )
    return bindings


def read_bindings(path: str | Path) -> dict[str, Any]:
    document = mapping(read(path), "bindings")
    check("operations" in document, "bindings.operations: missing field")
    json_value(document, "bindings")
    return validate_bindings(document["operations"])


def validate(cfg: object, bindings: object) -> tuple[list[str], dict[str, list[str]]]:
    cfg = keys(
        cfg, ["schema", "spec_revision", "workflow", "activation", "execution"], ["actors", "lifecycle"], "config"
    )
    json_value(cfg, "config")
    json_value(bindings, "bindings.operations")
    bindings = validate_bindings(bindings)
    check(
        cfg["schema"] == "sapi-lab/v0" and cfg["spec_revision"] == SPEC,
        "config.schema/spec_revision: Unsupported schema/spec revision",
    )
    w = keys(
        cfg["workflow"],
        ["id", "revision", "kind", "inputs", "steps", "dependencies", "acceptance", "output"],
        [],
        "workflow",
    )
    check(ID.fullmatch(string(w["id"], "workflow.id")), "workflow.id: Invalid workflow identity")
    check(type(w["revision"]) is int and w["revision"] > 0, "workflow.revision: Invalid workflow identity")
    check(w["kind"] in ("Pipeline", "Gantt"), "workflow.kind: Unknown workflow kind")
    mapping(w["inputs"], "workflow.inputs")
    string(w["acceptance"], "workflow.acceptance")
    check(isinstance(w["steps"], list) and w["steps"], "workflow.steps: Steps must be nonempty list")
    check(isinstance(w["dependencies"], list), "workflow.dependencies: must be a list")

    execution = keys(
        cfg["execution"], ["concurrency", "deadline_seconds", "on_step_error"], ["refinement"], "execution"
    )
    check(
        execution["concurrency"] in ("independent", "required_parallel"), "execution.concurrency: Unknown concurrency"
    )
    check(
        type(execution["deadline_seconds"]) is int and execution["deadline_seconds"] > 0,
        "execution.deadline_seconds: Invalid timeout",
    )
    check(execution["on_step_error"] == "fail", "execution.on_step_error: Only fail-fast is defined in v0")
    refinement = None
    if "refinement" in execution:
        refinement = keys(
            execution["refinement"],
            ["region", "initial_state", "until", "max_attempts", "carry", "exhausted", "output_policy"],
            [],
            "execution.refinement",
        )
        string_list(refinement["region"], "execution.refinement.region")
        mapping(refinement["initial_state"], "execution.refinement.initial_state")
        mapping(refinement["carry"], "execution.refinement.carry")
        keys(refinement["until"], ["ref", "eq"], [], "execution.refinement.until")
        check(
            type(refinement["max_attempts"]) is int and 1 <= refinement["max_attempts"] <= 10,
            "execution.refinement.max_attempts: Invalid refinement limit",
        )
        check(
            refinement["exhausted"] == "failed" and refinement["output_policy"] == "last_accepted_only",
            "execution.refinement: Invalid refinement outcome",
        )
        check(
            set(refinement["initial_state"]) == set(refinement["carry"]),
            "execution.refinement.carry: Carry fields must match initial state",
        )

    actors = mapping(cfg.get("actors", {}), "actors")
    for name, value in actors.items():
        actor = keys(value, ["context_scope", "allowed_operations"], [], f"actors.{name}")
        string(actor["context_scope"], f"actors.{name}.context_scope")
        allowed = string_list(actor["allowed_operations"], f"actors.{name}.allowed_operations")
        check(set(allowed) <= bindings.keys(), f"actors.{name}.allowed_operations: unknown operation")

    steps: dict[str, Any] = {}
    for index, value in enumerate(w["steps"]):
        label = f"workflow.steps[{index}]"
        step = keys(value, ["id", "kind", "uses", "with"], ["when", "join", "actor"], label)
        sid = string(step["id"], f"{label}.id")
        check(ID.fullmatch(sid) and sid not in steps, f"{label}.id: Invalid or duplicate step ID")
        operation = string(step["uses"], f"{label}.uses")
        check(operation in bindings, f"{label}.uses: Unknown operation: {operation}")
        op = bindings[operation]
        check(step["kind"] == op["kind"], f"{label}.kind: operation kind mismatch")
        check(w["kind"] != "Pipeline" or step["kind"] == "Script", f"{label}.kind: Pipeline cannot include LLM steps")
        arguments = mapping(step["with"], f"{label}.with")
        check(set(arguments) == set(op["inputs"]), f"{label}.with: incorrect operation inputs")
        if "when" in step:
            keys(step["when"], ["ref", "eq"], [], f"{label}.when")
        check("join" not in step or step["join"] == "all_terminal", f"{label}.join: Unsupported join policy")
        if "actor" in step:
            actor_name = string(step["actor"], f"{label}.actor")
            selected_actor = actors.get(actor_name)
            check(
                selected_actor is not None and operation in selected_actor["allowed_operations"],
                f"{label}.actor: unknown/unauthorized actor binding",
            )
        steps[sid] = step

    deps: dict[str, list[str]] = {sid: [] for sid in steps}
    for index, edge in enumerate(w["dependencies"]):
        label = f"workflow.dependencies[{index}]"
        check(isinstance(edge, list) and len(edge) == 2, f"{label}: Dependency must be [upstream, downstream]")
        a, b = (string(endpoint, f"{label}[{i}]") for i, endpoint in enumerate(edge))
        check(a in steps and b in steps and a != b, f"{label}: Bad dependency endpoint")
        check(a not in deps[b], f"{label}: Duplicate dependency")
        deps[b].append(a)
    order: list[str] = []
    remaining = set(steps)
    while remaining:
        ready = [sid for sid in steps if sid in remaining and set(deps[sid]) <= set(order)]
        check(ready, "workflow.dependencies: Cyclic dependencies: sapi-lab/v0 deliberately supports DAGs only")
        order.extend(ready)
        remaining.difference_update(ready)
    ancestors: dict[str, set[str]] = {}
    for sid in order:
        ancestors[sid] = set(deps[sid])
        for pred in deps[sid]:
            ancestors[sid] |= ancestors[pred]
        check(
            len(deps[sid]) < 2 or steps[sid].get("join") == "all_terminal",
            f"workflow.steps.{sid}.join: explicit join required",
        )

    def check_refs(value: Any, label: str, sid: str | None = None) -> None:
        for ref, optional, location in references(value, label):
            parts = ref.split(".")
            check(len(parts) >= 2 and all(parts), f"{location}: Invalid reference: {ref}")
            if parts[0] == "inputs":
                item = w["inputs"]
                for part in parts[1:]:
                    check(isinstance(item, dict) and part in item, f"{location}: Missing input: {ref}")
                    item = item[part]
            elif parts[0] == "steps":
                producer = parts[1]
                check(producer in steps, f"{location}: Missing producer: {ref}")
                check(
                    sid is None or producer in ancestors[sid],
                    f"{location}: producer is not an upstream dependency: {ref}",
                )
                check(
                    optional or "when" not in steps[producer],
                    f"{location}: conditional output requires optional_ref: {ref}",
                )
                if len(parts) > 2:
                    check(
                        parts[2] in bindings[steps[producer]["uses"]]["outputs"],
                        f"{location}: Unknown output field: {ref}",
                    )
            elif parts[0] == "runtime":
                initial = refinement["initial_state"] if refinement else {}
                check(parts[1] in initial, f"{location}: Unknown runtime field: {ref}")
            else:
                raise Invalid(f"{location}: Unknown reference root: {ref}")

    for index, step in enumerate(w["steps"]):
        check_refs(step["with"], f"workflow.steps[{index}].with", step["id"])
        if "when" in step:
            check_refs(step["when"], f"workflow.steps[{index}].when", step["id"])
    check_refs(w["output"], "workflow.output")
    if refinement:
        check(
            set(refinement["region"]) == set(steps),
            "execution.refinement.region: Lab refinement must cover the complete graph",
        )
        check_refs(refinement["until"], "execution.refinement.until")
        check_refs(refinement["carry"], "execution.refinement.carry")

    activation = mapping(cfg["activation"], "activation")
    check(activation.get("kind") in ("Cron", "Callback"), "activation.kind: Invalid workflow initiator")
    common = ["kind", "rule_id", "workflow_ref"]
    fields = (
        ["hook", "condition", "reaction"] if activation["kind"] == "Callback" else ["schedule", "timezone", "enabled"]
    )
    keys(activation, common + fields, [], "activation")
    string(activation["rule_id"], "activation.rule_id")
    workflow_ref = keys(activation["workflow_ref"], ["id", "revision"], [], "activation.workflow_ref")
    string(workflow_ref["id"], "activation.workflow_ref.id")
    check(
        type(workflow_ref["revision"]) is int and workflow_ref["revision"] > 0,
        "activation.workflow_ref.revision: must be a positive integer",
    )
    check(
        workflow_ref == {"id": w["id"], "revision": w["revision"]},
        "activation.workflow_ref: Activation points to another revision",
    )
    if activation["kind"] == "Callback":
        string(activation["hook"], "activation.hook")
        check(
            activation["reaction"] == "Trigger" and activation["condition"] == "valid_input",
            "activation: Unsupported callback reaction",
        )
    else:
        string(activation["schedule"], "activation.schedule")
        string(activation["timezone"], "activation.timezone")
        check(activation["enabled"] is False, "activation.enabled: Lab Cron must be disabled")
    if "lifecycle" in cfg:
        lifecycle = keys(
            cfg["lifecycle"],
            ["initial_state", "test", "on_test_pass", "on_test_fail", "persistence", "scheduling", "replacement"],
            [],
            "lifecycle",
        )
        check(
            lifecycle["initial_state"] == "draft" and activation["kind"] == "Cron",
            "lifecycle.initial_state: Invalid lifecycle activation",
        )
        fields_by_section = {
            "test": ["initiator", "rule_id", "hook", "condition", "reaction", "verifier", "output_mode"],
            "on_test_pass": ["action", "enable_cron", "retarget"],
            "on_test_fail": [
                "action",
                "objective",
                "source_work",
                "output",
                "max_rebuilds",
                "archive_original",
                "next",
                "exhausted",
            ],
            "persistence": ["store", "immutable_definitions", "record_test_results", "deduplicate_by"],
            "scheduling": ["overlap", "missed_run", "suspended"],
            "replacement": ["in_flight", "activate_new_revision", "restore_archive"],
        }
        for section, fields in fields_by_section.items():
            nested = keys(lifecycle[section], fields, [], f"lifecycle.{section}")
            for field, value in nested.items():
                label = f"lifecycle.{section}.{field}"
                if field in ("immutable_definitions", "record_test_results"):
                    check(type(value) is bool, f"{label}: must be a boolean")
                elif field == "deduplicate_by":
                    string_list(value, label)
                elif field == "max_rebuilds":
                    check(type(value) is int and value >= 0, f"{label}: Unbounded rebuild")
                else:
                    string(value, label)
        check(
            lifecycle["on_test_pass"]["enable_cron"] == activation["rule_id"],
            "lifecycle.on_test_pass.enable_cron: Unknown Cron for release",
        )
        check(lifecycle["test"]["initiator"] == "Callback", "lifecycle.test.initiator: Tests need Callback initiation")
    return order, deps
