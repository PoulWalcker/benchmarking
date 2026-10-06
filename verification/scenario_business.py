"""Independent business obligations for the four bounded expansion scenarios.

These checks consume logical observations only. They import neither compiler nor
operation implementations; native execution and submitted origins are checked at
separate seams. Source facts are an explicitly bounded fixture lexical contract.
"""

from collections.abc import Callable
import json
import re
from pathlib import Path
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING or __package__:
    from .contracts import WorkflowObservation, equal, require
else:
    from contracts import WorkflowObservation, equal, require


def _occurrence(observation: WorkflowObservation, role: str) -> dict:
    sid = observation.roles[role]
    require(observation.events[sid]["status"] == "completed", "Required occurrence did not complete: " + role)
    require(sid in observation.states[sid]["steps"], "Missing occurrence output: " + role)
    return observation.states[sid]["steps"][sid]


def _ledger(
    inputs: dict[str, Any], observation: WorkflowObservation, field: str, validate: str, total: str, report: str
) -> dict:
    invoices = inputs[field]
    expected = {
        "total_minor": sum(item["amount_minor"] for item in invoices),
        "currency": invoices[0]["currency"],
        "invoice_count": len(invoices),
    }
    equal(_occurrence(observation, validate), {"invoices": invoices}, "Validation changed ledger invoices")
    equal(
        _occurrence(observation, total),
        {
            "amount_minor": expected["total_minor"],
            "currency": expected["currency"],
            "count": expected["invoice_count"],
        },
        "Wrong ledger intermediate sum",
    )
    equal(_occurrence(observation, report), expected, "Wrong ledger report")
    return expected


def _routing(inputs: dict[str, Any], observation: WorkflowObservation) -> dict:
    roles, events, states = observation.roles, observation.events, observation.states
    high = inputs["ticket"]["days_overdue"] > 2
    active, skipped = ("escalate", "normal") if high else ("normal", "escalate")
    equal(
        _occurrence(observation, "classify"),
        {"category": "delivery", "priority": "high" if high else "normal"},
        "Wrong classification",
    )
    require(events[roles[skipped]]["status"] == "skipped", "Wrong action branch executed")
    expected = {
        "ticket_id": inputs["ticket"]["id"],
        "action": "escalate" if high else "normal_reply",
        "mode": "draft",
    }
    equal(_occurrence(observation, active), expected, "Wrong selected action")
    equal(_occurrence(observation, "select"), expected, "Wrong action selection")
    require(roles[skipped] not in states[roles["select"]]["steps"], "Skipped action leaked output")
    require(states[roles["select"]]["statuses"].get(roles[skipped]) == "skipped", "Missing skipped action status")
    return expected


def _reply_review(inputs: dict[str, Any], observation: WorkflowObservation) -> dict:
    """The emitted review must equal the errors computed here from the draft itself."""
    reply = _occurrence(observation, "draft")
    require(set(reply) == {"text"} and isinstance(reply["text"], str) and reply["text"], "Invalid reply draft")
    errors = []
    if inputs["required_order_id"] not in reply["text"]:
        errors.append("Include the order ID")
    # JS string length counts UTF-16 code units, including surrogate pairs.
    if len(reply["text"].encode("utf-16-le", errors="surrogatepass")) // 2 > inputs["max_characters"]:
        errors.append("Shorten the reply")
    review = {"pass": not errors, "errors": errors}
    equal(_occurrence(observation, "check"), review, "Dishonest reply review")
    return {"reply": reply, "review": review}


def _facts(text: str, anchors: list, label: str, forbidden: list | tuple = ()) -> None:
    """Lexical source-fact coverage: every required fact present, no absent one invented."""
    require(isinstance(text, str) and text.strip(), "Empty " + label)
    for alternatives in anchors:
        require(
            any(re.search(pattern, text, re.IGNORECASE) for pattern in alternatives),
            label + " omitted a required source fact: " + " / ".join(alternatives),
        )
    for pattern in forbidden:
        require(not re.search(pattern, text, re.IGNORECASE), label + " invented a controlled absent fact: " + pattern)


def _fact_contract(scenario: str, inputs: dict[str, Any], case: dict | None) -> dict:
    """The frozen fixture these inputs came from, which states the required facts."""
    if case is not None:
        equal(case.get("inputs"), inputs, "Independent fixture inputs differ")
        return case
    fixtures = json.loads((Path(__file__).parent / "cases.json").read_text())[scenario]["positive"]
    matches = [item for item in fixtures if item["inputs"] == inputs]
    require(matches, "No independent source fact contract for these inputs")
    return matches[0]


def _research(
    observation: WorkflowObservation,
    product_source: str,
    marketing_source: str,
    fixture: dict,
    mode: str = "live",
) -> dict:
    """Two independent analyses, a join that keeps both, and a brief over them."""
    roles, states = observation.roles, observation.states
    product, marketing = _occurrence(observation, "product"), _occurrence(observation, "marketing")
    for name, analysis, source in (
        ("product", product, product_source),
        ("marketing", marketing, marketing_source),
    ):
        require(set(analysis) == {"summary", "evidence"}, "Invalid analysis fields")
        require(
            isinstance(analysis["evidence"], str) and analysis["evidence"].strip() and analysis["evidence"] in source,
            "Analysis evidence is absent from its actual source",
        )
        _facts(
            analysis["summary"],
            fixture[name + "_anchors"],
            name + " analysis",
            fixture.get("forbidden_anchors", []),
        )
        other = "marketing" if name == "product" else "product"
        require(roles[other] not in states[roles[name]]["statuses"], "Research analyses are chained")
        if mode == "stub":
            equal(
                analysis, {"summary": name.title() + ": " + source, "evidence": source}, "Wrong deterministic analysis"
            )
    equal(
        _occurrence(observation, "combine"),
        {"product": product, "marketing": marketing},
        "Join changed analysis or evidence",
    )
    for role in ("product", "marketing"):
        joined = states[roles["combine"]]
        require(joined["statuses"].get(roles[role]) == "completed", "Join lost completed analysis status")
        equal(joined["steps"].get(roles[role]), _occurrence(observation, role), "Join lost analysis output")
    brief = _occurrence(observation, "write")
    require(set(brief) == {"report", "evidence"}, "Invalid brief output")
    equal(brief["evidence"], [product["evidence"], marketing["evidence"]], "Brief changed source evidence order")
    _facts(
        brief["report"],
        fixture["product_anchors"] + fixture["marketing_anchors"],
        "brief",
        fixture.get("forbidden_anchors", []),
    )
    if mode == "stub":
        equal(brief["report"], product["summary"] + "\n\n" + marketing["summary"], "Wrong deterministic brief")
    return brief


def _digest(inputs: dict[str, Any], observation: WorkflowObservation, case: dict | None) -> dict:
    """The digest lists exactly the supplied articles, once each, and changed none."""
    _fact_contract("bulletin-market-brief", inputs, case)
    equal(_occurrence(observation, "prepare"), {"articles": inputs["articles"]}, "Preparation changed articles")
    digest = _occurrence(observation, "summarize")
    require(set(digest) == {"text", "article_ids"}, "Invalid digest fields")
    ids = digest["article_ids"]
    require(isinstance(ids, list) and all(isinstance(item, str) for item in ids), "Invalid digest article IDs")
    require(len(ids) == len(set(ids)), "Duplicate digest article ID")
    equal(sorted(ids), sorted(item["id"] for item in inputs["articles"]), "Unknown or missing digest article ID")
    return digest


def _digest_facts(inputs: dict[str, Any], observation: WorkflowObservation, case: dict | None) -> dict:
    """The digest text states the facts the supplied articles require of it."""
    fixture = _fact_contract("bulletin-market-brief", inputs, case)
    digest = _occurrence(observation, "summarize")
    _facts(digest["text"], fixture["product_anchors"], "digest", fixture.get("forbidden_anchors", []))
    return digest


def _preview(observation: WorkflowObservation) -> dict:
    """The preview repeats the digest and adds only its own mode."""
    preview = {"mode": "preview", **_occurrence(observation, "summarize")}
    equal(_occurrence(observation, "preview"), preview, "Preview changed digest")
    return preview


def _actors(observation: WorkflowObservation) -> dict:
    """Four distinct logical actors produced the four authored outputs."""
    events, roles = observation.events, observation.roles
    actors = [events[roles[role]].get("actor") for role in ("summarize", "product", "marketing", "write")]
    require(
        all(isinstance(actor, str) and actor for actor in actors) and len(set(actors)) == 4,
        "Four distinct logical actors are required",
    )
    return {"actors": actors}


def _bulletin_brief(inputs: dict[str, Any], observation: WorkflowObservation, case: dict | None, mode: str) -> dict:
    """The brief merges the run's own digest with the supplied marketing material."""
    fixture = _fact_contract("bulletin-market-brief", inputs, case)
    digest = _occurrence(observation, "summarize")
    return _research(observation, digest["text"], inputs["marketing_material"], fixture, mode)


def _priority_research(
    inputs: dict[str, Any], observation: WorkflowObservation, case: dict | None, mode: str
) -> dict | None:
    """High priority earns a grounded brief; normal priority runs no research at all."""
    roles, events, states = observation.roles, observation.events, observation.states
    if inputs["ticket"]["days_overdue"] > 2:
        return _research(
            observation,
            inputs["product_material"],
            inputs["marketing_material"],
            _fact_contract("priority-support-brief", inputs, case),
            mode,
        )
    for role in ("product", "marketing", "combine", "write"):
        sid = roles[role]
        require(events[sid]["status"] == "skipped", "Normal priority executed optional research")
        require(sid not in states[sid]["steps"], "Skipped research leaked output")
        require(states[roles["write"]]["statuses"].get(sid) == "skipped", "Lost skipped research envelope")
    return None


def bulletin_brief_obligations(
    inputs: dict[str, Any], observation: WorkflowObservation, *, case: dict | None = None, mode: str = "live"
) -> dict[str, Callable[[], Any]]:
    """The five named obligations of bulletin-market-brief, in acceptance order.

    `mode` defaults to live: the extra stub equalities are the harness checking
    its own deterministic operations, not an obligation of the brief. Acceptance
    passes the mode it was given, so its behaviour is unchanged.
    """
    return {
        "digest_covers_every_article": lambda: _digest(inputs, observation, case),
        "digest_grounded_in_articles": lambda: _digest_facts(inputs, observation, case),
        "preview_repeats_the_digest": lambda: _preview(observation),
        "four_distinct_actors": lambda: _actors(observation),
        "brief_merges_both_sources": lambda: _bulletin_brief(inputs, observation, case, mode),
    }


def priority_support_obligations(
    inputs: dict[str, Any], observation: WorkflowObservation, *, case: dict | None = None, mode: str = "live"
) -> dict[str, Callable[[], Any]]:
    """The two named obligations of priority-support-brief, in acceptance order."""
    return {
        "routing_single_action": lambda: _routing(inputs, observation),
        "research_matches_priority": lambda: _priority_research(inputs, observation, case, mode),
    }


def support_review_obligations(
    inputs: dict[str, Any], observation: WorkflowObservation
) -> dict[str, Callable[[], dict]]:
    """The three named obligations of support-review-packet, in acceptance order.

    Acceptance calls exactly these and lets the first Rejected propagate, as it
    always has. rubric_facts.py calls them one at a time and reads raised / did
    not raise as one named check each, so neither side restates the other.
    """
    return {
        "routing_single_action": lambda: _routing(inputs, observation),
        "ledger_report": lambda: _ledger(inputs, observation, "invoices", "validate", "total", "report"),
        "review_matches": lambda: _reply_review(inputs, observation),
    }


def check_scenario_business_result(
    scenario: str,
    inputs: dict[str, Any],
    observation: WorkflowObservation,
    mode: str = "stub",
    *,
    case: dict | None = None,
) -> dict[str, Any]:
    roles, events, states = observation.roles, observation.events, observation.states

    def ledger(field: str, validate: str, total: str, report: str) -> dict:
        return _ledger(inputs, observation, field, validate, total, report)

    output = observation.final["output"]
    if scenario == "dual-ledger-closeout":
        expected = {
            name: ledger(name + "_invoices", name + "_validate", name + "_sum", name + "_report")
            for name in ("domestic", "export")
        }
        equal(output, expected, "Wrong ledger packet")
        for name, other in (("domestic", "export"), ("export", "domestic")):
            other_ids = {sid for role, sid in roles.items() if role.startswith(other + "_")}
            for suffix in ("validate", "sum", "report"):
                state = states[roles[name + "_" + suffix]]
                require(not other_ids.intersection(state["statuses"]), "Cross-ledger dependency data")
    elif scenario == "support-review-packet":
        # The three named obligations, run in order; the first failure still raises.
        obligations = support_review_obligations(inputs, observation)
        action = obligations["routing_single_action"]()
        report = obligations["ledger_report"]()
        draft = obligations["review_matches"]()
        equal(
            output,
            {"action": action, "invoice_report": report, **draft},
            "Wrong support review packet",
        )
        if mode == "stub":
            equal(draft["reply"], {"text": "We are checking your order."}, "Wrong one-shot deterministic draft")
        for role in ("select", "report"):
            require(states[roles["draft"]]["statuses"].get(roles[role]) == "completed", "Draft ran before packet join")
    elif scenario == "bulletin-market-brief":
        # The five named obligations, run in order; the first failure still raises.
        obligations = bulletin_brief_obligations(inputs, observation, case=case, mode=mode)
        obligations["digest_covers_every_article"]()
        obligations["digest_grounded_in_articles"]()
        preview = obligations["preview_repeats_the_digest"]()
        obligations["four_distinct_actors"]()
        brief = obligations["brief_merges_both_sources"]()
        equal(output, {"digest": preview, "brief": brief}, "Wrong bulletin packet")
    elif scenario == "priority-support-brief":
        # The two named obligations, run in order; the first failure still raises.
        obligations = priority_support_obligations(inputs, observation, case=case, mode=mode)
        action = obligations["routing_single_action"]()
        brief = obligations["research_matches_priority"]()
        equal(output, {"action": action, "brief": brief}, "Wrong priority support packet")
    else:
        require(False, "Unknown expansion acceptance scenario")
    return {"output_verified": True, "operation_count": len(events), "llm_mode": mode}
