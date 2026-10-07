"""Benchmark-specific views of source material and output for independent rubric judges."""

import json
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING or __package__:
    from .contracts import WorkflowObservation
else:
    from contracts import WorkflowObservation


def support_review_prose(
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


def competitor_report_prose(
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


def bulletin_brief_prose(
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


def priority_support_prose(
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
