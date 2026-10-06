"""Bounded experiment selection; registration never extends workflow semantics."""

from collections.abc import Iterable

BASELINE_SCENARIOS = {
    "invoice-total": "01-invoice-total.yaml",
    "ticket-routing": "02-ticket-routing.yaml",
    "competitor-report": "03-competitor-report.yaml",
}
EXPANSION_SCENARIOS = {
    "dual-ledger-closeout": "06-dual-ledger-closeout.yaml",
    "support-review-packet": "07-support-review-packet.yaml",
    "bulletin-market-brief": "08-bulletin-market-brief.yaml",
    "priority-support-brief": "09-priority-support-brief.yaml",
}
EXTENSION_SCENARIOS = {"revise-answer": "04-revise-answer.yaml"}
LIFECYCLE_SCENARIOS = {"daily-digest": "05-digest-lifecycle.yaml"}
SCENARIOS = {**BASELINE_SCENARIOS, **EXPANSION_SCENARIOS, **EXTENSION_SCENARIOS, **LIFECYCLE_SCENARIOS}


def select_scenarios(names: Iterable[str] | None = None) -> dict[str, str]:
    selected = tuple(BASELINE_SCENARIOS if names is None else names)
    if not selected or len(set(selected)) != len(selected) or any(name not in SCENARIOS for name in selected):
        raise ValueError("Unknown, duplicate, or empty scenario selection")
    return {name: SCENARIOS[name] for name in selected}
