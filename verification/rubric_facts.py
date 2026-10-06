"""Run facts for the rubric: the seam between acceptance and `rubric.py`; never decides acceptance.

A named check calls an obligation `business.py` or `scenario_business.py` already
states and records whether it held, so a check cannot drift from acceptance. The
protected narrative goes under "verification", which `rubric._judge_view` never forwards.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING or __package__:
    from .business import competitor_report_obligations, ticket_routing_obligations
    from .contracts import Rejected, WorkflowObservation
    from .rubric import SCHEMA, Judge, RubricCard, RubricError, RunFacts, digest, score
    from .rubric_cards import card_for
    from .scenario_business import (
        bulletin_brief_obligations,
        priority_support_obligations,
        support_review_obligations,
    )
else:  # Harbor runs the distributed verifier as a standalone script.
    from business import competitor_report_obligations, ticket_routing_obligations
    from contracts import Rejected, WorkflowObservation
    from rubric import SCHEMA, Judge, RubricCard, RubricError, RunFacts, digest, score
    from rubric_cards import card_for
    from scenario_business import (
        bulletin_brief_obligations,
        priority_support_obligations,
        support_review_obligations,
    )

Document = dict[str, Any]

NOT_EVALUATED = "not_evaluated"
_NO_JUDGE = "Not scored: this card has judged criteria and no judge is reachable here"
_NO_RUN = "Not scored: no executed case produced the named checks"

# What a malformed run raises past an obligation; the same set verify.corruption_checks treats as rejection.
_REJECTIONS = (Rejected, KeyError, TypeError, IndexError)


def observe(
    scenario: str, inputs: dict[str, Any], observation: WorkflowObservation, *, case: dict | None = None
) -> Document:
    """Facts for one executed case; never raises, and reports no facts rather than scoring a card short."""
    try:
        checks = _checks(scenario, inputs, observation, case)
        prose = _prose(scenario, inputs, observation, checks)
    except Exception:  # Facts must never fail an accepted run
        checks, prose = {}, {}
    return {"case": (case or {}).get("name"), "checks": checks, "prose": prose}


def evaluate(
    scenario: str,
    runs: list[Document],
    *,
    accepted: bool,
    execution_pass: bool,
    judge: Judge | None = None,
) -> Document | None:
    """This submission's card scored, or why not; None for a scenario with no card. Never raises."""
    try:
        card = card_for(scenario)
    except RubricError:
        return None
    checks: dict[str, bool] = {"accepted": bool(accepted)}
    try:
        checks = _merged_checks(runs, accepted)
        missing = sorted(
            criterion.check_id
            for criterion in card.criteria
            if criterion.evaluator == "deterministic" and criterion.check_id not in checks
        )
        if missing:
            return _not_evaluated(card, _NO_RUN + ": " + ", ".join(missing), checks, execution_pass)
        if card.needs_judge and judge is None:
            return _not_evaluated(card, _NO_JUDGE, checks, execution_pass)
        facts = RunFacts(
            execution_pass=execution_pass,
            checks=checks,
            prose=_merged_prose(runs),
            run_digest=digest({"scenario": scenario, "cases": [run.get("case") for run in runs]}),
        )
        return score(card, facts, judge)
    except Exception as error:  # A rubric must never fail an accepted run
        return _not_evaluated(card, "Not scored: " + type(error).__name__ + ": " + str(error), checks, execution_pass)


def _not_evaluated(card: RubricCard, reason: str, checks: dict[str, bool], execution_pass: bool) -> Document:
    """Scoring never ran: the score is null, never zero."""
    return {
        "schema": SCHEMA,
        "rubric": {"id": card.id, "version": card.version, "origin": card.origin, "digest": card.digest()},
        "status": NOT_EVALUATED,
        "score_0_10": None,
        "normalized_reward": None,
        "execution_pass": execution_pass,
        "checks": dict(checks),
        "reason": reason,
    }


def _merged_checks(runs: list[Document], accepted: bool) -> dict[str, bool]:
    """One verdict per named check across the cases: it held, or it held everywhere."""
    merged: dict[str, bool] = {"accepted": bool(accepted)}
    if any(not run["checks"] for run in runs):
        # A case without facts leaves the card unscored, never a check holding where it was not measured.
        return merged
    for run in runs:
        for name, held in run["checks"].items():
            merged[name] = merged.get(name, True) and bool(held)
    return merged


def _merged_prose(runs: list[Document]) -> dict[str, str]:
    """Label the cases by ordinal, never by fixture name: a name states its intent."""
    blocks: dict[str, list[str]] = {}
    for number, run in enumerate(runs, 1):
        for source, text in run["prose"].items():
            blocks.setdefault(source, []).append("Run " + str(number) + ":\n" + text)
    return {source: "\n\n".join(texts) for source, texts in blocks.items()}


def _checks(
    scenario: str, inputs: dict[str, Any], observation: WorkflowObservation, case: dict | None = None
) -> dict[str, bool]:
    if scenario == "ticket-routing":
        obligations = ticket_routing_obligations(inputs, observation)
    elif scenario == "competitor-report":
        obligations = competitor_report_obligations(inputs, observation, case=case)
    elif scenario == "support-review-packet":
        obligations = support_review_obligations(inputs, observation)
    elif scenario == "bulletin-market-brief":
        obligations = bulletin_brief_obligations(inputs, observation, case=case)
    elif scenario == "priority-support-brief":
        obligations = priority_support_obligations(inputs, observation, case=case)
    else:
        return {}
    return {name: _held(obligation) for name, obligation in obligations.items()}


def _prose(
    scenario: str, inputs: dict[str, Any], observation: WorkflowObservation, checks: dict[str, bool]
) -> dict[str, str]:
    if scenario == "competitor-report":
        return _competitor_report_prose(inputs, observation, checks)
    if scenario == "support-review-packet":
        return _support_review_prose(inputs, observation, checks)
    if scenario == "bulletin-market-brief":
        return _bulletin_brief_prose(inputs, observation, checks)
    if scenario == "priority-support-brief":
        return _priority_support_prose(inputs, observation, checks)
    return {}


def _held(obligation: Any) -> bool:
    try:
        obligation()
    except _REJECTIONS:
        return False
    return True


def _support_review_prose(
    inputs: dict[str, Any], observation: WorkflowObservation, checks: dict[str, bool]
) -> dict[str, str]:
    """The ticket given, the packet produced, and the verdict a judge must not see."""
    output = _mapping(getattr(observation, "final", None), "output")
    ticket = _mapping(inputs, "ticket")
    invoices = inputs.get("invoices") if isinstance(inputs, dict) else None
    reply = _mapping(output, "reply")
    action = _mapping(output, "action")
    return {
        "environment": "\n".join(
            [
                "Ticket " + _render(ticket.get("id")) + ", " + _render(ticket.get("days_overdue")) + " days overdue.",
                "Ticket text: " + _render(ticket.get("text")),
                "Invoices on the account: " + _invoices(invoices),
            ]
        ),
        "candidate": "\n".join(
            [
                "Draft reply: " + _render(reply.get("text")),
                "The workflow's own review of that draft: " + _review(output.get("review")),
                "Action it selected: "
                + _render(action.get("action"))
                + " in "
                + _render(action.get("mode"))
                + " mode.",
            ]
        ),
        # Protected: _judge_view never forwards "verification".
        "verification": _verification(checks),
    }


def _verification(checks: dict[str, bool]) -> str:
    """The protected narrative. No llm criterion may declare its source name."""
    return "Named checks: " + ", ".join(
        name + (" held" if held else " failed") for name, held in sorted(checks.items())
    )


def _competitor_report_prose(
    inputs: dict[str, Any], observation: WorkflowObservation, checks: dict[str, bool]
) -> dict[str, str]:
    """The two source materials, the analyses over them, and the report they fed."""
    product = _by_operation(observation, "research.product")
    marketing = _by_operation(observation, "research.marketing")
    output = _mapping(getattr(observation, "final", None), "output")
    return {
        "environment": "\n".join(
            [
                "Product material: " + _render(_get(inputs, "product_material")),
                "Marketing material: " + _render(_get(inputs, "marketing_material")),
            ]
        ),
        "candidate": "\n".join(
            [
                "Product analysis: " + _render(product.get("summary")),
                "Excerpt it quoted from the product material: " + _render(product.get("evidence")),
                "Marketing analysis: " + _render(marketing.get("summary")),
                "Excerpt it quoted from the marketing material: " + _render(marketing.get("evidence")),
                "Report it wrote: " + _render(output.get("report")),
                "Excerpts carried with the report: " + _excerpts(output.get("evidence")),
            ]
        ),
        "verification": _verification(checks),
    }


def _bulletin_brief_prose(
    inputs: dict[str, Any], observation: WorkflowObservation, checks: dict[str, bool]
) -> dict[str, str]:
    """The bulletins and the marketing copy, then the digest and brief made of them."""
    digest = _by_role(observation, "summarize")
    product = _by_role(observation, "product")
    marketing = _by_role(observation, "marketing")
    brief = _by_role(observation, "write")
    return {
        "environment": "\n".join(
            [
                "Articles supplied:",
                _articles(_get(inputs, "articles")),
                "Marketing material: " + _render(_get(inputs, "marketing_material")),
            ]
        ),
        "candidate": "\n".join(
            [
                "Digest it wrote: " + _render(digest.get("text")),
                "Articles the digest claims to cover: " + _excerpts(digest.get("article_ids")),
                "Product analysis of that digest: " + _render(product.get("summary")),
                "Marketing analysis: " + _render(marketing.get("summary")),
                "Brief it wrote: " + _render(brief.get("report")),
            ]
        ),
        "verification": _verification(checks),
    }


def _priority_support_prose(
    inputs: dict[str, Any], observation: WorkflowObservation, checks: dict[str, bool]
) -> dict[str, str]:
    """The ticket and the research material, then the action and whatever brief followed."""
    ticket = _mapping(inputs, "ticket")
    output = _mapping(getattr(observation, "final", None), "output")
    action = _mapping(output, "action")
    brief = output.get("brief")
    return {
        "environment": "\n".join(
            [
                "Ticket " + _render(ticket.get("id")) + ", " + _render(ticket.get("days_overdue")) + " days overdue.",
                "Ticket text: " + _render(ticket.get("text")),
                "Product material: " + _render(_get(inputs, "product_material")),
                "Marketing material: " + _render(_get(inputs, "marketing_material")),
            ]
        ),
        "candidate": "\n".join(
            [
                "Action it selected: "
                + _render(action.get("action"))
                + " in "
                + _render(action.get("mode"))
                + " mode.",
                "Brief it wrote: " + (_render(brief.get("report")) if isinstance(brief, dict) else "none."),
            ]
        ),
        "verification": _verification(checks),
    }


def _by_operation(observation: Any, operation: str) -> dict[str, Any]:
    """One operation's own output, or {}; collecting prose must never raise."""
    try:
        for sid, event in observation.events.items():
            if event.get("operation") == operation:
                value = observation.states[sid]["steps"].get(sid)
                return value if isinstance(value, dict) else {}
    except AttributeError, KeyError, TypeError:
        return {}
    return {}


def _by_role(observation: Any, role: str) -> dict[str, Any]:
    """One role's own output, or {}; collecting prose must never raise."""
    try:
        sid = observation.roles[role]
        value = observation.states[sid]["steps"].get(sid)
    except AttributeError, KeyError, TypeError:
        return {}
    return value if isinstance(value, dict) else {}


def _get(inputs: Any, key: str) -> Any:
    return inputs.get(key) if isinstance(inputs, dict) else None


def _articles(articles: Any) -> str:
    if not isinstance(articles, list) or not articles:
        return _render(articles)
    return "\n".join(
        "- " + _render(item.get("id")) + " " + _render(item.get("title")) + ": " + _render(item.get("text"))
        if isinstance(item, dict)
        else "- " + _render(item)
        for item in articles
    )


def _excerpts(values: Any) -> str:
    if not isinstance(values, list) or not values:
        return _render(values)
    return "; ".join(_render(item) for item in values)


def _mapping(value: Any, key: str) -> dict[str, Any]:
    """Reach one nested mapping of a run that may be any shape at all."""
    inner = value.get(key) if isinstance(value, dict) else getattr(value, key, None)
    return inner if isinstance(inner, dict) else {}


def _render(value: Any) -> str:
    if isinstance(value, str):
        return value
    if value is None:
        return "(not produced)"
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _invoices(invoices: Any) -> str:
    if not isinstance(invoices, list) or not invoices:
        return _render(invoices)
    return "; ".join(
        _render(item.get("id")) + " " + _render(item.get("amount_minor")) + " " + _render(item.get("currency"))
        if isinstance(item, dict)
        else _render(item)
        for item in invoices
    )


def _review(review: Any) -> str:
    if not isinstance(review, dict) or "pass" not in review:
        return _render(review)
    errors = review.get("errors")
    if review["pass"]:
        return "it passed."
    if isinstance(errors, list) and errors:
        return "it failed, asking for: " + "; ".join(_render(item) for item in errors) + "."
    return "it failed: " + _render(errors) + "."
