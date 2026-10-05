"""Independent business obligations for the four bounded expansion scenarios.

These checks consume logical observations only. They import neither compiler nor
operation implementations; native execution and submitted origins are checked at
separate seams. Source facts are an explicitly bounded fixture lexical contract.
"""

import json
import re
from pathlib import Path
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING or __package__:
    from .contracts import WorkflowObservation, equal, require
else:
    from contracts import WorkflowObservation, equal, require


def check_scenario_business_result(
    scenario: str,
    inputs: dict[str, Any],
    observation: WorkflowObservation,
    mode: str = "stub",
    *,
    case: dict | None = None,
) -> dict[str, Any]:
    roles, events, states = observation.roles, observation.events, observation.states

    def value(role: str) -> dict:
        sid = roles[role]
        require(events[sid]["status"] == "completed", "Required occurrence did not complete: " + role)
        require(sid in states[sid]["steps"], "Missing occurrence output: " + role)
        return states[sid]["steps"][sid]

    def ledger(field: str, validate: str, total: str, report: str) -> dict:
        invoices = inputs[field]
        expected = {
            "total_minor": sum(item["amount_minor"] for item in invoices),
            "currency": invoices[0]["currency"],
            "invoice_count": len(invoices),
        }
        equal(value(validate), {"invoices": invoices}, "Validation changed ledger invoices")
        equal(
            value(total),
            {
                "amount_minor": expected["total_minor"],
                "currency": expected["currency"],
                "count": expected["invoice_count"],
            },
            "Wrong ledger intermediate sum",
        )
        equal(value(report), expected, "Wrong ledger report")
        return expected

    def routing() -> dict:
        high = inputs["ticket"]["days_overdue"] > 2
        active, skipped = ("escalate", "normal") if high else ("normal", "escalate")
        equal(
            value("classify"),
            {"category": "delivery", "priority": "high" if high else "normal"},
            "Wrong classification",
        )
        require(events[roles[skipped]]["status"] == "skipped", "Wrong action branch executed")
        expected = {
            "ticket_id": inputs["ticket"]["id"],
            "action": "escalate" if high else "normal_reply",
            "mode": "draft",
        }
        equal(value(active), expected, "Wrong selected action")
        equal(value("select"), expected, "Wrong action selection")
        require(roles[skipped] not in states[roles["select"]]["steps"], "Skipped action leaked output")
        require(states[roles["select"]]["statuses"].get(roles[skipped]) == "skipped", "Missing skipped action status")
        return expected

    def facts(text: str, anchors: list, label: str, forbidden: list | tuple = ()) -> None:
        require(isinstance(text, str) and text.strip(), "Empty " + label)
        for alternatives in anchors:
            require(
                any(re.search(pattern, text, re.IGNORECASE) for pattern in alternatives),
                label + " omitted a required source fact: " + " / ".join(alternatives),
            )
        for pattern in forbidden:
            require(
                not re.search(pattern, text, re.IGNORECASE), label + " invented a controlled absent fact: " + pattern
            )

    def fact_contract() -> dict:
        if case is not None:
            equal(case.get("inputs"), inputs, "Independent fixture inputs differ")
            return case
        fixtures = json.loads((Path(__file__).parent / "cases.json").read_text())[scenario]["positive"]
        matches = [item for item in fixtures if item["inputs"] == inputs]
        require(matches, "No independent source fact contract for these inputs")
        return matches[0]

    def research(product_source: str, marketing_source: str, fixture: dict) -> dict:
        product, marketing = value("product"), value("marketing")
        for name, analysis, source in (
            ("product", product, product_source),
            ("marketing", marketing, marketing_source),
        ):
            require(set(analysis) == {"summary", "evidence"}, "Invalid analysis fields")
            require(
                isinstance(analysis["evidence"], str)
                and analysis["evidence"].strip()
                and analysis["evidence"] in source,
                "Analysis evidence is absent from its actual source",
            )
            facts(
                analysis["summary"],
                fixture[name + "_anchors"],
                name + " analysis",
                fixture.get("forbidden_anchors", []),
            )
            other = "marketing" if name == "product" else "product"
            require(roles[other] not in states[roles[name]]["statuses"], "Research analyses are chained")
            if mode == "stub":
                equal(
                    analysis,
                    {"summary": name.title() + ": " + source, "evidence": source},
                    "Wrong deterministic analysis",
                )
        equal(value("combine"), {"product": product, "marketing": marketing}, "Join changed analysis or evidence")
        for role in ("product", "marketing"):
            joined = states[roles["combine"]]
            require(joined["statuses"].get(roles[role]) == "completed", "Join lost completed analysis status")
            equal(joined["steps"].get(roles[role]), value(role), "Join lost analysis output")
        brief = value("write")
        require(set(brief) == {"report", "evidence"}, "Invalid brief output")
        equal(brief["evidence"], [product["evidence"], marketing["evidence"]], "Brief changed source evidence order")
        facts(
            brief["report"],
            fixture["product_anchors"] + fixture["marketing_anchors"],
            "brief",
            fixture.get("forbidden_anchors", []),
        )
        if mode == "stub":
            equal(brief["report"], product["summary"] + "\n\n" + marketing["summary"], "Wrong deterministic brief")
        return brief

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
        action = routing()
        report = ledger("invoices", "validate", "total", "report")
        reply = value("draft")
        require(set(reply) == {"text"} and isinstance(reply["text"], str) and reply["text"], "Invalid reply draft")
        errors = []
        if inputs["required_order_id"] not in reply["text"]:
            errors.append("Include the order ID")
        # JS string length counts UTF-16 code units, including surrogate pairs.
        if len(reply["text"].encode("utf-16-le", errors="surrogatepass")) // 2 > inputs["max_characters"]:
            errors.append("Shorten the reply")
        review = {"pass": not errors, "errors": errors}
        equal(value("check"), review, "Dishonest reply review")
        equal(
            output,
            {"action": action, "invoice_report": report, "reply": reply, "review": review},
            "Wrong support review packet",
        )
        if mode == "stub":
            equal(reply, {"text": "We are checking your order."}, "Wrong one-shot deterministic draft")
        for role in ("select", "report"):
            require(states[roles["draft"]]["statuses"].get(roles[role]) == "completed", "Draft ran before packet join")
    elif scenario == "bulletin-market-brief":
        fixture = fact_contract()
        equal(value("prepare"), {"articles": inputs["articles"]}, "Preparation changed articles")
        digest = value("summarize")
        require(set(digest) == {"text", "article_ids"}, "Invalid digest fields")
        ids = digest["article_ids"]
        require(isinstance(ids, list) and all(isinstance(item, str) for item in ids), "Invalid digest article IDs")
        require(len(ids) == len(set(ids)), "Duplicate digest article ID")
        equal(sorted(ids), sorted(item["id"] for item in inputs["articles"]), "Unknown or missing digest article ID")
        facts(digest["text"], fixture["product_anchors"], "digest", fixture.get("forbidden_anchors", []))
        preview = {"mode": "preview", **digest}
        equal(value("preview"), preview, "Preview changed digest")
        actors = [events[roles[role]].get("actor") for role in ("summarize", "product", "marketing", "write")]
        require(
            all(isinstance(actor, str) and actor for actor in actors) and len(set(actors)) == 4,
            "Four distinct logical actors are required",
        )
        brief = research(digest["text"], inputs["marketing_material"], fixture)
        equal(output, {"digest": preview, "brief": brief}, "Wrong bulletin packet")
    elif scenario == "priority-support-brief":
        action = routing()
        if inputs["ticket"]["days_overdue"] > 2:
            brief = research(inputs["product_material"], inputs["marketing_material"], fact_contract())
        else:
            brief = None
            for role in ("product", "marketing", "combine", "write"):
                sid = roles[role]
                require(events[sid]["status"] == "skipped", "Normal priority executed optional research")
                require(sid not in states[sid]["steps"], "Skipped research leaked output")
                require(states[roles["write"]]["statuses"].get(sid) == "skipped", "Lost skipped research envelope")
        equal(output, {"action": action, "brief": brief}, "Wrong priority support packet")
    else:
        require(False, "Unknown expansion acceptance scenario")
    return {"output_verified": True, "operation_count": len(events), "llm_mode": mode}
