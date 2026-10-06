"""Run facts for the rubric: named checks, judge prose, and one evaluation.

This is the seam between acceptance and `rubric.py`. `verify.py` calls `observe`
once per executed case while it still holds the engine-neutral observation, then
`evaluate` once per submission; `rubric.score` does the scoring and `verify.py`
writes what comes back to `evaluation.json`.

Nothing here decides acceptance and nothing here reaches `reward.txt`. A named
check reports whether an obligation that `scenario_business.py` already states
held on this run: the obligation is called, and raised / did not raise becomes
false / true. The rule is never restated here, so a check cannot drift from the
acceptance it describes.

Prose is the run's own text, formatted for a reader. The protected narrative is
supplied under "verification", which no llm criterion may declare and which
`rubric._judge_view` therefore never forwards.

A scenario is added by writing one function and one branch in `_checks` and
`_prose`. There is deliberately no registry: a reader follows the branch.
"""

from __future__ import annotations

import json
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING or __package__:
    from .contracts import Rejected, WorkflowObservation
    from .rubric import SCHEMA, Judge, RubricCard, RubricError, RunFacts, digest, score
    from .rubric_cards import card_for
    from .scenario_business import support_review_obligations
else:  # Harbor runs the distributed verifier as a standalone script.
    from contracts import Rejected, WorkflowObservation
    from rubric import SCHEMA, Judge, RubricCard, RubricError, RunFacts, digest, score
    from rubric_cards import card_for
    from scenario_business import support_review_obligations

Document = dict[str, Any]

NOT_EVALUATED = "not_evaluated"
_NO_JUDGE = "Not scored: this card has judged criteria and no judge is reachable here"
_NO_RUN = "Not scored: no executed case produced the named checks"

# What a malformed run throws on its way past an obligation, matching the set
# verify.corruption_checks already treats as a rejection. Collecting facts must
# never raise into acceptance, and never reports an unmeasured check as true.
_REJECTIONS = (Rejected, KeyError, TypeError, IndexError)


def observe(
    scenario: str, inputs: dict[str, Any], observation: WorkflowObservation, *, case: dict | None = None
) -> Document:
    """Facts for one executed case. Called before acceptance and never raises."""
    checks = _checks(scenario, inputs, observation)
    return {"case": (case or {}).get("name"), "checks": checks, "prose": _prose(scenario, inputs, observation, checks)}


def evaluate(
    scenario: str,
    runs: list[Document],
    *,
    accepted: bool,
    execution_pass: bool,
    judge: Judge | None = None,
) -> Document | None:
    """Score this submission's card, or say plainly why it was not scored.

    Returns None when the scenario has no card, so most scenarios write nothing.
    Never raises: a rubric that cannot be computed is reported in its own
    document, because the rubric must not turn an accepted run into a failure.
    """
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
    except Exception as error:
        return _not_evaluated(card, "Not scored: " + type(error).__name__ + ": " + str(error), checks, execution_pass)


def _not_evaluated(card: RubricCard, reason: str, checks: dict[str, bool], execution_pass: bool) -> Document:
    """The fourth status, and the only one this module writes itself.

    `rubric.score` returns complete, unscored or judge_failed. This one says the
    scoring never ran, so no total is stated: a withheld score is null, never a
    zero, and never a figure nothing computed.
    """
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


def _checks(scenario: str, inputs: dict[str, Any], observation: WorkflowObservation) -> dict[str, bool]:
    if scenario == "support-review-packet":
        obligations = support_review_obligations(inputs, observation)
        return {name: _held(obligation) for name, obligation in obligations.items()}
    return {}


def _prose(
    scenario: str, inputs: dict[str, Any], observation: WorkflowObservation, checks: dict[str, bool]
) -> dict[str, str]:
    if scenario == "support-review-packet":
        return _support_review_prose(inputs, observation, checks)
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
        # Protected: no llm criterion may declare "verification", so _judge_view
        # withholds this rather than a judge reading the checks off it.
        "verification": "Named checks: "
        + ", ".join(name + (" held" if held else " failed") for name, held in sorted(checks.items())),
    }


def _mapping(value: Any, key: str) -> dict[str, Any]:
    """Reach one nested mapping of a run that may be any shape at all."""
    if isinstance(value, dict):
        inner = value.get(key)
    else:
        inner = getattr(value, key, None) if value is not None else None
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
