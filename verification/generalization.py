"""Finite semantic composition checks, independent of compiler/business helpers.

Origins are symbolic input/operation values, not comparisons to a reference DAG.
Native graph evidence is checked against the submitted graph separately.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Any, TYPE_CHECKING
import re

if TYPE_CHECKING or __package__:
    from .contracts import WorkflowObservation, equal, require
    from .n8n_provenance import check_graph_evidence, check_provenance, live_operations, one_run
    from .roles import resolve
else:
    from contracts import WorkflowObservation, equal, require
    from n8n_provenance import check_graph_evidence, check_provenance, live_operations, one_run
    from roles import resolve


@dataclass(frozen=True)
class Origin:
    stage: str
    occurrence: str
    field: str = ""


def facts(text, anchors, forbidden=()):
    require(isinstance(text, str) and bool(text.strip()), "Missing substantive source text")
    for alternatives in anchors:
        require(
            any(re.search(pattern, text, re.IGNORECASE) for pattern in alternatives),
            "A required source fact is missing",
        )
        # Bounded contradiction controls, not a general natural-language entailment judge.
        for sentence in re.split(r"[.!?;\n]", text):
            if any(re.search(pattern, sentence, re.IGNORECASE) for pattern in alternatives):
                require(
                    not re.search(
                        r"\b(?:does not|doesn't|do not|don't|cannot|can't|never)\s+(?:support|offer|provide|include|enable|allow)\b|\b(?:lacks?|unavailable)\b",
                        sentence,
                        re.IGNORECASE,
                    ),
                    "A source fact is explicitly contradicted",
                )
    require(
        not any(re.search(pattern, text, re.IGNORECASE) for pattern in forbidden),
        "An absent controlled fact was introduced",
    )


def analyze(config: dict, case: dict, values: dict | None = None) -> dict:
    """Prove business origins; optional observed values additionally prove results."""
    workflow = config["workflow"]
    task, inputs = workflow["id"], case["inputs"]
    require(task in {"billing-bulletin-packet", "two-audience-briefs"}, "Unknown composition task")
    steps = {step["id"]: step for step in workflow["steps"]}
    require(len(steps) == len(workflow["steps"]), "Duplicate occurrence ID")
    predecessors = {sid: {a for a, b in workflow["dependencies"] if b == sid} for sid in steps}
    require(all(parent in steps for parents in predecessors.values() for parent in parents), "Unknown dependency")
    symbolic_inputs = {field: Origin("input", field) for field in inputs}
    symbols: dict[str, Any] = {}
    pending = dict(steps)
    analyses: dict[str, str] = {}
    writers: dict[str, str] = {}
    writer_sources: dict[str, set[str]] = {}
    used_models = set()
    digest_step = None
    counts = Counter(step["uses"] for step in steps.values())
    allowed = (
        {"invoices.validate", "invoices.sum", "invoices.report", "digest.prepare", "digest.summarize", "digest.preview"}
        if task == "billing-bulletin-packet"
        else {"research.product", "research.marketing", "research.combine", "research.write"}
    )
    require(set(counts) <= allowed, "Unrequested catalog operation")
    if task == "billing-bulletin-packet":
        require(counts["digest.summarize"] == 1, "Packet requires exactly one digest")
    else:
        require(
            sum(counts[op] for op in ("research.product", "research.marketing", "research.write")) <= 6,
            "Research exceeds the public six-call ceiling",
        )
    while pending:
        ready = [sid for sid in pending if predecessors[sid] <= symbols.keys()]
        require(ready, "Cyclic or unresolvable submitted graph")
        for sid in ready:
            step = pending.pop(sid)
            operation = step["uses"]
            args = resolve(step["with"], symbolic_inputs, symbols, {s: "completed" for s in symbols})
            actual = (
                resolve(step["with"], inputs, values, {s: "completed" if s in values else "skipped" for s in steps})
                if values is not None and sid in values
                else None
            )
            model = operation in {"digest.summarize", "research.product", "research.marketing", "research.write"}
            equal(step["kind"], "LLM" if model else "Script", "Wrong operation kind")
            actor = step.get("actor")
            if model and (actor is not None or task == "two-audience-briefs"):
                require(isinstance(actor, str) and bool(actor), "Model operation requires its declared logical actor")
                equal(
                    config.get("actors", {}).get(actor, {}).get("allowed_operations"),
                    [operation],
                    "Actor permissions exceed its operation",
                )
            if operation == "invoices.validate":
                equal(args, {"invoices": Origin("input", "invoices")}, "Invoice validation bypassed supplied input")
                symbols[sid] = {"invoices": Origin("validated", "invoices")}
                if values is not None and sid in values:
                    equal(values[sid], {"invoices": inputs["invoices"]}, "Validation changed invoices")
            elif operation == "invoices.sum":
                equal(args, {"invoices": Origin("validated", "invoices")}, "Invoice sum bypassed validation")
                symbols[sid] = {
                    key: Origin("invoice_total", "invoices", key) for key in ("amount_minor", "currency", "count")
                }
                if values is not None and sid in values:
                    equal(
                        values[sid],
                        {
                            "amount_minor": sum(i["amount_minor"] for i in inputs["invoices"]),
                            "currency": inputs["invoices"][0]["currency"],
                            "count": len(inputs["invoices"]),
                        },
                        "Incorrect invoice sum",
                    )
            elif operation == "invoices.report":
                expected = {
                    key: Origin("invoice_total", "invoices", key) for key in ("amount_minor", "currency", "count")
                }
                equal(args, {"total": expected}, "Invoice report bypassed computed total")
                symbols[sid] = {
                    "total_minor": expected["amount_minor"],
                    "currency": expected["currency"],
                    "invoice_count": expected["count"],
                }
                if values is not None and sid in values:
                    assert actual is not None
                    total = actual["total"]
                    equal(
                        values[sid],
                        {
                            "total_minor": total["amount_minor"],
                            "currency": total["currency"],
                            "invoice_count": total["count"],
                        },
                        "Report changed its total",
                    )
            elif operation == "digest.prepare":
                equal(args, {"articles": Origin("input", "articles")}, "Digest preparation bypassed supplied articles")
                symbols[sid] = {"articles": Origin("prepared", "articles")}
                if values is not None and sid in values:
                    equal(values[sid], {"articles": inputs["articles"]}, "Preparation changed articles")
            elif operation == "digest.summarize":
                equal(args, {"articles": Origin("prepared", "articles")}, "Digest bypassed prepared articles")
                symbols[sid] = {key: Origin("digest", sid, key) for key in ("text", "article_ids")}
                digest_step = sid
                used_models.add(sid)
                if values is not None and sid in values:
                    equal(set(values[sid]), {"text", "article_ids"}, "Invalid digest fields")
                    ids = values[sid]["article_ids"]
                    require(isinstance(ids, list) and all(isinstance(item, str) for item in ids), "Invalid digest IDs")
                    require(len(ids) == len(set(ids)), "Duplicate digest ID")
                    equal(
                        sorted(ids), sorted(a["id"] for a in inputs["articles"]), "Digest lost or invented article IDs"
                    )
                    facts(values[sid]["text"], case["anchors"], case.get("forbidden", []))
            elif operation == "digest.preview":
                require(digest_step is not None, "Preview has no actual digest")
                assert digest_step is not None
                equal(args, {"summary": symbols[digest_step]}, "Preview bypassed actual digest")
                symbols[sid] = {"mode": "preview", **args["summary"]}
                if values is not None and sid in values:
                    assert actual is not None
                    equal(values[sid], {"mode": "preview", **actual["summary"]}, "Preview changed summary")
            elif operation in {"research.product", "research.marketing"}:
                source = args.get("material")
                require(
                    isinstance(source, Origin) and source.stage == "input",
                    "Research replaced an input with a literal or derived source",
                )
                allowed_sources = (
                    {"product_material"}
                    if operation == "research.product"
                    else {"cooperatives_material", "clinics_material"}
                )
                require(source.occurrence in allowed_sources, "Research analyzed the wrong source role")
                equal(set(args), {"material"}, "Unexpected research input")
                analyses[sid] = source.occurrence
                symbols[sid] = {key: Origin("analysis", sid, key) for key in ("summary", "evidence")}
                if values is not None and sid in values:
                    analysis = values[sid]
                    equal(set(analysis), {"summary", "evidence"}, "Invalid analysis fields")
                    require(
                        isinstance(analysis["evidence"], str)
                        and analysis["evidence"].strip()
                        and analysis["evidence"] in inputs[source.occurrence],
                        "Analysis evidence is unsupported",
                    )
                    facts(analysis["summary"], case["anchors"][source.occurrence], case.get("forbidden", []))
            elif operation == "research.combine":
                equal(set(args), {"product", "marketing"}, "Incomplete research pair")
                symbols[sid] = args
                if values is not None and sid in values:
                    equal(values[sid], actual, "Combination changed source analyses")
            else:
                require(
                    set(args) == {"brief"}
                    and isinstance(args["brief"], dict)
                    and set(args["brief"]) == {"product", "marketing"},
                    "Writer omitted an analysis",
                )
                sources = {}
                for role in ("product", "marketing"):
                    analysis = args["brief"][role]
                    token = analysis.get("summary") if isinstance(analysis, dict) else None
                    require(isinstance(token, Origin) and token.stage == "analysis", "Writer bypassed actual analysis")
                    assert isinstance(token, Origin)
                    equal(analysis, symbols[token.occurrence], "Writer mixed unrelated analysis fields")
                    field = analyses[token.occurrence]
                    require(
                        field == "product_material"
                        if role == "product"
                        else field in {"cooperatives_material", "clinics_material"},
                        "Writer swapped product and audience",
                    )
                    sources[role] = (token.occurrence, field)
                branch = sources["marketing"][1].removesuffix("_material")
                writers[sid] = branch
                writer_sources[sid] = {sources[role][0] for role in ("product", "marketing")}
                symbols[sid] = {key: Origin("writer", sid, key) for key in ("report", "evidence")}
                if values is not None and sid in values:
                    written = values[sid]
                    equal(set(written), {"report", "evidence"}, "Invalid report fields")
                    product, marketing = (values[sources[role][0]] for role in ("product", "marketing"))
                    equal(
                        written["evidence"],
                        [product["evidence"], marketing["evidence"]],
                        "Writer changed audience/source evidence",
                    )
                    other = "clinics_material" if branch == "cooperatives" else "cooperatives_material"
                    forbidden = case.get("forbidden", []) + [p for choices in case["anchors"][other] for p in choices]
                    facts(
                        written["report"],
                        case["anchors"]["product_material"] + case["anchors"][sources["marketing"][1]],
                        forbidden,
                    )
    output = resolve(workflow["output"], symbolic_inputs, symbols, {s: "completed" for s in steps})
    if task == "billing-bulletin-packet":
        require(digest_step is not None, "Missing digest")
        assert digest_step is not None
        expected_output = {
            "billing": {
                "total_minor": Origin("invoice_total", "invoices", "amount_minor"),
                "currency": Origin("invoice_total", "invoices", "currency"),
                "invoice_count": Origin("invoice_total", "invoices", "count"),
            },
            "update": {"mode": "preview", **symbols[digest_step]},
        }
    else:
        require(
            isinstance(output, dict) and set(output) == {"cooperatives", "clinics"}, "An audience report is missing"
        )
        expected_output = {}
        for branch, fields in output.items():
            token = fields.get("report") if isinstance(fields, dict) else None
            require(isinstance(token, Origin) and token.stage == "writer", "Final output bypassed writer origins")
            assert isinstance(token, Origin)
            require(writers.get(token.occurrence) == branch, "Final output swapped audience origins")
            expected_output[branch] = symbols[token.occurrence]
            used_models.add(token.occurrence)
            used_models.update(writer_sources[token.occurrence])
    equal(output, expected_output, "Final output bypassed required input/operation origins")
    if values is not None:
        require(used_models <= values.keys(), "Required model output was skipped or unavailable")
    result = {
        "model_occurrences": {sid: step["uses"] for sid, step in steps.items() if step["kind"] == "LLM"},
        "semantic_origins_verified": True,
        "unused_model_occurrences": sorted({sid for sid, step in steps.items() if step["kind"] == "LLM"} - used_models),
    }
    if values is not None:
        if task == "billing-bulletin-packet":
            assert digest_step is not None
            result["expected_output"] = {
                "billing": {
                    "total_minor": sum(i["amount_minor"] for i in inputs["invoices"]),
                    "currency": inputs["invoices"][0]["currency"],
                    "invoice_count": len(inputs["invoices"]),
                },
                "update": {"mode": "preview", **values[digest_step]},
            }
        else:
            result["expected_output"] = {
                branch: values[fields["report"].occurrence] for branch, fields in output.items()
            }
    return result


def verify_execution(config: dict, case: dict, run: dict, *, mode="stub") -> dict:
    """Check native execution plus independently calculated values and provenance."""
    check_provenance(run)
    require(
        run.get("status") == run.get("persisted_status") == "success" and run.get("execute_exit_code") == 0,
        "Native workflow failed",
    )
    _, final = one_run(run, "Result")
    equal(run.get("result"), final, "Adapter Result differs")
    equal(run.get("output"), final.get("output"), "Adapter output differs")
    equal(final.get("llm_mode"), mode, "Wrong model mode")
    equal(final.get("spec_revision"), "06ddd3333109cea8a2cb3071609070d7a3c0d3ff", "Wrong specification pin")
    _, fixture = one_run(run, "Fixture")
    equal(fixture.get("inputs"), case["inputs"], "Native fixture differs from private inputs")
    steps = {step["id"]: step for step in config["workflow"]["steps"]}
    trace = final.get("trace", [])
    events = {event["step_id"]: event for event in trace}
    require(len(trace) == len(events), "Repeated occurrence in trace")
    equal(set(events), set(steps), "Missing/extra operation execution")
    equal(set(run["mapping"]), set(steps), "Wrong execution mapping")
    states = {}
    for sid, step in steps.items():
        _, state = one_run(run, run["mapping"][sid])
        event = events[sid]
        equal(event.get("operation"), step["uses"], "Wrong executed operation")
        require(event.get("status") in {"completed", "skipped"}, "Invalid operation status")
        require(
            (sid in state.get("steps", {})) == (event["status"] == "completed"),
            "Skipped or completed value presence differs",
        )
        equal(
            event.get("implementation"), mode if step["kind"] == "LLM" else "script", "Wrong operation implementation"
        )
        equal(state.get("inputs"), case["inputs"], "Operation lost fixture inputs")
        equal(state.get("events", {}).get(sid), event, "Final trace differs from native operation")
        states[sid] = state
    equal(final.get("statuses"), {sid: events[sid]["status"] for sid in steps}, "Final statuses differ")
    equal(
        final.get("steps"),
        {sid: states[sid]["steps"][sid] for sid in steps if events[sid]["status"] == "completed"},
        "Final step values differ",
    )
    observation = WorkflowObservation(final, events, states, {})
    check_graph_evidence(config, case["inputs"], run, observation, mode)
    checked = analyze(config, case, final["steps"])
    equal(final["output"], checked.pop("expected_output"), "Final business values differ")
    calls = live_operations(run) if mode == "live" else []
    return {**checked, "passed": True, "calls": calls, "native_execution_verified": True}
