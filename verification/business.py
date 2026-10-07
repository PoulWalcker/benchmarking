"""Business acceptance for invoice-total, ticket-routing and competitor-report, independent of any engine.

Arithmetic uses Python integers and report facts come from supplied source text. Named
obligations run in order for acceptance; `rubric_facts.py` calls the same ones one at a time.
"""

from collections.abc import Callable
import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING or __package__:
    from .contracts import WorkflowObservation, equal, require
    from .scenario_business import fact_contract
else:  # Harbor runs the distributed verifier as a standalone script.
    from contracts import WorkflowObservation, equal, require
    from scenario_business import fact_contract


def _indexed(observation: WorkflowObservation) -> tuple[dict, dict]:
    """Index one observation by operation name, the way this file always has."""
    events = {event["operation"]: event for event in observation.events.values()}
    states = {event["operation"]: observation.states[sid] for sid, event in observation.events.items()}
    return events, states


def _produced(observation: WorkflowObservation, operation: str) -> dict:
    events, states = _indexed(observation)
    return states[operation]["steps"][events[operation]["step_id"]]


def _branches(inputs: dict[str, Any]) -> tuple[str, str]:
    """Which draft the stated overdue boundary requires, and which must not run."""
    high = inputs["ticket"]["days_overdue"] > 2
    if high:
        return "ticket.escalation_draft", "ticket.normal_draft"
    return "ticket.normal_draft", "ticket.escalation_draft"


def expected_classification(inputs: dict[str, Any]) -> dict[str, Any]:
    """What reading this ticket must yield: the overdue boundary chooses the priority."""
    return {"category": "delivery", "priority": "high" if inputs["ticket"]["days_overdue"] > 2 else "normal"}


def _classification(inputs: dict[str, Any], observation: WorkflowObservation) -> dict[str, Any]:
    """The classifier read this ticket and the overdue boundary chose the priority."""
    expected = expected_classification(inputs)
    equal(_produced(observation, "ticket.classify"), expected, "Wrong classification")
    return expected


def _one_branch(inputs: dict[str, Any], observation: WorkflowObservation) -> dict[str, Any]:
    """Exactly one draft ran; the classifier and the join both completed."""
    events, _ = _indexed(observation)
    active, skipped = _branches(inputs)
    require(
        events[active]["status"] == "completed" and events[skipped]["status"] == "skipped",
        "Exactly one correct branch must execute",
    )
    require(
        events["ticket.classify"]["status"] == events["branch.select_one"]["status"] == "completed",
        "Classification/join did not complete",
    )
    return {"active": active, "skipped": skipped}


def _selected_draft(inputs: dict[str, Any], observation: WorkflowObservation) -> dict[str, Any]:
    """The packet, the branch that ran and the join all state the same action."""
    active, _ = _branches(inputs)
    expected = {
        "ticket_id": inputs["ticket"]["id"],
        "action": "escalate" if inputs["ticket"]["days_overdue"] > 2 else "normal_reply",
        "mode": "draft",
    }
    equal(observation.final["output"], expected, "Wrong ticket draft")
    equal(_produced(observation, active), expected, "Wrong selected branch output")
    equal(_produced(observation, "branch.select_one"), expected, "Wrong branch join result")
    return expected


def _skipped_branch(inputs: dict[str, Any], observation: WorkflowObservation) -> dict[str, Any]:
    """The branch that did not run left no output behind and is still recorded."""
    events, states = _indexed(observation)
    _, skipped = _branches(inputs)
    joined = states["branch.select_one"]
    require(events[skipped]["step_id"] not in joined["steps"], "Skipped branch leaked a result into the join")
    require(
        joined["statuses"].get(events[skipped]["step_id"]) == "skipped",
        "Join did not retain skipped terminal branch",
    )
    return {"skipped": skipped}


def ticket_routing_obligations(
    inputs: dict[str, Any], observation: WorkflowObservation, *, case: dict | None = None
) -> dict[str, Callable[[], dict]]:
    """The four named obligations of ticket-routing, in acceptance order."""
    return {
        "classification_matches_boundary": lambda: _classification(inputs, observation),
        "single_branch_executed": lambda: _one_branch(inputs, observation),
        "draft_is_the_selected_action": lambda: _selected_draft(inputs, observation),
        "skipped_branch_left_no_trace": lambda: _skipped_branch(inputs, observation),
    }


def _analyses(observation: WorkflowObservation) -> tuple[dict, dict]:
    return _produced(observation, "research.product"), _produced(observation, "research.marketing")


def _independent_analyses(observation: WorkflowObservation) -> dict[str, Any]:
    """Every operation ran, under its own actor, and neither analysis saw the other."""
    events, states = _indexed(observation)
    require(all(event["status"] == "completed" for event in events.values()), "Unexpected skipped operation")
    product, marketing = _analyses(observation)
    for operation, actor, other in [
        ("research.product", "product-sapi", "research.marketing"),
        ("research.marketing", "marketing-sapi", "research.product"),
        ("research.write", "writer-sapi", None),
    ]:
        require(events[operation].get("actor") == actor, "Research actor metadata was lost")
        if other:
            require(
                events[other]["step_id"] not in states[operation]["statuses"],
                "Analyses are chained rather than independent",
            )
    return {"product": product, "marketing": marketing}


def _quoted_analyses(inputs: dict[str, Any], observation: WorkflowObservation, mode: str) -> dict[str, Any]:
    """Each analysis quotes an excerpt that is literally present in its own source."""
    product, marketing = _analyses(observation)
    for analysis, source, label in [
        (product, inputs["product_material"], "Product"),
        (marketing, inputs["marketing_material"], "Marketing"),
    ]:
        require(set(analysis) == {"summary", "evidence"}, "Invalid analysis fields")
        require(isinstance(analysis["summary"], str) and analysis["summary"].strip(), "Empty analysis summary")
        require(
            isinstance(analysis["evidence"], str) and analysis["evidence"].strip() and analysis["evidence"] in source,
            "Analysis evidence is absent from its own source",
        )
        if mode == "stub":
            equal(analysis, {"summary": label + ": " + source, "evidence": source}, "Wrong deterministic analysis")
    return {"product": product, "marketing": marketing}


def _joined_analyses(observation: WorkflowObservation) -> dict[str, Any]:
    """The join waited for both analyses and carried each one through unchanged."""
    events, states = _indexed(observation)
    product, marketing = _analyses(observation)
    equal(
        _produced(observation, "research.combine"),
        {"product": product, "marketing": marketing},
        "Join lost or changed an analysis",
    )
    combined = states["research.combine"]
    for operation, analysis in (("research.product", product), ("research.marketing", marketing)):
        require(
            combined["statuses"].get(events[operation]["step_id"]) == "completed",
            "Join did not wait for both analyses",
        )
        equal(combined["steps"].get(events[operation]["step_id"]), analysis, "Join lost input evidence")
    return {"product": product, "marketing": marketing}


def _report_excerpts(observation: WorkflowObservation) -> dict[str, Any]:
    """The report is non-empty prose and still carries both excerpts, in order."""
    product, marketing = _analyses(observation)
    output = observation.final["output"]
    require(isinstance(output, dict) and set(output) == {"report", "evidence"}, "Invalid report output")
    require(isinstance(output["report"], str) and output["report"].strip(), "Empty final report")
    equal(
        output["evidence"],
        [product["evidence"], marketing["evidence"]],
        "Report must retain both independent source excerpts",
    )
    return output


def _report_facts(
    inputs: dict[str, Any], observation: WorkflowObservation, mode: str, case: dict | None
) -> dict[str, Any]:
    """The report states the facts the frozen contract requires of these sources."""
    output = observation.final["output"]
    fixture = fact_contract("competitor-report", inputs, case)
    for alternatives in fixture["report_anchors"]:
        require(
            any(re.search(pattern, output["report"], re.IGNORECASE) for pattern in alternatives),
            "Report omitted a required source fact: " + " / ".join(alternatives),
        )
    if mode == "stub":
        product, marketing = _analyses(observation)
        equal(
            output["report"],
            product["summary"] + "\n\n" + marketing["summary"],
            "Report omitted or altered an analysis",
        )
    return fixture


def competitor_report_obligations(
    inputs: dict[str, Any], observation: WorkflowObservation, *, mode: str = "live", case: dict | None = None
) -> dict[str, Callable[[], dict]]:
    """The five named obligations of competitor-report, in acceptance order.

    Stub-only equalities check the harness's own operations, so `mode` defaults to live.
    """
    return {
        "analyses_independent": lambda: _independent_analyses(observation),
        "analyses_quote_their_source": lambda: _quoted_analyses(inputs, observation, mode),
        "join_preserves_both_analyses": lambda: _joined_analyses(observation),
        "report_carries_both_excerpts": lambda: _report_excerpts(observation),
        "report_covers_both_sources": lambda: _report_facts(inputs, observation, mode, case),
    }


def invoice_total(
    inputs: dict, observation: WorkflowObservation, mode: str = "stub", *, case: dict | None = None
) -> None:
    output = observation.final["output"]
    require(
        all(event["status"] == "completed" for event in observation.events.values()), "Unexpected skipped operation"
    )
    invoices = inputs["invoices"]
    expected = {
        "total_minor": sum(x["amount_minor"] for x in invoices),
        "currency": invoices[0]["currency"],
        "invoice_count": len(invoices),
    }
    equal(output, expected, "Wrong invoice result")
    equal(
        _produced(observation, "invoices.validate"), {"invoices": invoices}, "Invoice validation changed/lost invoices"
    )
    equal(
        _produced(observation, "invoices.sum"),
        {
            "amount_minor": expected["total_minor"],
            "currency": expected["currency"],
            "count": expected["invoice_count"],
        },
        "Wrong intermediate sum",
    )
    equal(_produced(observation, "invoices.report"), expected, "Wrong report operation output")


def ticket_routing(
    inputs: dict, observation: WorkflowObservation, mode: str = "stub", *, case: dict | None = None
) -> None:
    for obligation in ticket_routing_obligations(inputs, observation).values():
        obligation()


def competitor_report(
    inputs: dict, observation: WorkflowObservation, mode: str = "stub", *, case: dict | None = None
) -> None:
    for obligation in competitor_report_obligations(inputs, observation, mode=mode, case=case).values():
        obligation()
    equal(_produced(observation, "research.write"), observation.final["output"], "Result differs from writer output")
