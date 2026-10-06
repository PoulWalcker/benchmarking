"""Bind independent business roles to submitted logical occurrences by lineage."""

from itertools import product
from typing import Any, TYPE_CHECKING, TypedDict, NotRequired, cast

if TYPE_CHECKING or __package__:
    from .contracts import Rejected, equal, require
else:
    from contracts import Rejected, equal, require


class Role(TypedDict, total=False):
    operation: str
    kind: str
    inputs: dict[str, Any]
    when: dict[str, Any]
    actor: str


class RoleContract(TypedDict):
    roles: dict[str, Role]
    edges: list[tuple[str, str]]
    output: dict[str, Any]
    exclusive_actors: NotRequired[list[str]]


# The three original scenarios, whose records predate submitted-graph binding.
BASELINE_SCENARIOS = ("invoice-total", "ticket-routing", "competitor-report")


def contract_for(scenario: str) -> RoleContract:
    if TYPE_CHECKING or __package__:
        from .scenario_contracts import CONTRACTS
    else:
        from scenario_contracts import CONTRACTS
    require(scenario in CONTRACTS, "Unknown acceptance scenario")
    return cast(RoleContract, CONTRACTS[scenario])


def role_references(value: Any, bindings: dict[str, str]) -> Any:
    """Replace only reference step IDs, never literals or object field names."""
    if isinstance(value, list):
        return [role_references(item, bindings) for item in value]
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            if key in {"ref", "optional_ref"} and isinstance(item, str) and item.startswith("steps."):
                parts = item.split(".")
                require(parts[1] in bindings, "Reference names an unbound occurrence")
                parts[1] = bindings[parts[1]]
                result[key] = ".".join(parts)
            else:
                result[key] = role_references(item, bindings)
        return result
    return value


def bind_roles(scenario: str, config: dict) -> dict[str, str]:
    """Fail closed unless exactly one graph/input-origin assignment satisfies the task."""
    contract = contract_for(scenario)
    workflow = config["workflow"]
    require(workflow.get("id") == scenario, "Wrong submitted workflow identity")
    steps = workflow["steps"]
    require(len(steps) == len(contract["roles"]), "Wrong number of submitted occurrences")
    require(len({step["id"] for step in steps}) == len(steps), "Duplicate submitted occurrence")
    roles = list(contract["roles"])
    candidates = [
        [
            step
            for step in steps
            if step.get("uses") == contract["roles"][role]["operation"]
            and step.get("kind") == contract["roles"][role]["kind"]
        ]
        for role in roles
    ]
    matches = []
    for assignment in product(*candidates):
        binding = {role: step["id"] for role, step in zip(roles, assignment)}
        if len(set(binding.values())) != len(roles):
            continue
        reverse = {sid: role for role, sid in binding.items()}
        try:
            edges = [(reverse[first], reverse[second]) for first, second in workflow["dependencies"]]
            require(len(edges) == len(set(edges)), "Duplicate submitted dependency")
            require(
                set(edges) == {tuple(edge) for edge in contract["edges"]},
                "Submitted dependencies differ from role lineage",
            )
            for role, step in zip(roles, assignment):
                expected = contract["roles"][role]
                equal(
                    role_references(step.get("with"), reverse), expected["inputs"], "Wrong role input origin: " + role
                )
                equal(role_references(step.get("when"), reverse), expected.get("when"), "Wrong role guard: " + role)
                if sum(second == role for _, second in edges) > 1:
                    require(step.get("join") == "all_terminal", "Missing all-terminal role join")
                if "actor" in expected:
                    equal(step.get("actor"), expected["actor"], "Wrong role actor")
                if step.get("actor"):
                    actor = config.get("actors", {}).get(step["actor"], {})
                    require(step["uses"] in actor.get("allowed_operations", []), "Actor does not allow role operation")
            exclusive_names = []
            for role in contract.get("exclusive_actors", []):
                step = next(step for step in assignment if step["id"] == binding[role])
                actor_name = step.get("actor")
                require(isinstance(actor_name, str) and actor_name, "Missing exclusive logical actor")
                equal(
                    config.get("actors", {}).get(actor_name, {}).get("allowed_operations"),
                    [step["uses"]],
                    "Logical actor is not exclusive to its role",
                )
                exclusive_names.append(actor_name)
            require(len(exclusive_names) == len(set(exclusive_names)), "Exclusive roles share an actor")
            equal(role_references(workflow["output"], reverse), contract["output"], "Wrong role output lineage")
        except Rejected, KeyError, TypeError, ValueError:
            continue
        matches.append(binding)
        if len(matches) > 1:
            break
    require(len(matches) == 1, "Missing, ambiguous, or incorrect occurrence role/input lineage")
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
