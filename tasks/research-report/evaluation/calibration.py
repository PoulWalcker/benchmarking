"""Synthetic semantic counterfactuals of one verified native record; nothing here calls a model.

A variant replaces only the final report the Judge reads. Native execution and acceptance are never
claimed for it, and the preregistered expectations never enter a Judge request.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from verification import verify
from verification.rubric import JudgeReply, JudgeRequest, JudgeView

from .evaluator import card, evaluate

CATALOG = Path(__file__).with_name("calibration.json")
SCHEMA = "sapi-lab-research-judge-calibration/v1"
# The single-run projection `evaluator.prose` and the rubric facts give the native Judge.
_ENVIRONMENT = "Run 1:\nSupplied material:\n"
_CANDIDATE = "Run 1:\nFinal report:\n"


def catalog() -> dict[str, Any]:
    document = json.loads(CATALOG.read_text())
    if document.get("schema") != SCHEMA or set(document["variants"]) != set(document["expected"]):
        raise ValueError("Unsupported research calibration catalog")
    return document


def variant_text(name: str) -> str:
    text = catalog()["variants"].get(name)
    if not isinstance(text, str):
        raise ValueError("Unknown calibration variant: " + name)
    return text


class _Capture:
    """Receives the native request the verifier would send, then declines it: no answer, no model."""

    mode: str = "calibration-capture"
    model: str | None = None

    def __init__(self) -> None:
        self.request: JudgeRequest | None = None

    def judge(self, request: JudgeRequest) -> JudgeReply:
        self.request = request
        raise ValueError("Calibration captures the native request and never answers it")


def request(evidence: Path, options: dict, name: str) -> tuple[JudgeRequest, dict[str, Any]]:
    """The synthetic request for one frozen variant of a verified record, and the identity it binds.

    The base record is verified again into `options["evaluation"]`; its final report is replaced only in
    the request. Unverifiable evidence or another source refuses here, before any reservation exists.
    """
    text = variant_text(name)
    source = catalog()["source"]
    capture = _Capture()
    evaluate(evidence, options, judge=capture)
    native = capture.request
    if native is None:
        raise ValueError("Calibration base evidence is incomplete: no verified source and final report")
    if (
        native.facts.prose.get("environment") != _ENVIRONMENT + source
        or not native.facts.prose.get("candidate", "").startswith(_CANDIDATE)
        or native.card_digest != card().digest()
    ):
        raise ValueError("Calibration base evidence is not exactly one verified run of the frozen source")
    identity = {
        "schema": SCHEMA,
        "variant": name,
        "variant_sha256": hashlib.sha256(text.encode()).hexdigest(),
        "source_sha256": hashlib.sha256(source.encode()).hexdigest(),
        "base_run_digest": native.facts.run_digest,
        "card_digest": native.card_digest,
    }
    run_digest = verify.digest(identity)
    synthetic = JudgeRequest(
        card_id=native.card_id,
        card_digest=native.card_digest,
        criteria=native.criteria,
        facts=JudgeView(
            prose={"environment": native.facts.prose["environment"], "candidate": _CANDIDATE + text},
            refs=native.facts.refs,
            run_digest=run_digest,
        ),
        prompt_version=native.prompt_version,
    )
    return synthetic, {**identity, "run_digest": run_digest}


def compare(name: str, reply: JudgeReply | None, *, fresh: bool = False) -> dict[str, Any]:
    """Expected against observed labels; only this call's own fresh dispatch is a measurement.

    A mocked reply is simulated, and a replayed wrapper reply reproduces an earlier measurement.
    """
    expected = catalog()["expected"][name]
    result: dict[str, Any] = {
        "variant": name,
        "expected": expected,
        "status": "not_judged",
        "labels_agree": None,
        "affects_candidate_score": False,
    }
    if reply is None:
        return result
    answers = dict(reply.answers)
    # A null expectation is reported, not gated: fluent prose may read clearly whether or not it is true.
    checks = {
        criterion: answers.get(criterion) in allowed
        for criterion in ("clarity", "usefulness")
        if (allowed := expected[criterion]) is not None
    }
    wrapper = reply.attribution.get("mode") == "wrapper"
    return {
        **result,
        "status": ("measured" if fresh else "replayed") if wrapper else "simulated",
        "observed": {"answers": answers, "reasons": dict(reply.reasons), "model": reply.attribution.get("model")},
        "checks": checks,
        "labels_agree": all(checks.values()),
        # Labels can agree by accident; whether each reason cites the supplied text is a human judgement.
        "reason_review": "required",
    }
