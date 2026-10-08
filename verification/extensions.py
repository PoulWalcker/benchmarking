"""Independent checks for bounded refinement (revise-answer): a logical history alone never proves an attempt ran."""

import copy
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING or __package__:
    from .contracts import Rejected, equal, require
    from .n8n_provenance import rows
    from .refinement import check_refinement_history as generic_history
    from .refinement import verify_refinement as generic_verify
    from .roles import whole_results
else:  # Standalone Harbor distribution.
    from contracts import Rejected, equal, require
    from n8n_provenance import rows
    from refinement import check_refinement_history as generic_history
    from refinement import verify_refinement as generic_verify
    from roles import whole_results


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
    uses = {draft["id"]: "reply.generate", check["id"]: "reply.check"}
    equal(
        {key: whole_results(value, uses) for key, value in check["with"].items()},
        {
            "draft": {"ref": "steps." + draft["id"]},
            "order_id": {"ref": "inputs.required_order_id"},
            "max_characters": {"ref": "inputs.max_characters"},
        },
        "Review lost draft or constraint origin",
    )
    equal(whole_results(workflow["output"], uses), {"ref": "steps." + draft["id"]}, "Wrong refinement output origin")
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


def check_reply_steps(config: dict, attempt: dict) -> None:
    """Keep the retired reply business rule in its compatibility evaluator."""
    draft, check = reply_roles(config)
    output = attempt["steps"][draft]
    require(isinstance(output, dict) and set(output) == {"text"}, "Wrong draft output shape")
    inputs = config["workflow"]["inputs"]
    equal(
        attempt["steps"][check],
        review_reply(output["text"], inputs["required_order_id"], inputs["max_characters"]),
        "Dishonest independent reply review",
    )


def check_refinement_history(config: dict, attempts: list[dict]) -> dict[str, Any]:
    """Legacy reply history adapter."""
    reply_roles(config)
    return generic_history(config, attempts, check_steps=check_reply_steps)


def verify_refinement(config: dict, run: dict, *, mode: str = "live") -> dict[str, Any]:
    """Legacy reply native-evidence adapter."""
    reply_roles(config)
    return generic_verify(config, run, check_steps=check_reply_steps, mode=mode)


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


def verify_refinement_task(config: dict | None, run: dict, *, mode: str, case: dict | None) -> dict:
    """The reply benchmark's identity and exhaustion contract, beyond generic refinement evidence."""
    require(config is not None and case is not None, "Refinement requires submitted config and frozen case")
    assert config is not None and case is not None
    require(
        config["workflow"]["id"] == "revise-answer" and config["workflow"]["revision"] == 1, "Wrong reply task identity"
    )
    require(config["execution"]["refinement"]["max_attempts"] == 3, "Reply task requires three maximum attempts")
    result = verify_refinement(config, run, mode=mode)
    require(result["exhausted"] == (case.get("expected") == "exhausted"), "Wrong refinement business outcome")
    return {key: value for key, value in result.items() if key != "calls"}


def refinement_model_calls(config: dict) -> dict[str, str]:
    """The reply benchmark reserves every possible draft attempt independently of the runtime."""
    draft, _ = reply_roles(config)
    require(config["execution"]["refinement"]["max_attempts"] == 3, "Reply task requires three maximum attempts")
    revision = config["workflow"]["revision"]
    return {f"revise-answer/r{revision}/{draft}/attempt{number}": "reply.generate" for number in range(1, 4)}
