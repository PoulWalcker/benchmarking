"""Weighted rubric scoring that rides alongside binary acceptance.

Resolves a rubric card against already-collected run facts and returns one
sapi-lab-rubric-evaluation/v1 document. The module is pure: no file, no
environment variable, no path, no model call, so callers own persistence and own
every dispatch. It deliberately does NOT decide acceptance, turn a score into a
Harbor reward, import an upstream scorecard or compare two documents.

Three rules the rest of this file enforces.

Answer values are fixed: a card states exactly {"yes": 1, "maybe": 0.33,
"no": 0} numerically or it is refused, and points come from that table rather
than the card's copy of it, as upstream does. No card can reweight an answer, so
score_0_10 stays in [0, 10] and normalized_reward in [0, 1] by construction.

A judge sees prose, references and the run digest, and nothing else: not the
execution flag, no deterministic check name or outcome, and not the protected
verification narrative, which no llm criterion may require and which is never
forwarded. What is withheld is absent, never replaced by a fabricated value.

One run costs at most one dispatch, because there is one call site and no loop.
A run that did not execute costs none, since its score would be discarded.
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

# Points are Decimal so a total never acquires a binary-float remainder. The
# card's own table is validated against this one and then ignored, which is what
# the upstream evaluator does with it.
_ANSWER_POINTS: Mapping[Answer, Decimal] = {"yes": Decimal("1"), "maybe": Decimal("0.33"), "no": Decimal("0")}
_EVIDENCE_SOURCES = frozenset({"environment", "candidate", "verification", "engine"})
_PROTECTED_SOURCE = "verification"
_ATTRIBUTION_KEYS = ("mode", "model", "prompt_version", "prompt_digest", "response_digest")
_AWAITING = "Awaiting LLM judge"
_NOT_CONSULTED = "No judge was asked: the run did not execute"
_DETERMINED = "Protected environment verification"


def digest(value: Any) -> str:
    """Canonical JSON digest, ASCII-escaped.

    Source of truth is `sapi_config_lab.runtime.task_evaluation.digest`,
    itself matching AutoWFBench's `autowfbench.core.common.digest`. This is a
    copy rather than an import because the packager flattens verification/*.py
    into a container without `sapi_config_lab`; a test pins the two together.
    `lifecycle.py` hashes with `ensure_ascii=False` and is a different function
    on purpose: do not merge them.
    """
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


class RubricError(ValueError):
    """The card, the run facts or the request is unusable; no score was computed."""


def _sealed(mapping: Mapping[Any, Any]) -> Any:
    """Copy and seal; frozen guards the attribute, never the mapping behind it.

    Without this a caller mutating its own dict, or a judge writing through the
    request it was handed, rewrites a validated card for every later run.
    """
    return MappingProxyType(dict(mapping))


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
        # Frozen protects rebinding, not the caller's dict. Copy, or a later
        # mutation of that dict silently changes an already-scored criterion.
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
        """Identify the questions, weights and answer table this card asks about."""
        return digest(
            {
                "id": self.id,
                "version": self.version,
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

    @classmethod
    def binary(cls, scenario: str) -> RubricCard:
        """One deterministic criterion, so a pass/fail scenario keeps 10.0 or 0.0."""
        return cls(
            id=scenario,
            version="1.0.0",
            origin="local",
            criteria=(
                Criterion(
                    id="acceptance",
                    question="Does the result satisfy the independent acceptance contract for " + scenario + "?",
                    weight=10,
                    evaluator="deterministic",
                    check_id="accepted",
                ),
            ),
        )


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
        object.__setattr__(self, "refs", _sealed({key: tuple(value) for key, value in self.refs.items()}))


@dataclass(frozen=True)
class JudgeView:
    """The only run facts a judge receives.

    There is no execution flag and no check here, so no llm criterion can be
    answered from either, and no field asserts something false in their place.
    """

    prose: Mapping[str, str]
    refs: Mapping[str, tuple[str, ...]]
    run_digest: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "prose", _sealed(self.prose))
        object.__setattr__(self, "refs", _sealed({key: tuple(value) for key, value in self.refs.items()}))


@dataclass(frozen=True)
class JudgeRequest:
    """One batched request. Scoring never sends a second one for the same run.

    It names the card by digest instead of carrying it: the deterministic
    criteria and their check names are not the judge's business.
    """

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
    if sum(criterion.weight for criterion in card.criteria) <= 0:
        raise RubricError("Rubric weights must sum above zero")
    for criterion in card.criteria:
        if type(criterion.weight) is not int or criterion.weight <= 0:
            raise RubricError("Criterion weight must be a positive integer: " + criterion.id)
        if not set(criterion.required_evidence) <= _EVIDENCE_SOURCES:
            raise RubricError("Unsupported evidence source: " + criterion.id)
        if criterion.evaluator == "deterministic":
            if not criterion.check_id:
                raise RubricError("A deterministic criterion needs an independent check: " + criterion.id)
            # Nothing reads a deterministic criterion's sources: it is answered
            # by its check. Declaring one would describe a lookup that never happens.
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


def _validate_answer_values(values: Mapping[Answer, float]) -> None:
    """Require the one table upstream hardcodes, numerically; refuse every other.

    Checking the key set alone let a card state `{"yes": 7.5}` or a negative or
    a NaN, which then left the scale, or reached `digest()` and raised there.
    """
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
            # A check the caller did not resolve is an error, never a silent "no".
            if criterion.check_id not in facts.checks:
                raise RubricError("Missing deterministic check: " + criterion.check_id)
            if type(facts.checks[criterion.check_id]) is not bool:
                raise RubricError("Non-boolean deterministic check: " + criterion.check_id)
            continue
        # An llm criterion reads prose and nothing else, so its required sources
        # are satisfiable only if the caller supplied prose under those names.
        missing = sorted(set(criterion.required_evidence) - set(facts.prose))
        if missing:
            raise RubricError("Evidence for " + criterion.id + " lacks: " + ", ".join(missing))


def _judge_view(card: RubricCard, facts: RunFacts) -> JudgeView:
    """Build the judge's copy by withholding, never by restating.

    Only prose that some llm criterion declared it needs crosses over, which
    excludes the protected verification narrative because no llm criterion may
    declare it. Refs cross over only for the criteria actually being judged.
    """
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
        # A judge that reports an unusable card or request is reporting the
        # caller's mistake, not an outage. Degrading it would hide a bug.
        raise
    except (Exception, asyncio.CancelledError) as error:
        # Transport, timeout, unavailability and cancellation are environment
        # faults: reported, never raised and never scored as a "no".
        # KeyboardInterrupt and SystemExit stay unhandled; they are not faults.
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
    """Score one run against one card and return the evaluation document.

    Raises RubricError for an unusable card or unusable run facts, always before
    any dispatch. A judge that fails after dispatch yields an unscored document
    with a stated error, because a missing judgement is not a bad result.

    At most one judgement is requested per call, guaranteed by there being a
    single call site below and no loop; a run that did not execute requests
    none, since its score would be discarded. A judge fault outranks that, so
    an operator filtering `status` for outages still sees every one of them.
    """
    _validate_card(card)
    _validate_facts(card, facts)
    if card.needs_judge and judge is None:
        raise RubricError("This card has llm criteria; pass a judge to score it")
    # Before the dispatch: a digest that cannot be taken must not cost a judgement.
    card_digest = card.digest()

    answers: dict[str, Answer] = {}
    reasons: dict[str, str] = {}
    attribution: Document | None = None
    judge_error: str | None = None
    consulted = card.needs_judge and facts.execution_pass
    if consulted and judge is not None:
        answers, reasons, attribution, judge_error = _consult(card, facts, judge, card_digest)

    rows: list[Document] = []
    total, deterministic = Decimal(0), Decimal(0)
    for criterion in card.criteria:
        row: Document = {
            "id": criterion.id,
            "question": criterion.question,
            "weight": criterion.weight,
            "evaluator": criterion.evaluator,
            "answer": None,
            "points": None,
            "reason": _AWAITING if consulted or criterion.evaluator == "deterministic" else _NOT_CONSULTED,
            # What went in, never what a judge cited back: this module collects
            # no citations and validates none, so it does not claim to hold any.
            "refs_supplied": [],
        }
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

    if judge_error is not None:
        status = "judge_failed"
    elif not facts.execution_pass:
        # The engine not running is a separate fact from acceptance and from
        # quality: the criteria stay reported, the total is withheld.
        status = "unscored"
    elif all(row["answer"] is not None for row in rows):
        status = "complete"
    else:
        status = "judge_failed"

    # No total is manufactured for a run that is not fully answered. Both the
    # score and the reward come off the same Decimal; dividing the float by ten
    # disagrees with it for 289 of the 1001 reachable scores.
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
