"""Content-bound fixture Judge: one reserved wrapper dispatch, exact saved replay or labeled mock."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
import hashlib
from http.client import HTTPException
import json
from pathlib import Path
import re
import subprocess
from typing import Any, Literal, cast

from sapi_config_lab.coordinate.ledger import Ledger
from sapi_config_lab.coordinate.wrapper import wrapper_identity
from sapi_config_lab.evidence import digest, durable_json
from sapi_config_lab.execute.agency import MAX_BODY
from sapi_config_lab.wrapper_audit import reported_model, reported_tokens, stderr_sha256, tool_markers
from verification.rubric import Answer, JudgeReply, JudgeRequest, RubricCard

PROMPT_VERSION = "fixture-judge/v1"
PROMPT_PATH = Path(__file__).with_name("fixture-judge-prompt.md")
PROMPT_SHA256 = "ec8023162bcb95ddf8f25a3edb02851944dcbde701d0d0fdc83a4beba3536142"
MAX_BYTES = 65536
MAX_REASON = 2000
# Below the 420-second task evaluation ceiling, so the parent never times out a Judge that could still finish.
JUDGE_TIMEOUT_SECONDS = 180
RECEIPT_SCHEMA = "sapi-lab-fixture-judge-receipt/v1"
UNSETTLED = frozenset({"dispatch_started", "unknown"})
FRESH_RECEIPT_FIELDS = frozenset(
    {
        "schema",
        "origin",
        "requested_dispatch",
        "state",
        "new_invocations",
        "reservation",
        "expected_model",
        "reported_model",
        "wrapper_inspection_digest",
        "timeout_seconds",
        "started_at",
        "finished_at",
        "request_digest",
        "prompt_digest",
        "context_digest",
        "response_digest",
        "wrapper_digest",
        "reply_digest",
        "failure",
    }
)


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


def _now() -> str:
    return datetime.now(UTC).isoformat()


class _Incomplete(ValueError):
    """The Judge itself reported that it could not answer every criterion."""


def _read_receipt(directory: Path) -> dict[str, Any]:
    receipt = _strict_json((directory / "receipt.json").read_bytes())
    if not isinstance(receipt, dict) or receipt.get("schema") != RECEIPT_SCHEMA:
        raise ValueError("Judge receipt invalid")
    return receipt


def receipt_state(directory: Path) -> str | None:
    """The recorded dispatch state, or None where no receipt was ever initialized."""
    if not (directory / "receipt.json").is_file():
        return None
    state = _read_receipt(directory).get("state")
    return state if isinstance(state, str) else "unknown"


def _unknown(directory: Path) -> subprocess.TimeoutExpired:
    # The ledger's existing unknown path; a process loss is not claimed to be a measured provider timeout.
    return subprocess.TimeoutExpired("fixture judge unknown_outcome: " + str(directory), JUDGE_TIMEOUT_SECONDS)


@contextmanager
def unknown_while_unsettled(directory: Path) -> Iterator[None]:
    """Any failure after a start receipt without a terminal one is an unknown spend, never a known failure."""
    try:
        yield
    except subprocess.TimeoutExpired:
        raise
    except Exception as error:
        if receipt_state(directory) in UNSETTLED:
            raise _unknown(directory) from error
        raise
    if receipt_state(directory) in UNSETTLED:
        raise _unknown(directory)


def _stamp(ledger: Path, index: int, event: dict) -> dict[str, Any]:
    return {"ledger": str(Path(ledger).resolve()), "index": index, "event_digest": digest(event)}


def stamp(directory: Path, ledger: Path, index: int, event: dict) -> None:
    """Bind the initialized fresh receipt to the exact pending reservation before its call runs."""
    receipt = _read_receipt(directory)
    if receipt.get("origin") != "fresh" or receipt.get("state") != "not_dispatched" or receipt.get("reservation"):
        raise ValueError("Judge reservation needs an initialized, unreserved fresh receipt")
    durable_json(directory / "receipt.json", {**receipt, "reservation": _stamp(ledger, index, event)})


def skip(directory: Path, reason: str) -> None:
    """Close a receipt that never started a dispatch; a started one keeps its state."""
    receipt = _read_receipt(directory)
    if receipt.get("state") == "not_dispatched":
        durable_json(directory / "receipt.json", {**receipt, "state": "skipped", "failure": reason})


def dispatch_outcome(
    directory: Path,
    ledger: Path,
    index: int,
    event: dict,
    *,
    model: str | None = None,
    inspection_sha256: str | None = None,
) -> dict[str, Any]:
    """Prove one fresh dispatch for this reservation completed, from its durable artifacts alone.

    A given `model` or `inspection_sha256` is the caller's own expectation, never the receipt's.
    """
    receipt = _read_receipt(directory)
    if receipt.get("state") in UNSETTLED:
        raise _unknown(directory)
    if receipt.get("origin") != "fresh" or receipt.get("reservation") != _stamp(ledger, index, event):
        raise ValueError("Judge receipt is not a fresh dispatch for this reservation")
    if (model is not None and receipt.get("expected_model") != model) or (
        inspection_sha256 is not None and receipt.get("wrapper_inspection_digest") != inspection_sha256
    ):
        raise ValueError("Judge receipt is for another model or wrapper inspection")
    if receipt.get("state") != "completed":
        raise ValueError(f"Judge invocation {receipt.get('state')}: {receipt.get('failure')}")
    context = _strict_json((directory / "context.json").read_bytes())
    if not isinstance(context, dict) or context.get("origin") != "fresh":
        raise ValueError("Judge context invalid")
    _verify_artifacts(directory, context)
    _check_terminal(directory, receipt, context)
    names = ("request.json", "prompt.txt", "response.txt", "wrapper.json", "reply.json", "context.json")
    return {
        "status": "complete",
        "receipt_sha256": _sha((directory / "receipt.json").read_bytes()),
        "artifacts": {name: _sha((directory / name).read_bytes()) for name in names},
    }


def require_fresh(
    directory: Path, ledger: Path, index: int, event: dict, *, model: str, inspection: Path, quality: Any
) -> dict[str, Any]:
    """A host's own proof that the fresh judging it requested produced exactly this quality.

    The task process's success is not evidence: a missing, saved, mocked, substituted or
    foreign receipt, or quality scored from another reply, fails here.
    """
    if receipt_state(directory) is None:
        raise ValueError("Requested fresh Judge left no receipt")
    try:
        proof = dispatch_outcome(
            directory, ledger, index, event, model=model, inspection_sha256=_sha(inspection.read_bytes())
        )
    except OSError as error:
        raise ValueError("Fresh Judge artifacts missing") from error
    judged = quality.get("judge") if isinstance(quality, dict) else None
    if (
        not isinstance(judged, dict)
        or quality.get("status") != "complete"
        or judged.get("model") != model
        or judged.get("response_digest") != proof["artifacts"]["response.txt"]
    ):
        raise ValueError("Judge quality is not the verified fresh reply")
    return proof


def saved_model(bundle: Path) -> str:
    """The Judge identity that answered a saved bundle; its replay verifies every other byte."""
    context = _strict_json((bundle / "context.json").read_bytes())
    model = context.get("requested_model") if isinstance(context, dict) else None
    if not isinstance(model, str) or not model:
        raise ValueError("Saved Judge identity missing")
    return model


def _verify_artifacts(directory: Path, context: dict[str, Any]) -> None:
    for name, field in (
        ("request.json", "request_digest"),
        ("prompt.txt", "prompt_digest"),
        ("response.txt", "response_digest"),
        ("reply.json", "reply_digest"),
    ):
        if _sha((directory / name).read_bytes()) != context.get(field):
            raise ValueError("Judge artifact mismatch: " + name)


def _check_terminal(directory: Path, receipt: Any, context: dict[str, Any]) -> None:
    """A completed terminal receipt bound to exactly these artifacts; fresh ones also to their wrapper."""
    core = {
        "schema": RECEIPT_SCHEMA,
        "state": "completed",
        "origin": context["origin"],
        "new_invocations": 0 if context["origin"] == "mocked" else 1,
        "context_digest": _sha(canonical_bytes(context)),
        "response_digest": context["response_digest"],
        "reply_digest": context["reply_digest"],
    }
    if not isinstance(receipt, dict) or any(receipt.get(key) != value for key, value in core.items()):
        raise ValueError("Saved Judge terminal receipt mismatch")
    if context["origin"] == "mocked":
        if set(receipt) != set(core):
            raise ValueError("Saved Judge terminal receipt mismatch")
        return
    wrapper = (directory / "wrapper.json").read_bytes()
    metadata = _strict_json(wrapper)
    if (
        set(receipt) != FRESH_RECEIPT_FIELDS
        or receipt["wrapper_digest"] != _sha(wrapper)
        or not isinstance(metadata, dict)
        or metadata.get("output_sha256") != context["response_digest"]
        or metadata.get("model") != context["requested_model"]
        or receipt["reported_model"] != context["requested_model"]
        or receipt["expected_model"] != context["requested_model"]
        or receipt["wrapper_inspection_digest"] != context["wrapper_inspection_digest"]
        or receipt["request_digest"] != context["request_digest"]
        or receipt["prompt_digest"] != context["prompt_digest"]
        or not isinstance(receipt["reservation"], dict)
    ):
        raise ValueError("Fresh Judge terminal receipt mismatch")


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
    """Dispatch one reserved fresh request, replay a trusted saved bundle, or create a labeled mock."""

    mode: str
    model: str | None

    def __init__(
        self,
        bundle: Path,
        native_identity: bytes | dict[str, Any],
        card: RubricCard,
        model: str | None,
        *,
        origin: str,
        response: Callable[[str], bytes] | None = None,
        transport: Callable[..., Any] | None = None,
        replay_receipt: Path | None = None,
        reserved: Callable[[Callable[[], dict]], Any] | None = None,
        upstream: str | None = None,
        inspection: Path | None = None,
        wrapper_files: dict[str, Path] | None = None,
        timeout: float = JUDGE_TIMEOUT_SECONDS,
    ) -> None:
        if origin not in {"saved", "mocked", "fresh"}:
            raise ValueError("Unknown fixture Judge origin")
        self.bundle = bundle
        self.native_bytes = native_identity if isinstance(native_identity, bytes) else canonical_bytes(native_identity)
        self.card = card
        self.model = model
        self.origin = origin
        self.mode = "mocked" if origin == "mocked" else "wrapper"
        self.response = response
        self.transport = transport
        self.replay_receipt = replay_receipt or bundle.with_name(bundle.name + "-replay-receipt.json")
        self.reserved = reserved
        self.upstream = upstream
        self.inspection = inspection
        self.wrapper_files = wrapper_files
        self.timeout = timeout
        self.consulted = False
        self._fresh_reply: JudgeReply | None = None
        if origin == "saved":
            saved_context = _strict_json((bundle / "context.json").read_bytes())
            if not isinstance(saved_context, dict) or saved_context.get("origin") not in {"mocked", "fresh"}:
                raise ValueError("Saved Judge origin invalid")
            self.mode = "mocked" if saved_context["origin"] == "mocked" else "wrapper"
        if origin == "fresh":
            if not callable(reserved) or not callable(transport) or not upstream or inspection is None or not model:
                raise ValueError("Fresh Judge dispatch needs a reservation, transport, inspected wrapper and model")
            if not 0 < timeout <= JUDGE_TIMEOUT_SECONDS:
                raise ValueError("Judge timeout must be positive and at most 180 seconds")
            self.inspection_digest = self._inspect()
            bundle.mkdir(parents=True, exist_ok=False)
            durable_json(
                bundle / "receipt.json",
                dict.fromkeys(FRESH_RECEIPT_FIELDS)
                | {
                    "schema": RECEIPT_SCHEMA,
                    "origin": "fresh",
                    "requested_dispatch": True,
                    "state": "not_dispatched",
                    "new_invocations": 0,
                    "expected_model": model,
                    "wrapper_inspection_digest": self.inspection_digest,
                    "timeout_seconds": timeout,
                },
            )

    @classmethod
    def fresh(
        cls,
        directory: Path,
        native_identity: bytes | dict[str, Any],
        card: RubricCard,
        model: str | None,
        *,
        reserved: Callable[[Callable[[], dict]], Any],
        transport: Callable[..., Any],
        upstream: str | None,
        inspection: Path | None,
        wrapper_files: dict[str, Path] | None = None,
        timeout: float = JUDGE_TIMEOUT_SECONDS,
    ) -> FixtureJudge:
        """Dispatch at most one request through the inspected wrapper inside its one-use reservation.

        A missing model, endpoint or inspection refuses here, before any reservation exists.
        """
        return cls(
            directory,
            native_identity,
            card,
            model,
            origin="fresh",
            transport=transport,
            reserved=reserved,
            upstream=upstream,
            inspection=inspection,
            wrapper_files=wrapper_files,
            timeout=timeout,
        )

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
        """Validate every identity before any reservation; only the fresh origin reaches transport."""
        if self.origin != "fresh":
            request_bytes, prompt, context = self._prepare(request)
            if self.origin == "mocked":
                return self._mock(request_bytes, prompt, {**context, "wrapper_inspection_digest": None}, request)
            return self._replay(request_bytes, prompt, context, request)
        if self.consulted:
            raise ValueError("A fresh fixture Judge answers exactly one request")
        self.consulted = True
        try:
            request_bytes, prompt, context = self._prepare(request)
        except ValueError as error:
            self._close(_read_receipt(self.bundle), "failed", failure="refused before reservation: " + str(error))
            raise
        context["wrapper_inspection_digest"] = self.inspection_digest
        assert self.reserved is not None
        try:
            outcome = self.reserved(lambda: self._dispatch(request_bytes, prompt, context, request))
        except ValueError as error:
            if receipt_state(self.bundle) == "not_dispatched":
                self._close(_read_receipt(self.bundle), "failed", failure="reservation refused: " + str(error))
            raise
        if not isinstance(outcome, dict) or outcome.get("status") != "complete" or self._fresh_reply is None:
            raise ValueError("Judge invocation did not complete")
        return self._fresh_reply

    def _prepare(self, request: JudgeRequest) -> tuple[bytes, bytes, dict[str, Any]]:
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
        return (
            request_bytes,
            prompt,
            {
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
            },
        )

    def _inspect(self) -> str:
        assert self.inspection is not None and self.upstream is not None and self.model is not None
        wrapper_identity(self.inspection, self.upstream, self.model, self.wrapper_files)
        return _sha(self.inspection.read_bytes())

    def _close(self, receipt: dict[str, Any], state: str, **fields: Any) -> dict[str, Any]:
        receipt = {**receipt, **fields, "state": state, "finished_at": _now()}
        durable_json(self.bundle / "receipt.json", receipt)
        return receipt

    def _dispatch(
        self, request_bytes: bytes, prompt: bytes, context: dict[str, Any], request: JudgeRequest
    ) -> dict[str, Any]:
        """Run inside the reservation: start durably, send once, then record one terminal state."""
        receipt = _read_receipt(self.bundle)
        reservation = receipt.get("reservation")
        try:
            if receipt.get("state") != "not_dispatched" or not isinstance(reservation, dict):
                raise ValueError("no stamped reservation")
            ledger = Ledger.open(Path(reservation["ledger"]), ceilings=None, stop_after_failure=None)
            events = ledger.data["events"]
            index = reservation["index"]
            if (
                type(index) is not int
                or not 0 <= index < len(events)
                or digest(events[index]) != reservation["event_digest"]
                or events[index]["phase"] != "judge"
                or events[index]["status"] != "unknown"
            ):
                raise ValueError("reservation is not the exact pending Judge event")
            if self._inspect() != self.inspection_digest:
                raise ValueError("wrapper inspection changed")
        except (KeyError, OSError, ValueError) as error:
            self._close(receipt, "failed", failure="refused before dispatch: " + str(error))
            return {"status": "failed"}
        (self.bundle / "request.json").write_bytes(request_bytes)
        (self.bundle / "prompt.txt").write_bytes(prompt)
        receipt = {
            **receipt,
            "state": "dispatch_started",
            "started_at": _now(),
            "request_digest": context["request_digest"],
            "prompt_digest": context["prompt_digest"],
        }
        durable_json(self.bundle / "receipt.json", receipt)
        try:
            raw = cast(Callable[..., Any], self.transport)(
                self.upstream, prompt.decode("utf-8"), self.timeout, MAX_BODY
            )
        except (OSError, HTTPException) as error:
            # No complete response was observed, so the wrapper may still have charged or finished.
            self._close(receipt, "unknown", failure="transport outcome unknown: " + type(error).__name__)
            raise _unknown(self.bundle) from error
        return self._settle(raw, receipt, context, request)

    def _settle(
        self, raw: Any, receipt: dict[str, Any], context: dict[str, Any], request: JudgeRequest
    ) -> dict[str, Any]:
        """A complete wrapper response is one consumed attempt: valid, failed or incomplete, never retried."""
        receipt = {**receipt, "new_invocations": 1}
        metadata: dict[str, Any] = {}
        answer: bytes | None = None
        try:
            if not isinstance(raw, bytes):
                raise ValueError("wrapper response is not bytes")
            metadata = {"response_bytes": len(raw), "envelope_sha256": _sha(raw)}
            if len(raw) > MAX_BODY:
                raise ValueError("wrapper response too large")
            envelope = _strict_json(raw)
            if (
                not isinstance(envelope, dict)
                or envelope.get("ok") is not True
                or type(envelope.get("exit_code")) is not int
                or envelope["exit_code"] != 0
            ):
                raise ValueError("wrapper reported failure")
            output, stderr = envelope.get("output"), envelope.get("stderr", "")
            if not isinstance(output, str) or not isinstance(stderr, str):
                raise ValueError("wrapper metadata invalid")
            answer = output.encode("utf-8")
            markers = tool_markers(stderr)
            metadata |= {
                "ok": True,
                "exit_code": 0,
                "model": reported_model(stderr),
                "output_sha256": _sha(answer),
                "stderr_sha256": stderr_sha256(stderr),
                "cli_reported_tokens": reported_tokens(stderr),
                "observed_tool_markers": markers,
                "observed_retry_markers": len(re.findall(r"(?mi)^(?:retrying|reconnecting)\b", stderr)),
            }
            if markers:
                raise ValueError("wrapper reported tool use")
            if metadata["model"] != self.model:
                raise ValueError("wrapper reported another or no model")
            reply = self._reply(answer, context, request, "wrapper")
        except ValueError as error:
            fields = self._write_attempt(answer, metadata)
            state = "incomplete" if isinstance(error, _Incomplete) else "failed"
            self._close(receipt, state, failure=str(error), reported_model=metadata.get("model"), **fields)
            return {"status": state}
        fields = self._write_attempt(answer, metadata)
        reply_bytes = canonical_bytes(self._reply_document(reply))
        (self.bundle / "reply.json").write_bytes(reply_bytes)
        context = {**context, "response_digest": fields["response_digest"], "reply_digest": _sha(reply_bytes)}
        context["origin"] = "fresh"
        context_bytes = canonical_bytes(context)
        (self.bundle / "context.json").write_bytes(context_bytes)
        self._fresh_reply = reply
        self._close(
            receipt,
            "completed",
            reported_model=metadata["model"],
            context_digest=_sha(context_bytes),
            reply_digest=_sha(reply_bytes),
            **fields,
        )
        return {"status": "complete"}

    def _write_attempt(self, answer: bytes | None, metadata: dict[str, Any]) -> dict[str, Any]:
        """Persist the answer and sanitized wrapper facts; raw stderr and the envelope are never written."""
        wrapper = canonical_bytes(metadata)
        (self.bundle / "wrapper.json").write_bytes(wrapper)
        if answer is not None:
            (self.bundle / "response.txt").write_bytes(answer)
        return {"response_digest": _sha(answer) if answer is not None else None, "wrapper_digest": _sha(wrapper)}

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
            raise _Incomplete("Judge response incomplete")
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
        # The wrapper inspection is the original dispatch's provenance; replay only carries it forward.
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
        _check_terminal(self.bundle, receipt, context)
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
