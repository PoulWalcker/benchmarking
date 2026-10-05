"""Task obligations stated independently of submitted YAML or runtime output.

Role names are verifier vocabulary only. Authors can use any unambiguous step IDs
and list order; the generic binder checks operation, source, graph and output.
"""

from typing import Any


def ref(path: str, *, optional: bool = False) -> dict:
    return {"optional_ref" if optional else "ref": path}


def role(operation: str, inputs: dict, *, when: dict | None = None) -> dict:
    result = {
        "operation": operation,
        "kind": "LLM"
        if operation
        in {
            "ticket.classify",
            "reply.generate",
            "digest.summarize",
            "research.product",
            "research.marketing",
            "research.write",
        }
        else "Script",
        "inputs": inputs,
    }
    if when is not None:
        result["when"] = when
    return result


def routing() -> dict:
    return {
        "classify": role("ticket.classify", {"ticket": ref("inputs.ticket")}),
        "escalate": role(
            "ticket.escalation_draft",
            {"ticket": ref("inputs.ticket")},
            when={"ref": "steps.classify.priority", "eq": "high"},
        ),
        "normal": role(
            "ticket.normal_draft",
            {"ticket": ref("inputs.ticket")},
            when={"ref": "steps.classify.priority", "eq": "normal"},
        ),
        "select": role(
            "branch.select_one",
            {"escalated": ref("steps.escalate", optional=True), "normal": ref("steps.normal", optional=True)},
        ),
    }


ROUTING_EDGES = [("classify", "escalate"), ("classify", "normal"), ("escalate", "select"), ("normal", "select")]

CONTRACTS: dict[str, dict[str, Any]] = {
    "dual-ledger-closeout": {
        "roles": {
            "domestic_validate": role("invoices.validate", {"invoices": ref("inputs.domestic_invoices")}),
            "domestic_sum": role("invoices.sum", {"invoices": ref("steps.domestic_validate.invoices")}),
            "domestic_report": role("invoices.report", {"total": ref("steps.domestic_sum")}),
            "export_validate": role("invoices.validate", {"invoices": ref("inputs.export_invoices")}),
            "export_sum": role("invoices.sum", {"invoices": ref("steps.export_validate.invoices")}),
            "export_report": role("invoices.report", {"total": ref("steps.export_sum")}),
        },
        "edges": [
            ("domestic_validate", "domestic_sum"),
            ("domestic_sum", "domestic_report"),
            ("export_validate", "export_sum"),
            ("export_sum", "export_report"),
        ],
        "output": {"domestic": ref("steps.domestic_report"), "export": ref("steps.export_report")},
    },
    "support-review-packet": {
        "roles": {
            **routing(),
            "validate": role("invoices.validate", {"invoices": ref("inputs.invoices")}),
            "total": role("invoices.sum", {"invoices": ref("steps.validate.invoices")}),
            "report": role("invoices.report", {"total": ref("steps.total")}),
            "draft": role(
                "reply.generate",
                {
                    "ticket": {"id": ref("inputs.ticket.id"), "text": ref("inputs.ticket.text")},
                    "previous": None,
                    "feedback": [],
                },
            ),
            "check": role(
                "reply.check",
                {
                    "draft": ref("steps.draft"),
                    "order_id": ref("inputs.required_order_id"),
                    "max_characters": ref("inputs.max_characters"),
                },
            ),
        },
        "edges": ROUTING_EDGES
        + [("validate", "total"), ("total", "report"), ("select", "draft"), ("report", "draft"), ("draft", "check")],
        "output": {
            "action": ref("steps.select"),
            "invoice_report": ref("steps.report"),
            "reply": ref("steps.draft"),
            "review": ref("steps.check"),
        },
    },
    "bulletin-market-brief": {
        "exclusive_actors": ["summarize", "product", "marketing", "write"],
        "roles": {
            "prepare": role("digest.prepare", {"articles": ref("inputs.articles")}),
            "summarize": role("digest.summarize", {"articles": ref("steps.prepare.articles")}),
            "preview": role("digest.preview", {"summary": ref("steps.summarize")}),
            "product": role("research.product", {"material": ref("steps.summarize.text")}),
            "marketing": role("research.marketing", {"material": ref("inputs.marketing_material")}),
            "combine": role("research.combine", {"product": ref("steps.product"), "marketing": ref("steps.marketing")}),
            "write": role("research.write", {"brief": ref("steps.combine")}),
        },
        "edges": [
            ("prepare", "summarize"),
            ("summarize", "preview"),
            ("summarize", "product"),
            ("product", "combine"),
            ("marketing", "combine"),
            ("combine", "write"),
        ],
        "output": {"digest": ref("steps.preview"), "brief": ref("steps.write")},
    },
    "priority-support-brief": {
        "roles": {
            **routing(),
            "product": role(
                "research.product",
                {"material": ref("inputs.product_material")},
                when={"ref": "steps.classify.priority", "eq": "high"},
            ),
            "marketing": role(
                "research.marketing",
                {"material": ref("inputs.marketing_material")},
                when={"ref": "steps.classify.priority", "eq": "high"},
            ),
            "combine": role(
                "research.combine",
                {"product": ref("steps.product", optional=True), "marketing": ref("steps.marketing", optional=True)},
                when={"ref": "steps.classify.priority", "eq": "high"},
            ),
            "write": role(
                "research.write",
                {"brief": ref("steps.combine", optional=True)},
                when={"ref": "steps.classify.priority", "eq": "high"},
            ),
        },
        "edges": ROUTING_EDGES
        + [
            ("select", "product"),
            ("select", "marketing"),
            ("product", "combine"),
            ("marketing", "combine"),
            ("combine", "write"),
        ],
        "output": {"action": ref("steps.select"), "brief": ref("steps.write", optional=True)},
    },
}
