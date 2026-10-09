"""Content-bound offline fixture Judge; live transport is composed in a later ticket."""

from __future__ import annotations

from collections.abc import Callable
import hashlib
import json
from pathlib import Path
from typing import Any, Literal, cast

from verification.rubric import Answer, JudgeReply, JudgeRequest, RubricCard

PROMPT_VERSION = "fixture-judge/v1"
PROMPT_PATH = Path(__file__).with_name("fixture-judge-prompt.md")
PROMPT_SHA256 = "ec8023162bcb95ddf8f25a3edb02851944dcbde701d0d0fdc83a4beba3536142"
MAX_BYTES = 65536
MAX_REASON = 2000


def canonical_bytes(value: Any) -> bytes:
    """Encode R03 JSON identities with finite numbers and literal Unicode."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _strict_json(raw: bytes) -> Any:
    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicate Judge JSON key")
            result[key] = value
        return result

    def nonfinite(value: str) -> None:
        raise ValueError("Nonfinite Judge JSON value: " + value)

    return json.loads(raw.decode("utf-8"), object_pairs_hook=unique, parse_constant=nonfinite)


def _request_document(request: JudgeRequest) -> dict[str, Any]:
    return {
        "card_id": request.card_id,
        "card_digest": request.card_digest,
        "prompt_version": request.prompt_version,
        "run_digest": request.facts.run_digest,
        "criteria": [
            {
                "id": item.id,
                "question": item.question,
                "anchors": dict(item.anchors),
                "required_evidence": list(item.required_evidence),
            }
            for item in request.criteria
        ],
        "prose": dict(request.facts.prose),
        "refs": {key: list(value) for key, value in request.facts.refs.items()},
    }


class FixtureJudge:
    """Replay a trusted saved bundle or create an explicitly mocked offline bundle."""

    mode: str
    model: str | None

    def __init__(
        self,
        bundle: Path,
        native_identity: bytes | dict[str, Any],
        card: RubricCard,
        model: str,
        *,
        origin: str,
        response: Callable[[str], bytes] | None = None,
        transport: Callable[[str], Any] | None = None,
        replay_receipt: Path | None = None,
    ) -> None:
        if origin not in {"saved", "mocked"}:
            raise ValueError("Live fixture Judge transport is disabled")
        self.bundle = bundle
        self.native_bytes = native_identity if isinstance(native_identity, bytes) else canonical_bytes(native_identity)
        self.card = card
        self.model = model
        self.origin = origin
        self.mode = "mocked" if origin == "mocked" else "wrapper"
        self.response = response
        self.transport = transport
        self.replay_receipt = replay_receipt or bundle.with_name(bundle.name + "-replay-receipt.json")
        if origin == "saved":
            saved_context = _strict_json((bundle / "context.json").read_bytes())
            if not isinstance(saved_context, dict) or saved_context.get("origin") not in {"mocked", "fresh"}:
                raise ValueError("Saved Judge origin invalid")
            self.mode = "mocked" if saved_context["origin"] == "mocked" else "wrapper"

    @classmethod
    def saved(
        cls,
        bundle: Path,
        native_identity: bytes | dict[str, Any],
        card: RubricCard,
        model: str,
        *,
        transport: Callable[[str], Any] | None = None,
        replay_receipt: Path | None = None,
    ) -> FixtureJudge:
        """Open a trusted bundle for exact offline replay."""
        return cls(
            bundle, native_identity, card, model, origin="saved", transport=transport, replay_receipt=replay_receipt
        )

    @classmethod
    def mock(
        cls,
        bundle: Path,
        native_identity: bytes | dict[str, Any],
        card: RubricCard,
        model: str,
        response: Callable[[str], bytes],
    ) -> FixtureJudge:
        """Create labeled synthetic evidence without a provider call."""
        return cls(bundle, native_identity, card, model, origin="mocked", response=response)

    def judge(self, request: JudgeRequest) -> JudgeReply:
        """Validate all saved identities before returning a reply; never call transport."""
        if request.card_id != self.card.id or request.card_digest != self.card.digest():
            raise ValueError("Judge card identity mismatch")
        if request.prompt_version != self.card.prompt_version or not request.facts.run_digest:
            raise ValueError("Judge request identity missing or mismatched")
        if tuple(request.criteria) != tuple(c for c in self.card.criteria if c.evaluator == "llm"):
            raise ValueError("Judge semantic criteria mismatch")
        request_bytes = canonical_bytes(_request_document(request))
        if len(request_bytes) > MAX_BYTES:
            raise ValueError("Judge request exceeds 64 KiB")
        template = PROMPT_PATH.read_bytes()
        if _sha(template) != PROMPT_SHA256:
            raise ValueError("Fixture Judge prompt differs from its pinned version")
        request_digest = _sha(request_bytes)
        prompt = template + b"\nREQUEST_DIGEST\n" + request_digest.encode("ascii") + b"\nREQUEST_JSON\n" + request_bytes
        context = {
            "schema": "sapi-lab-fixture-judge/v1",
            "evidence_digest": request.facts.run_digest,
            "native_identity_digest": _sha(self.native_bytes),
            "card_digest": request.card_digest,
            "card_version": self.card.version,
            "request_digest": request_digest,
            "prompt_digest": _sha(prompt),
            "template_version": PROMPT_VERSION,
            "template_digest": _sha(template),
            "requested_model": self.model,
            "wrapper_inspection_digest": None,
        }
        if self.origin == "mocked":
            return self._mock(request_bytes, prompt, context, request)
        return self._replay(request_bytes, prompt, context, request)

    def _validate(
        self, raw: bytes, context: dict[str, Any]
    ) -> tuple[dict[str, Answer], dict[str, str], Literal["complete"]]:
        if len(raw) > MAX_BYTES:
            raise ValueError("Judge response exceeds 64 KiB")
        document = _strict_json(raw)
        if not isinstance(document, dict) or set(document) != {"request_digest", "answers", "reasons", "completeness"}:
            raise ValueError("Judge response schema mismatch")
        expected = {criterion.id for criterion in self.card.criteria if criterion.evaluator == "llm"}
        answers, reasons = document["answers"], document["reasons"]
        if document["request_digest"] != context["request_digest"]:
            raise ValueError("Judge answered another request")
        if (
            not isinstance(answers, dict)
            or set(answers) != expected
            or any(not isinstance(v, str) or v not in {"yes", "maybe", "no"} for v in answers.values())
        ):
            raise ValueError("Judge answers incomplete or invalid")
        if (
            not isinstance(reasons, dict)
            or set(reasons) != expected
            or any(not isinstance(v, str) or not v.strip() or len(v) > MAX_REASON for v in reasons.values())
        ):
            raise ValueError("Judge reasons incomplete or invalid")
        if document["completeness"] != "complete":
            raise ValueError("Judge response incomplete")
        return cast(dict[str, Answer], answers), reasons, "complete"

    def _reply(self, raw: bytes, context: dict[str, Any], request: JudgeRequest, mode: str) -> JudgeReply:
        answers, reasons, completeness = self._validate(raw, context)
        return JudgeReply(
            answers,
            reasons,
            {
                "mode": mode,
                "model": self.model,
                "prompt_version": request.prompt_version,
                "prompt_digest": context["prompt_digest"],
                "response_digest": _sha(raw),
                "run_digest": request.facts.run_digest,
                "template_version": PROMPT_VERSION,
                "template_digest": context["template_digest"],
                "card_version": self.card.version,
                "card_digest": context["card_digest"],
                "reported_model": self.model,
            },
            completeness,
        )

    def _mock(self, request_bytes: bytes, prompt: bytes, context: dict[str, Any], request: JudgeRequest) -> JudgeReply:
        if self.response is None:
            raise ValueError("Missing mock response")
        raw = self.response(context["request_digest"])
        reply = self._reply(raw, context, request, "mocked")
        self.bundle.mkdir(parents=True, exist_ok=False)
        (self.bundle / "request.json").write_bytes(request_bytes)
        (self.bundle / "prompt.txt").write_bytes(prompt)
        (self.bundle / "response.txt").write_bytes(raw)
        reply_bytes = canonical_bytes(self._reply_document(reply))
        (self.bundle / "reply.json").write_bytes(reply_bytes)
        context = {**context, "response_digest": _sha(raw), "reply_digest": _sha(reply_bytes), "origin": "mocked"}
        (self.bundle / "context.json").write_bytes(canonical_bytes(context))
        (self.bundle / "receipt.json").write_bytes(
            canonical_bytes(
                {
                    "schema": "sapi-lab-fixture-judge-receipt/v1",
                    "state": "completed",
                    "origin": "mocked",
                    "new_invocations": 0,
                    "context_digest": _sha(canonical_bytes(context)),
                    "response_digest": _sha(raw),
                    "reply_digest": _sha(reply_bytes),
                }
            )
        )
        return reply

    def _replay(
        self, request_bytes: bytes, prompt: bytes, expected: dict[str, Any], request: JudgeRequest
    ) -> JudgeReply:
        context = _strict_json((self.bundle / "context.json").read_bytes())
        if not isinstance(context, dict) or any(context.get(key) != value for key, value in expected.items()):
            raise ValueError("Saved Judge context mismatch")
        if context.get("origin") not in {"mocked", "fresh"}:
            raise ValueError("Saved Judge origin invalid")
        for name, value in (("request.json", request_bytes), ("prompt.txt", prompt)):
            if (self.bundle / name).read_bytes() != value:
                raise ValueError("Saved Judge " + name + " mismatch")
        raw = (self.bundle / "response.txt").read_bytes()
        if _sha(raw) != context.get("response_digest"):
            raise ValueError("Saved Judge response mismatch")
        stored_reply = (self.bundle / "reply.json").read_bytes()
        if _sha(stored_reply) != context.get("reply_digest"):
            raise ValueError("Saved Judge reply mismatch")
        reply = self._reply(raw, context, request, "mocked" if context["origin"] == "mocked" else "wrapper")
        if _strict_json(stored_reply) != self._reply_document(reply):
            raise ValueError("Saved Judge reply content mismatch")
        receipt = _strict_json((self.bundle / "receipt.json").read_bytes())
        if not isinstance(receipt, dict) or receipt != {
            "schema": "sapi-lab-fixture-judge-receipt/v1",
            "state": "completed",
            "origin": context["origin"],
            "new_invocations": 0 if context["origin"] == "mocked" else 1,
            "context_digest": _sha(canonical_bytes(context)),
            "response_digest": context["response_digest"],
            "reply_digest": context["reply_digest"],
        }:
            raise ValueError("Saved Judge terminal receipt mismatch")
        with self.replay_receipt.open("xb") as replay_file:
            replay_file.write(
                canonical_bytes(
                    {
                        "schema": "sapi-lab-fixture-judge-replay/v1",
                        "state": "replayed",
                        "origin": context["origin"],
                        "new_invocations": 0,
                        "original_receipt_digest": _sha(canonical_bytes(receipt)),
                    }
                )
            )
        return reply

    @staticmethod
    def _reply_document(reply: JudgeReply) -> dict[str, Any]:
        return {
            "answers": dict(reply.answers),
            "reasons": dict(reply.reasons),
            "attribution": dict(reply.attribution),
            "completeness": reply.completeness,
        }
