"""Composition of independent fixture evaluators; benchmark semantics never come from runtime operations."""

import copy
from typing import TYPE_CHECKING

if TYPE_CHECKING or __package__:
    from . import business, fixture_prose, scenario_business
    from .contracts import WorkflowObservation, require
    from .fixture import FixtureEvaluator
    from .fixture_freshness import composed_fixtures
    from .n8n_provenance import rows
else:
    import business
    from contracts import WorkflowObservation, require
    from fixture import FixtureEvaluator
    from fixture_freshness import composed_fixtures
    import fixture_prose
    from n8n_provenance import rows
    import scenario_business


def ticket_guard(inputs: dict, when: dict) -> bool:
    """The routing benchmarks independently predict the classifier's priority."""
    field = when["ref"].removeprefix("steps.classify.")
    require(field != when["ref"], "No stated expectation decides this model call")
    return business.expected_classification(inputs)[field] == when["eq"]


def invoice_output(candidate: dict) -> None:
    final = rows(candidate["run_data"]["Result"][0])[0]
    final["output"]["total_minor"] += 1
    candidate["output"] = copy.deepcopy(final["output"])
    candidate["result"] = copy.deepcopy(final)


def routing_output(candidate: dict) -> None:
    final = rows(candidate["run_data"]["Result"][0])[0]
    final["output"]["action"] = "normal_reply" if final["output"]["action"] == "escalate" else "escalate"
    candidate["output"] = copy.deepcopy(final["output"])
    candidate["result"] = copy.deepcopy(final)


def report_output(candidate: dict) -> None:
    final = rows(candidate["run_data"]["Result"][0])[0]
    final["output"]["evidence"] = final["output"]["evidence"][:1]
    candidate["output"] = copy.deepcopy(final["output"])
    candidate["result"] = copy.deepcopy(final)


def missing_analysis(candidate: dict) -> None:
    event = next(
        event
        for event in rows(candidate["run_data"]["Result"][0])[0]["trace"]
        if event["operation"] == "research.marketing"
    )
    candidate["run_data"].pop(candidate["mapping"][event["step_id"]])


def contentless_report(candidate: dict) -> None:
    final = rows(candidate["run_data"]["Result"][0])[0]
    final["output"]["report"] = "An unrelated but nonempty report."
    writer = next(event for event in final["trace"] if event["operation"] == "research.write")
    envelope = rows(candidate["run_data"][candidate["mapping"][writer["step_id"]]][0])[0]
    envelope["steps"][writer["step_id"]]["report"] = final["output"]["report"]
    final["steps"][writer["step_id"]]["report"] = final["output"]["report"]
    candidate["output"] = copy.deepcopy(final["output"])
    candidate["result"] = copy.deepcopy(final)


EVALUATORS = {
    "invoice-total": FixtureEvaluator(business.invoice_total, corrupt_output=invoice_output),
    "ticket-routing": FixtureEvaluator(
        business.ticket_routing,
        guard=ticket_guard,
        obligations=business.ticket_routing_obligations,
        corrupt_output=routing_output,
    ),
    "competitor-report": FixtureEvaluator(
        business.competitor_report,
        obligations=business.competitor_report_obligations,
        prose=fixture_prose.competitor_report_prose,
        corrupt_output=report_output,
        extra_corruptions=(
            ("missing-analysis-execution", missing_analysis),
            ("contentless-report-with-preserved-evidence", contentless_report),
        ),
    ),
    "revise-answer": FixtureEvaluator(procedure="refinement"),
    "daily-digest": FixtureEvaluator(procedure="lifecycle"),
    "dual-ledger-closeout": FixtureEvaluator(scenario_business.dual_ledger_closeout, fresh=composed_fixtures),
    "support-review-packet": FixtureEvaluator(
        scenario_business.support_review_packet,
        guard=ticket_guard,
        obligations=scenario_business.support_review_obligations,
        prose=fixture_prose.support_review_prose,
        fresh=composed_fixtures,
    ),
    "bulletin-market-brief": FixtureEvaluator(
        scenario_business.bulletin_market_brief,
        obligations=scenario_business.bulletin_brief_obligations,
        prose=fixture_prose.bulletin_brief_prose,
        fresh=composed_fixtures,
    ),
    "priority-support-brief": FixtureEvaluator(
        scenario_business.priority_support_brief,
        guard=ticket_guard,
        obligations=scenario_business.priority_support_obligations,
        prose=fixture_prose.priority_support_prose,
        fresh=composed_fixtures,
    ),
}


def evaluator_for(scenario: str) -> FixtureEvaluator:
    require(scenario in EVALUATORS, "Unknown acceptance scenario", "unknown_scenario")
    return EVALUATORS[scenario]


def check_business_result(
    scenario: str, inputs: dict, observation: WorkflowObservation, mode: str = "stub", *, case: dict | None = None
) -> dict:
    check = evaluator_for(scenario).business
    require(check is not None, "This fixture uses a dedicated evaluation procedure")
    assert check is not None
    check(inputs, observation, mode, case=case)
    return {"output_verified": True, "operation_count": len(observation.events), "llm_mode": mode}


def fresh_cases(scenario: str, cases: dict) -> dict:
    fresh = evaluator_for(scenario).fresh
    require(fresh is not None, "This fixture evaluator declares no freshness contract")
    assert fresh is not None
    return fresh(cases)
