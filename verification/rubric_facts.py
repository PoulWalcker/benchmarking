"""Run facts for the rubric: the seam between acceptance and `rubric.py`; never decides acceptance.

A named check calls the explicitly supplied independent obligation and records whether it held. The
protected narrative goes under "verification", which `rubric._judge_view` never forwards.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING or __package__:
    from .contracts import Rejected, WorkflowObservation
    from .rubric import SCHEMA, Judge, RubricCard, RunFacts, digest, score
else:
    from contracts import Rejected, WorkflowObservation
    from rubric import SCHEMA, Judge, RubricCard, RunFacts, digest, score

Document = dict[str, Any]

NOT_EVALUATED = "not_evaluated"
_NO_JUDGE = "Not scored: this card has judged criteria and no judge is reachable here"
_NO_RUN = "Not scored: no executed case produced the named checks"

# What a malformed run raises past an obligation; the same set verify.corruption_checks treats as rejection.
_REJECTIONS = (Rejected, KeyError, TypeError, IndexError)


def observe(
    scenario: str, inputs: dict[str, Any], observation: WorkflowObservation, *, case: dict | None = None, evaluator
) -> Document:
    """Facts for one executed case; never raises, and reports no facts rather than scoring a card short."""
    try:
        obligations = evaluator.obligations(inputs, observation, case=case) if evaluator.obligations else {}
        checks = {name: _held(obligation) for name, obligation in obligations.items()}
        prose = evaluator.prose(inputs, observation, checks) if evaluator.prose else {}
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
    card: RubricCard | None,
) -> Document | None:
    """This submission's card scored, or why not; None for a scenario with no card. Never raises."""
    if card is None:
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


def _held(obligation: Any) -> bool:
    try:
        obligation()
    except _REJECTIONS:
        return False
    return True
