"""Bind independent business roles to submitted logical occurrences by lineage."""

from itertools import product
import json
from typing import TYPE_CHECKING, Any, Literal, NotRequired, TypedDict

if TYPE_CHECKING or __package__:
    from .contracts import Rejected, require
else:
    from contracts import Rejected, require

# The closed sapi-lab/v0 step vocabulary, restated so the verifier imports no profile code.
StepKind = Literal["Script", "LLM"]


class Role(TypedDict, total=False):
    operation: str
    kind: StepKind
    inputs: dict[str, Any]
    when: dict[str, Any]
    actor: str


class RoleContract(TypedDict):
    """A scenario's benchmarks/NN-name/evaluation/contract.json; roles are named, step IDs are the author's."""

    roles: dict[str, Role]
    edges: list[list[str]]
    output: dict[str, Any]
    exclusive_actors: NotRequired[list[str]]
    outputs: dict[str, list[str]]


# Binding failures in check order; when every assignment fails, the one that got furthest explains why.
CHECKS = (
    "unbound_reference",
    "duplicate_dependency",
    "wrong_role_dependencies",
    "wrong_role_input_lineage",
    "wrong_role_guard",
    "missing_join",
    "wrong_role_actor",
    "actor_disallows_operation",
    "actor_not_exclusive",
    "wrong_role_output_lineage",
)


def whole_results(value: Any, uses: dict[str, str], outputs: dict) -> Any:
    """Read an object that copies every declared field of one step's result as `{ref: steps.ID}`.

    Equivalent only for `ref`: a skipped `optional_ref` producer yields null, not an object of nulls.
    """
    if isinstance(value, list):
        return [whole_results(item, uses, outputs) for item in value]
    if not isinstance(value, dict) or "ref" in value or "optional_ref" in value:
        return value
    value = {key: whole_results(item, uses, outputs) for key, item in value.items()}
    for sid, operation in uses.items():
        fields = outputs.get(operation, ())
        if fields and value == {field: {"ref": f"steps.{sid}.{field}"} for field in fields}:
            return {"ref": "steps." + sid}
    return value


def role_references(value: Any, bindings: dict[str, str]) -> Any:
    """Replace only reference step IDs, never literals or object field names."""
    if isinstance(value, list):
        return [role_references(item, bindings) for item in value]
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            if key in {"ref", "optional_ref"} and isinstance(item, str) and item.startswith("steps."):
                parts = item.split(".")
                require(parts[1] in bindings, "Reference names an unbound occurrence: " + item, "unbound_reference")
                parts[1] = bindings[parts[1]]
                result[key] = ".".join(parts)
            else:
                result[key] = role_references(item, bindings)
        return result
    return value


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _same(actual: Any, expected: Any, code: str, subject: str) -> None:
    # Canonical JSON keeps the type distinctions `equal` makes (true is not 1, 1 is not 1.0).
    require(
        _json(actual) == _json(expected),
        f"{subject}: submitted {_json(actual)}, contract requires {_json(expected)}",
        code,
    )


def check_assignment(contract: RoleContract, config: dict, binding: dict[str, str]) -> None:
    """Every lineage obligation of one role -> step assignment; raises the first that fails."""
    workflow = config["workflow"]
    steps = {step["id"]: step for step in workflow["steps"]}
    uses = {sid: step["uses"] for sid, step in steps.items()}
    reverse = {sid: role for role, sid in binding.items()}

    def in_roles(value: Any) -> Any:
        return role_references(whole_results(value, uses, contract["outputs"]), reverse)

    def arguments(step: dict) -> Any:
        # `with` is keyed by input names; only its values are workflow values.
        given = step.get("with")
        return {key: in_roles(item) for key, item in given.items()} if isinstance(given, dict) else given

    edges = [(reverse[first], reverse[second]) for first, second in workflow["dependencies"]]
    require(len(edges) == len(set(edges)), "Duplicate submitted dependency", "duplicate_dependency")
    required = {tuple(edge) for edge in contract["edges"]}
    missing, extra = sorted(required - set(edges)), sorted(set(edges) - required)
    require(
        not missing and not extra,
        f"Role dependencies differ: missing {missing}, unexpected {extra}",
        "wrong_role_dependencies",
    )
    actors = config.get("actors", {})
    for role, expected in contract["roles"].items():
        step = steps[binding[role]]
        where = f"step {step['id']} ({role})"
        _same(arguments(step), expected["inputs"], "wrong_role_input_lineage", where + " inputs")
        _same(in_roles(step.get("when")), expected.get("when"), "wrong_role_guard", where + " guard")
        if sum(second == role for _, second in edges) > 1:
            require(
                step.get("join") == "all_terminal",
                where + " has several predecessors and no all_terminal join",
                "missing_join",
            )
        if "actor" in expected:
            require(
                step.get("actor") == expected["actor"],
                f"{where} is assigned actor {step.get('actor')!r} but the contract requires {expected['actor']!r}",
                "wrong_role_actor",
            )
        if step.get("actor"):
            allowed = actors.get(step["actor"], {}).get("allowed_operations", [])
            require(
                step["uses"] in allowed,
                f"{where}: actor {step['actor']!r} does not allow {step['uses']}",
                "actor_disallows_operation",
            )
    exclusive = []
    for role in contract.get("exclusive_actors", []):
        step = steps[binding[role]]
        actor = step.get("actor")
        require(
            isinstance(actor, str) and actor and actors.get(actor, {}).get("allowed_operations") == [step["uses"]],
            f"step {step['id']} ({role}) needs an actor that allows only {step['uses']}; it has {actor!r}",
            "actor_not_exclusive",
        )
        exclusive.append(actor)
    require(len(exclusive) == len(set(exclusive)), "Exclusive roles share an actor", "actor_not_exclusive")
    _same(in_roles(workflow["output"]), contract["output"], "wrong_role_output_lineage", "workflow output")


def bind_roles(scenario: str, config: dict, contract: RoleContract) -> dict[str, str]:
    """Fail closed unless exactly one graph/input-origin assignment satisfies the task."""
    require(isinstance(contract.get("outputs"), dict), "Explicit operation output fields required")
    workflow = config["workflow"]
    require(workflow.get("id") == scenario, "Wrong submitted workflow identity", "wrong_workflow_identity")
    steps = workflow["steps"]
    require(
        len(steps) == len(contract["roles"]),
        f"Submitted {len(steps)} occurrences; the contract has {len(contract['roles'])} roles",
        "wrong_occurrence_count",
    )
    require(len({step["id"] for step in steps}) == len(steps), "Duplicate submitted occurrence", "duplicate_occurrence")
    roles = list(contract["roles"])
    candidates = []
    for role in roles:
        operation, kind = contract["roles"][role]["operation"], contract["roles"][role]["kind"]
        matching = [step for step in steps if step.get("uses") == operation and step.get("kind") == kind]
        require(matching, f"No submitted {kind} step uses {operation} for role {role}", "missing_role_operation")
        candidates.append(matching)
    matches: list[dict[str, str]] = []
    furthest: Rejected | None = None
    for assignment in product(*candidates):
        binding = {role: step["id"] for role, step in zip(roles, assignment, strict=True)}
        if len(set(binding.values())) != len(roles):
            continue
        try:
            check_assignment(contract, config, binding)
        except Rejected as error:
            if furthest is None or CHECKS.index(error.code or CHECKS[0]) > CHECKS.index(furthest.code or CHECKS[0]):
                furthest = error
            continue
        except (KeyError, TypeError, ValueError) as error:
            if furthest is None:
                furthest = Rejected(f"Malformed submitted graph: {type(error).__name__}: {error}", "malformed_graph")
            continue
        matches.append(binding)
        if len(matches) > 1:
            raise Rejected(
                f"Ambiguous role binding: {matches[0]} and {matches[1]} both satisfy the contract",
                "ambiguous_role_binding",
            )
    if not matches:
        raise furthest or Rejected("No assignment of distinct steps to roles exists", "missing_role_operation")
    return matches[0]


def resolve(value: Any, inputs: dict, steps: dict, statuses: dict) -> Any:
    """Independent reference interpretation for comparing observed arguments/results."""
    if isinstance(value, list):
        return [resolve(item, inputs, steps, statuses) for item in value]
    if isinstance(value, dict):
        reference = value.get("ref", value.get("optional_ref"))
        if reference is not None:
            parts = reference.split(".")
            if "optional_ref" in value and parts[0] == "steps" and statuses.get(parts[1]) == "skipped":
                return None
            current = {"inputs": inputs, "steps": steps}
            for part in parts:
                require(isinstance(current, dict) and part in current, "Missing reference evidence: " + reference)
                current = current[part]
            return current
        return {key: resolve(item, inputs, steps, statuses) for key, item in value.items()}
    return value
