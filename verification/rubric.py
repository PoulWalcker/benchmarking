"""Weighted rubric scoring beside binary acceptance; pure, so callers own persistence and dispatch.

Rules enforced here:
- Answer values are exactly {"yes": 1, "maybe": 0.33, "no": 0}; no card can reweight an answer.
- A judge sees prose, references and the run digest only: never the execution flag, the
  deterministic checks or the protected verification narrative.
- At most one judge dispatch per run (one call site, no loop); none for a run that did not execute.
- The card is re-validated after the judge returns, since `object.__setattr__` defeats `frozen=True`.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
import hashlib
import json
from types import MappingProxyType
from typing import Any, Literal, Protocol

Answer = Literal["yes", "maybe", "no"]
Origin = Literal["upstream", "local"]
Document = dict[str, Any]

SCHEMA = "sapi-lab-rubric-evaluation/v1"
PROMPT_VERSION = "1.0.1"
ANSWER_VALUES: Mapping[Answer, float] = {"yes": 1.0, "maybe": 0.33, "no": 0.0}

# Decimal, so a total never acquires a binary-float remainder; the card's own table is only validated.
_ANSWER_POINTS: Mapping[Answer, Decimal] = {"yes": Decimal("1"), "maybe": Decimal("0.33"), "no": Decimal("0")}
_EVIDENCE_SOURCES = frozenset({"environment", "candidate", "verification", "engine"})
_PROTECTED_SOURCE = "verification"
_ATTRIBUTION_KEYS = ("mode", "model", "prompt_version", "prompt_digest", "response_digest")
_NOT_CONSULTED = "No judge was asked: the run did not execute"
_NOT_ANSWERED = "The judge was asked once and returned no usable answer"
_CARD_CHANGED = "Not scored: the rubric card changed while the judge held it"
_DETERMINED = "Protected environment verification"


def digest(value: Any) -> str:
    """Canonical ASCII-escaped JSON digest, copied from evaluate.autowfbench so verification/ imports nothing.

    A test pins the two together. lifecycle.py's `ensure_ascii=False` digest is deliberately different.
    """
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


class RubricError(ValueError):
    """The card, the run facts or the request is unusable; no score was computed."""


def _sealed(mapping: Mapping[Any, Any]) -> Any:
    """Copy and seal against ordinary writes; `score` still re-checks the card after the judge returns."""
    return MappingProxyType(dict(mapping))


def _sealed_refs(refs: Mapping[str, tuple[str, ...]]) -> Any:
    """Seal a refs mapping. One `str` is refused, never spread into characters."""
    sealed = {}
    for key, value in refs.items():
        if isinstance(value, str):
            raise RubricError("References for " + key + " must be a sequence of strings, not one string")
        sealed[key] = tuple(value)
    return _sealed(sealed)


@dataclass(frozen=True)
class Criterion:
    """One scored question. Its evaluator decides who may answer it, forever."""

    id: str
    question: str
    weight: int
    evaluator: Literal["deterministic", "llm"]
    check_id: str = ""
    anchors: Mapping[Answer, str] = field(default_factory=dict)
    required_evidence: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "anchors", _sealed(self.anchors))
        object.__setattr__(self, "required_evidence", tuple(self.required_evidence))


@dataclass(frozen=True)
class RubricCard:
    """A weighted rubric with a stated origin. Cards are data, not behaviour."""

    id: str
    version: str
    origin: Origin
    criteria: tuple[Criterion, ...]
    answer_values: Mapping[Answer, float] = field(default_factory=lambda: dict(ANSWER_VALUES))
    prompt_version: str = PROMPT_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "criteria", tuple(self.criteria))
        object.__setattr__(self, "answer_values", _sealed(self.answer_values))

    def digest(self) -> str:
        """Covers every field the document prints beside it, origin and prompt version included."""
        return digest(
            {
                "id": self.id,
                "version": self.version,
                "origin": self.origin,
                "prompt_version": self.prompt_version,
                "answer_values": dict(self.answer_values),
                "criteria": [
                    {
                        "id": criterion.id,
                        "question": criterion.question,
                        "weight": criterion.weight,
                        "evaluator": criterion.evaluator,
                        "check_id": criterion.check_id,
                        "anchors": dict(criterion.anchors),
                        "required_evidence": list(criterion.required_evidence),
                    }
                    for criterion in self.criteria
                ],
            }
        )

    @property
    def needs_judge(self) -> bool:
        return any(criterion.evaluator == "llm" for criterion in self.criteria)


@dataclass(frozen=True)
class RunFacts:
    """Everything scoring may read about one run. The caller, not this module, collects it."""

    execution_pass: bool
    checks: Mapping[str, bool]
    prose: Mapping[str, str] = field(default_factory=dict)
    refs: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    run_digest: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "checks", _sealed(self.checks))
        object.__setattr__(self, "prose", _sealed(self.prose))
        object.__setattr__(self, "refs", _sealed_refs(self.refs))


@dataclass(frozen=True)
class JudgeView:
    """The only run facts a judge receives: no execution flag and no checks."""

    prose: Mapping[str, str]
    refs: Mapping[str, tuple[str, ...]]
    run_digest: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "prose", _sealed(self.prose))
        object.__setattr__(self, "refs", _sealed_refs(self.refs))


@dataclass(frozen=True)
class JudgeRequest:
    """One batched request per run; it names the card by digest so deterministic criteria stay private."""

    card_id: str
    card_digest: str
    criteria: tuple[Criterion, ...]
    facts: JudgeView
    prompt_version: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "criteria", tuple(self.criteria))


@dataclass(frozen=True)
class JudgeReply:
    """Answers plus the attribution that binds them to this judge, prompt and run."""

    answers: Mapping[str, Answer]
    reasons: Mapping[str, str]
    attribution: Mapping[str, Any]
    completeness: Literal["complete", "incomplete"] = "complete"

    def __post_init__(self) -> None:
        object.__setattr__(self, "answers", _sealed(self.answers))
        object.__setattr__(self, "reasons", _sealed(self.reasons))
        object.__setattr__(self, "attribution", _sealed(self.attribution))


class Judge(Protocol):
    """A semantic judge. Implementations own transport, timeouts and artifacts."""

    mode: str
    model: str | None

    def judge(self, request: JudgeRequest) -> JudgeReply: ...


class RecordedJudge:
    """Replays fixed answers. A test adapter; it reaches no model and no network."""

    mode: str = "recorded"
    model: str | None = None

    def __init__(
        self,
        answers: Mapping[str, Answer],
        reasons: Mapping[str, str] | None = None,
        completeness: Literal["complete", "incomplete"] = "complete",
    ) -> None:
        self.answers = dict(answers)
        self.reasons = dict(reasons or {})
        self.completeness = completeness
        self.requests: list[JudgeRequest] = []

    def judge(self, request: JudgeRequest) -> JudgeReply:
        self.requests.append(request)
        reasons = {key: self.reasons.get(key, "Recorded judgement") for key in self.answers}
        return JudgeReply(
            answers=dict(self.answers),
            reasons=reasons,
            attribution={
                "mode": self.mode,
                "model": self.model,
                "prompt_version": request.prompt_version,
                "prompt_digest": digest(
                    [request.card_digest, [criterion.id for criterion in request.criteria], request.prompt_version]
                ),
                "response_digest": digest([self.answers, reasons, self.completeness]),
                "run_digest": request.facts.run_digest,
            },
            completeness=self.completeness,
        )


def _validate_card(card: RubricCard) -> None:
    ids = [criterion.id for criterion in card.criteria]
    if len(ids) != len(set(ids)):
        raise RubricError("Duplicate rubric criterion id")
    _validate_answer_values(card.answer_values)
    for criterion in card.criteria:
        # Type first, so a bad weight is a RubricError rather than a TypeError from the sum below.
        if type(criterion.weight) is not int or criterion.weight <= 0:
            raise RubricError("Criterion weight must be a positive integer: " + criterion.id)
        if not set(criterion.required_evidence) <= _EVIDENCE_SOURCES:
            raise RubricError("Unsupported evidence source: " + criterion.id)
        if criterion.evaluator == "deterministic":
            if not criterion.check_id:
                raise RubricError("A deterministic criterion needs an independent check: " + criterion.id)
            if criterion.required_evidence:
                raise RubricError("A deterministic criterion names no evidence source: " + criterion.id)
        elif criterion.evaluator == "llm":
            if criterion.check_id:
                raise RubricError("An llm criterion must not name a deterministic check: " + criterion.id)
            if set(criterion.anchors) != set(ANSWER_VALUES):
                raise RubricError("An llm criterion needs a yes, maybe and no anchor: " + criterion.id)
            if _PROTECTED_SOURCE in criterion.required_evidence:
                raise RubricError("An llm criterion must not read protected verification: " + criterion.id)
        else:
            raise RubricError("Unsupported evaluator: " + criterion.id)
    if sum(criterion.weight for criterion in card.criteria) <= 0:
        raise RubricError("Rubric weights must sum above zero")


def _validate_answer_values(values: Mapping[Answer, float]) -> None:
    """Require upstream's one answer table, numerically; refuse every other."""
    if set(values) != set(ANSWER_VALUES):
        raise RubricError("A rubric card must value exactly yes, maybe and no")
    for answer, value in values.items():
        if type(value) not in (int, float) or Decimal(str(value)) != _ANSWER_POINTS[answer]:
            raise RubricError("A rubric card must use the canonical answer values: " + answer)


def _validate_facts(card: RubricCard, facts: RunFacts) -> None:
    if type(facts.execution_pass) is not bool:
        raise RubricError("execution_pass must be a boolean")
    for criterion in card.criteria:
        if criterion.evaluator == "deterministic":
            if criterion.check_id not in facts.checks:
                raise RubricError("Missing deterministic check: " + criterion.check_id)
            if type(facts.checks[criterion.check_id]) is not bool:
                raise RubricError("Non-boolean deterministic check: " + criterion.check_id)
            continue
        missing = sorted(set(criterion.required_evidence) - set(facts.prose))
        if missing:
            raise RubricError("Evidence for " + criterion.id + " lacks: " + ", ".join(missing))


def _judge_view(card: RubricCard, facts: RunFacts) -> JudgeView:
    """The judge's copy, by withholding: only prose an llm criterion requires, only refs of judged criteria."""
    sources = {
        source for criterion in card.criteria if criterion.evaluator == "llm" for source in criterion.required_evidence
    }
    judged = {criterion.id for criterion in card.criteria if criterion.evaluator == "llm"}
    return JudgeView(
        prose={name: text for name, text in facts.prose.items() if name in sources},
        refs={name: value for name, value in facts.refs.items() if name in judged},
        run_digest=facts.run_digest,
    )


def _attribution_fault(card: RubricCard, judge: Judge, reply: JudgeReply) -> str | None:
    """Say why a reply cannot be attributed to this judge, prompt and run."""
    try:
        attribution = reply.attribution
        # A duck-typed reply may carry anything; check its shape before _consult copies it.
        if not isinstance(attribution, Mapping):
            return "Judge attribution is not a mapping"
        if any(key not in attribution for key in _ATTRIBUTION_KEYS):
            return "Judge attribution is incomplete"
        if attribution["mode"] != judge.mode or attribution["model"] != judge.model:
            return "Judge attribution differs from the dispatched judge"
        if attribution["prompt_version"] != card.prompt_version:
            return "Judge attribution differs from the card prompt version"
    except AttributeError, TypeError:
        return "Judge attribution validation failed"
    return None


def _answers_fault(card: RubricCard, facts: RunFacts, reply: JudgeReply) -> str | None:
    """Say why answers cannot be awarded. An attributed reply may still be unusable."""
    try:
        for name, value in (("answers", reply.answers), ("reasons", reply.reasons)):
            if not isinstance(value, Mapping):
                return "Judge " + name + " is not a mapping"
        if facts.run_digest and reply.attribution.get("run_digest", facts.run_digest) != facts.run_digest:
            return "Judge answered a different run"
        expected = {criterion.id for criterion in card.criteria if criterion.evaluator == "llm"}
        answered = set(reply.answers)
        if not answered <= expected:
            return "Judge answered an unknown criterion"
        if any(answer not in _ANSWER_POINTS for answer in reply.answers.values()):
            return "Judge returned an unsupported answer"
        if reply.completeness != "complete":
            return "Judge reported incomplete evidence"
        if answered != expected:
            return "Judge omitted a scored criterion"
    except AttributeError, TypeError:
        return "Judge response validation failed"
    return None


def _card_fault(card: RubricCard, facts: RunFacts, card_digest: str) -> str | None:
    """How the card changed while the judge held its Criterion objects; a change is a judge fault, never a score."""
    try:
        _validate_card(card)
        _validate_facts(card, facts)
        current = card.digest()
    except RubricError as error:
        return "The rubric card changed during dispatch: " + str(error)
    except (TypeError, ValueError) as error:
        return "The rubric card changed during dispatch: " + type(error).__name__ + ": " + str(error)
    if current != card_digest:
        return "The rubric card changed during dispatch: it no longer matches the digest taken before it"
    return None


def _consult(
    card: RubricCard, facts: RunFacts, judge: Judge, card_digest: str
) -> tuple[dict[str, Answer], dict[str, str], Document | None, str | None]:
    request = JudgeRequest(
        card_id=card.id,
        card_digest=card_digest,
        criteria=tuple(criterion for criterion in card.criteria if criterion.evaluator == "llm"),
        facts=_judge_view(card, facts),
        prompt_version=card.prompt_version,
    )
    try:
        reply = judge.judge(request)
    except RubricError:
        raise  # the caller's mistake, not an outage
    except BaseExceptionGroup as group:
        # asyncio wraps cancellations in groups; interrupts and caller mistakes keep their meaning inside one.
        intent, _ = group.split((KeyboardInterrupt, SystemExit, RubricError))
        if intent is not None:
            raise
        return {}, {}, None, "Judge dispatch failed: " + type(group).__name__ + ": " + str(group)
    except (Exception, asyncio.CancelledError) as error:  # Judge faults are reported, never scored
        return {}, {}, None, "Judge dispatch failed: " + type(error).__name__ + ": " + str(error)
    fault = _attribution_fault(card, judge, reply)
    if fault is not None:
        return {}, {}, None, fault
    attribution = dict(reply.attribution)
    fault = _answers_fault(card, facts, reply)
    if fault is not None:
        return {}, {}, attribution, fault
    return dict(reply.answers), dict(reply.reasons), attribution, None


def score(card: RubricCard, facts: RunFacts, judge: Judge | None = None) -> Document:
    """Score one run against one card.

    Raises RubricError before any dispatch for an unusable card or facts; a judge failure
    afterwards yields `judge_failed` with no score, because a missing judgement is not a bad result.
    """
    _validate_card(card)
    _validate_facts(card, facts)
    if card.needs_judge and judge is None:
        raise RubricError("This card has llm criteria; pass a judge to score it")
    card_digest = card.digest()

    answers: dict[str, Answer] = {}
    reasons: dict[str, str] = {}
    attribution: Document | None = None
    judge_error: str | None = None
    card_fault: str | None = None
    consulted = card.needs_judge and facts.execution_pass
    if consulted and judge is not None:
        answers, reasons, attribution, judge_error = _consult(card, facts, judge, card_digest)
        card_fault = _card_fault(card, facts, card_digest)
        if card_fault is not None:
            answers, reasons, judge_error = {}, {}, card_fault

    rows: list[Document] = []
    total, deterministic = Decimal(0), Decimal(0)
    for criterion in card.criteria:
        if card_fault is not None:
            unanswered = _CARD_CHANGED
        elif criterion.evaluator == "deterministic" or consulted:
            unanswered = _NOT_ANSWERED
        else:
            unanswered = _NOT_CONSULTED
        row: Document = {
            "id": criterion.id,
            "question": criterion.question,
            "weight": criterion.weight,
            "evaluator": criterion.evaluator,
            "answer": None,
            "points": None,
            "reason": unanswered,
            "refs_supplied": [],
        }
        if card_fault is not None:
            rows.append(row)
            continue
        if criterion.evaluator == "deterministic":
            row.update(
                answer="yes" if facts.checks[criterion.check_id] else "no",
                reason=_DETERMINED,
                refs_supplied=["check-" + criterion.check_id],
            )
        elif criterion.id in answers:
            row.update(
                answer=answers[criterion.id],
                reason=reasons.get(criterion.id, ""),
                refs_supplied=list(facts.refs.get(criterion.id, ())),
            )
        if row["answer"] is not None:
            points = Decimal(criterion.weight) * _ANSWER_POINTS[row["answer"]]
            row["points"] = float(points)
            total += points
            if criterion.evaluator == "deterministic":
                deterministic += points
        rows.append(row)

    # "complete" implies every row is answered: a judge that answered fewer is already a fault above.
    if judge_error is not None:
        status = "judge_failed"
    elif not facts.execution_pass:
        status = "unscored"
    else:
        status = "complete"

    # Score and reward come off the same Decimal; no total for a run that is not fully answered.
    scored: float | None = None
    reward: float | None = None
    if status == "complete":
        weights = Decimal(sum(criterion.weight for criterion in card.criteria))
        exact = (total * Decimal(10) / weights).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        scored = float(exact)
        reward = float((exact / Decimal(10)).quantize(Decimal("0.001"), rounding=ROUND_HALF_UP))
    return {
        "schema": SCHEMA,
        "rubric": {
            "id": card.id,
            "version": card.version,
            "origin": card.origin,
            "digest": card_digest,
        },
        "status": status,
        "score_0_10": scored,
        "deterministic_points": float(deterministic),
        "execution_pass": facts.execution_pass,
        "criteria": rows,
        "answer_values": {answer: float(value) for answer, value in _ANSWER_POINTS.items()},
        "judge": attribution,
        "judge_error": judge_error,
        "normalized_reward": reward,
    }
