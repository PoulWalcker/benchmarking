"""Business acceptance independent of compiler code and execution engines.

Arithmetic uses Python integers, routing follows the stated boundary, and report
facts come from supplied source text. A successful result here makes no claim
about whether any workflow actually executed.
"""

import json
import re
from pathlib import Path
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING or __package__:
    from .contracts import Rejected, WorkflowObservation, equal, require
else:  # Harbor runs the distributed verifier as a standalone script.
    from contracts import Rejected, WorkflowObservation, equal, require

OPERATIONS = {
    "invoice-total": ["invoices.validate", "invoices.sum", "invoices.report"],
    "ticket-routing": ["ticket.classify", "ticket.escalation_draft", "ticket.normal_draft", "branch.select_one"],
    "competitor-report": ["research.product", "research.marketing", "research.combine", "research.write"],
}


def check_business_result(
    scenario: str,
    inputs: dict[str, Any],
    observation: WorkflowObservation,
    mode: str = "stub",
    *,
    case: dict | None = None,
) -> dict[str, Any]:
    if scenario not in OPERATIONS:
        if TYPE_CHECKING or __package__:
            from .scenario_business import check_scenario_business_result
        else:
            from scenario_business import check_scenario_business_result
        return check_scenario_business_result(scenario, inputs, observation, mode, case=case)
    final = observation.final
    events = {event["operation"]: event for event in observation.events.values()}
    states = {event["operation"]: observation.states[sid] for sid, event in observation.events.items()}

    def value(operation: str) -> dict:
        return states[operation]["steps"][events[operation]["step_id"]]

    def all_completed() -> None:
        require(all(event["status"] == "completed" for event in events.values()), "Unexpected skipped operation")

    output = final["output"]
    if scenario == "invoice-total":
        all_completed()
        invoices = inputs["invoices"]
        expected = {
            "total_minor": sum(x["amount_minor"] for x in invoices),
            "currency": invoices[0]["currency"],
            "invoice_count": len(invoices),
        }
        equal(output, expected, "Wrong invoice result")
        equal(value("invoices.validate"), {"invoices": invoices}, "Invoice validation changed/lost invoices")
        equal(
            value("invoices.sum"),
            {
                "amount_minor": expected["total_minor"],
                "currency": expected["currency"],
                "count": expected["invoice_count"],
            },
            "Wrong intermediate sum",
        )
        equal(value("invoices.report"), expected, "Wrong report operation output")
    elif scenario == "ticket-routing":
        high = inputs["ticket"]["days_overdue"] > 2
        active = "ticket.escalation_draft" if high else "ticket.normal_draft"
        skipped = "ticket.normal_draft" if high else "ticket.escalation_draft"
        equal(
            value("ticket.classify"),
            {"category": "delivery", "priority": "high" if high else "normal"},
            "Wrong classification",
        )
        require(
            events[active]["status"] == "completed" and events[skipped]["status"] == "skipped",
            "Exactly one correct branch must execute",
        )
        require(
            events["ticket.classify"]["status"] == events["branch.select_one"]["status"] == "completed",
            "Classification/join did not complete",
        )
        expected = {
            "ticket_id": inputs["ticket"]["id"],
            "action": "escalate" if high else "normal_reply",
            "mode": "draft",
        }
        equal(output, expected, "Wrong ticket draft")
        equal(value(active), expected, "Wrong selected branch output")
        equal(value("branch.select_one"), expected, "Wrong branch join result")
        joined = states["branch.select_one"]
        require(events[skipped]["step_id"] not in joined["steps"], "Skipped branch leaked a result into the join")
        require(
            joined["statuses"].get(events[skipped]["step_id"]) == "skipped",
            "Join did not retain skipped terminal branch",
        )
    else:
        all_completed()
        product, marketing = value("research.product"), value("research.marketing")
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
        for analysis, source, label in [
            (product, inputs["product_material"], "Product"),
            (marketing, inputs["marketing_material"], "Marketing"),
        ]:
            require(set(analysis) == {"summary", "evidence"}, "Invalid analysis fields")
            require(isinstance(analysis["summary"], str) and analysis["summary"].strip(), "Empty analysis summary")
            require(
                isinstance(analysis["evidence"], str)
                and analysis["evidence"].strip()
                and analysis["evidence"] in source,
                "Analysis evidence is absent from its own source",
            )
            if mode == "stub":
                equal(analysis, {"summary": label + ": " + source, "evidence": source}, "Wrong deterministic analysis")
        equal(
            value("research.combine"), {"product": product, "marketing": marketing}, "Join lost or changed an analysis"
        )
        combined = states["research.combine"]
        for operation in ("research.product", "research.marketing"):
            require(
                combined["statuses"].get(events[operation]["step_id"]) == "completed",
                "Join did not wait for both analyses",
            )
            equal(combined["steps"].get(events[operation]["step_id"]), value(operation), "Join lost input evidence")
        require(isinstance(output, dict) and set(output) == {"report", "evidence"}, "Invalid report output")
        require(isinstance(output["report"], str) and output["report"].strip(), "Empty final report")
        equal(
            output["evidence"],
            [product["evidence"], marketing["evidence"]],
            "Report must retain both independent source excerpts",
        )
        fixtures = json.loads((Path(__file__).parent / "cases.json").read_text())["competitor-report"]["positive"]
        fixture = next((case for case in fixtures if case["inputs"] == inputs), None)
        if fixture is None:
            raise Rejected("No independent report fact contract for these source materials")
        for alternatives in fixture["report_anchors"]:
            require(
                any(re.search(pattern, output["report"], re.IGNORECASE) for pattern in alternatives),
                "Report omitted a required source fact: " + " / ".join(alternatives),
            )
        if mode == "stub":
            equal(
                output["report"],
                product["summary"] + "\n\n" + marketing["summary"],
                "Report omitted or altered an analysis",
            )
        equal(value("research.write"), output, "Result differs from writer output")
    return {
        "output_verified": True,
        "operation_count": len(events),
        "llm_mode": mode,
    }
