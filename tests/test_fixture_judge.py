"""Offline fixture Judge at the public Judge Interface."""

from dataclasses import replace
from functools import partial
import hashlib
from http.client import RemoteDisconnected
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import URLError

from sapi_config_lab.coordinate.fixture_judge import FixtureJudge, canonical_bytes
from sapi_config_lab.coordinate.ledger import Ledger
from sapi_config_lab.coordinate.native_evaluation import evaluate_record
from sapi_config_lab.coordinate.provenance import source_manifest
from sapi_config_lab.evidence import sha256
from sapi_config_lab.harbor_integration.model_wrapper import request_wrapper
from tests.test_native_evaluation import TASK, native_record
from verification import rubric_facts
from verification.rubric import Criterion, JudgeReply, JudgeRequest, JudgeView, RubricCard, RunFacts, score
from verification.verify import canonical

CARD = RubricCard(
    "sample",
    "1",
    "local",
    (
        Criterion("protected", "Never show", 1, "deterministic", check_id="secret"),
        Criterion(
            "clarity",
            "Is this clear?",
            1,
            "llm",
            anchors={"yes": "clear", "maybe": "mixed", "no": "unclear"},
            required_evidence=("environment", "candidate"),
        ),
    ),
)
REQUEST = JudgeRequest(
    "sample",
    CARD.digest(),
    (CARD.criteria[1],),
    JudgeView({"environment": "Café source", "candidate": "Résumé report"}, {}, "run-1"),
    CARD.prompt_version,
)


def answer(request_digest):
    return json.dumps(
        {
            "request_digest": request_digest,
            "answers": {"clarity": "yes"},
            "reasons": {"clarity": "The report states Café clearly."},
            "completeness": "complete",
        }
    ).encode()


class FixtureJudgeTests(unittest.TestCase):
    def test_host_and_verifier_canonical_unicode_agree(self):
        value = {"z": "Café", "a": ["Résumé", 1]}
        self.assertEqual(canonical_bytes(value), canonical(value).encode("utf-8"))
        self.assertEqual(canonical_bytes(value), b'{"a":["R\xc3\xa9sum\xc3\xa9",1],"z":"Caf\xc3\xa9"}')
        with self.assertRaises(ValueError):
            canonical_bytes({"bad": float("nan")})
        with self.assertRaises(ValueError):
            canonical({"bad": float("nan")})

    def test_offline_bundle_replays_after_relocation_without_transport(self):
        with tempfile.TemporaryDirectory() as temp:
            bundle = Path(temp) / "first"
            reply = FixtureJudge.mock(bundle, {"options": {"mode": "stub"}}, CARD, "judge-a", answer).judge(REQUEST)
            moved = Path(temp) / "moved"
            bundle.rename(moved)
            originals = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in moved.iterdir()}
            replay = FixtureJudge.saved(
                moved, {"options": {"mode": "stub"}}, CARD, "judge-a", transport=lambda _: self.fail("transport called")
            )
            self.assertEqual(replay.judge(REQUEST), reply)
            self.assertTrue((Path(temp) / "moved-replay-receipt.json").is_file())
            self.assertEqual(
                originals, {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in moved.iterdir()}
            )
            with self.assertRaises(FileExistsError):
                replay.judge(REQUEST)

    def test_changed_native_identity_rejects_without_transport(self):
        with tempfile.TemporaryDirectory() as temp:
            bundle = Path(temp) / "bundle"
            original = {
                "metadata": "native bytes",
                "options": "original",
                "submission": "yaml-a",
                "sources": "manifest-a",
            }
            FixtureJudge.mock(bundle, original, CARD, "judge-a", answer).judge(REQUEST)
            for field in original:
                with self.subTest(field=field), self.assertRaises(ValueError):
                    FixtureJudge.saved(
                        bundle,
                        {**original, field: "changed"},
                        CARD,
                        "judge-a",
                        transport=lambda _: self.fail("transport called"),
                    ).judge(REQUEST)

    def test_saved_judge_scores_through_the_existing_interface(self):
        with tempfile.TemporaryDirectory() as temp:
            bundle = Path(temp) / "bundle"
            FixtureJudge.mock(bundle, {"source": "fixed"}, CARD, "judge-a", answer).judge(REQUEST)
            saved = FixtureJudge.saved(bundle, {"source": "fixed"}, CARD, "judge-a")
            facts = RunFacts(True, {"secret": True}, prose=dict(REQUEST.facts.prose), run_digest="run-1")
            self.assertEqual(score(CARD, facts, saved)["status"], "complete")

    def test_model_visible_prompt_withholds_protected_data(self):
        with tempfile.TemporaryDirectory() as temp:
            bundle = Path(temp) / "bundle"
            FixtureJudge.mock(bundle, {"private_fixture": "DO NOT SEND"}, CARD, "judge-a", answer).judge(REQUEST)
            prompt = (bundle / "prompt.txt").read_text()
            for secret in (
                "protected",
                "Never show",
                "secret",
                "private_fixture",
                "DO NOT SEND",
                "acceptance",
                "engine",
                "oracle",
                "verification",
            ):
                self.assertNotIn(secret, prompt)
            self.assertIn("Café source", prompt)
            self.assertIn("Résumé report", prompt)
            self.assertIn("REQUEST_DIGEST", prompt)

    def test_saved_bundle_rejects_mutated_artifacts_and_identities(self):
        with tempfile.TemporaryDirectory() as temp:
            original = Path(temp) / "original"
            FixtureJudge.mock(original, {"source": "fixed"}, CARD, "judge-a", answer).judge(REQUEST)
            changes = {
                "request.json": b"{}",
                "prompt.txt": b"altered",
                "response.txt": b"{}",
                "reply.json": b"{}",
                "receipt.json": b"{}",
                "context.json": b"{}",
            }
            for name, raw in changes.items():
                with self.subTest(name=name):
                    bundle = Path(temp) / (name + "-copy")
                    shutil.copytree(original, bundle)
                    (bundle / name).write_bytes(raw)
                    with self.assertRaises((ValueError, KeyError)):
                        FixtureJudge.saved(bundle, {"source": "fixed"}, CARD, "judge-a").judge(REQUEST)
            for changed in (
                (REQUEST, "judge-b", CARD),
                (
                    replace(
                        REQUEST,
                        facts=JudgeView({"environment": "Changed source", "candidate": "Résumé report"}, {}, "run-1"),
                    ),
                    "judge-a",
                    CARD,
                ),
                (replace(REQUEST, facts=JudgeView(dict(REQUEST.facts.prose), {}, "changed-run")), "judge-a", CARD),
                (REQUEST, "judge-a", replace(CARD, version="2")),
                (
                    REQUEST,
                    "judge-a",
                    replace(CARD, criteria=(CARD.criteria[0], replace(CARD.criteria[1], anchors={"yes": "changed"}))),
                ),
                (REQUEST, "judge-a", replace(CARD, prompt_version="new")),
            ):
                with self.subTest(changed=changed):
                    request, model, card = changed
                    with self.assertRaises(ValueError):
                        FixtureJudge.saved(original, {"source": "fixed"}, card, model).judge(request)

    def test_strict_responses_never_create_a_mock_bundle(self):
        with tempfile.TemporaryDirectory() as temp:

            def response(case, request_digest):
                valid = {
                    "request_digest": request_digest,
                    "answers": {"clarity": "yes"},
                    "reasons": {"clarity": "Report follows source."},
                    "completeness": "complete",
                }
                if case == "duplicate":
                    return (json.dumps(valid)[:-1] + ',"answers":{}}').encode()
                if case == "nonfinite":
                    return (json.dumps(valid)[:-1] + ',"total":NaN}').encode()
                if case == "oversize":
                    return b"x" * 65537
                if case == "wrong-request":
                    valid["request_digest"] = "wrong"
                elif case == "extra-field":
                    valid["total"] = 10
                elif case == "deterministic-answer":
                    valid["answers"]["protected"] = "yes"
                elif case == "missing-reason":
                    valid["reasons"] = {}
                elif case == "empty-reason":
                    valid["reasons"]["clarity"] = ""
                elif case == "long-reason":
                    valid["reasons"]["clarity"] = "x" * 2001
                elif case == "incomplete":
                    valid["completeness"] = "incomplete"
                return json.dumps(valid).encode()

            for number, case in enumerate(
                (
                    "duplicate",
                    "nonfinite",
                    "oversize",
                    "wrong-request",
                    "extra-field",
                    "deterministic-answer",
                    "missing-reason",
                    "empty-reason",
                    "long-reason",
                    "incomplete",
                )
            ):
                with self.subTest(case=case):
                    bundle = Path(temp) / str(number)
                    judge = FixtureJudge.mock(
                        bundle, {}, CARD, "judge-a", lambda digest, case=case: response(case, digest)
                    )
                    with self.assertRaises(ValueError):
                        judge.judge(REQUEST)
                    facts = RunFacts(True, {"secret": True}, prose=dict(REQUEST.facts.prose), run_digest="run-1")
                    result = score(CARD, facts, judge)
                    self.assertEqual(result["status"], "judge_failed")
                    self.assertIsNone(result["score_0_10"])
                    self.assertIsNone(result["normalized_reward"])
                    self.assertFalse(bundle.exists())

    def test_fresh_dispatch_needs_an_explicit_reservation_and_inspected_wrapper(self):
        with tempfile.TemporaryDirectory() as temp:
            for missing in ("reserved", "inspection"):
                with self.subTest(missing=missing), self.assertRaisesRegex(ValueError, "Fresh Judge dispatch needs"):
                    FixtureJudge(
                        Path(temp) / "bundle",
                        {},
                        CARD,
                        "judge-a",
                        origin="fresh",
                        transport=lambda *_: self.fail("transport called"),
                        upstream=UPSTREAM,
                        reserved=None if missing == "reserved" else lambda call: call(),
                        inspection=None if missing == "inspection" else Path(temp) / "inspection.json",
                    )
                self.assertFalse((Path(temp) / "bundle").exists())

    def test_invalid_response_and_oversize_request_yield_no_partial_score(self):
        with tempfile.TemporaryDirectory() as temp:
            facts = RunFacts(True, {"secret": True}, prose=dict(REQUEST.facts.prose), run_digest="run-1")
            invalid = FixtureJudge.mock(Path(temp) / "invalid", {}, CARD, "judge-a", lambda _: b"{}")
            result = score(CARD, facts, invalid)
            self.assertEqual(result["status"], "judge_failed")
            self.assertIsNone(result["normalized_reward"])
            huge = RunFacts(
                True, {"secret": True}, prose={"environment": "x" * 65537, "candidate": "report"}, run_digest="run-1"
            )
            blocked = FixtureJudge.mock(Path(temp) / "huge", {}, CARD, "judge-a", answer)
            self.assertEqual(score(CARD, huge, blocked)["status"], "judge_failed")
            self.assertFalse((Path(temp) / "huge").exists())


MODEL = "judge-sol"
UPSTREAM = "http://127.0.0.1:9/judge"
RUN = {
    "case": "case-1",
    "checks": {"secret": True},
    "prose": {"environment": "Café source", "candidate": "Résumé report"},
}


def asked(prompt):
    """The request digest the Adapter put in the prompt it sent."""
    return prompt.split("\nREQUEST_DIGEST\n", 1)[1].split("\n", 1)[0]


def envelope(output, *, model=MODEL, ok=True, exit_code=0, stderr=""):
    return json.dumps(
        {
            "ok": ok,
            "exit_code": exit_code,
            "output": output,
            "stderr": (f"model: {model}\n" if model else "") + "tokens used\n12\n" + stderr,
        }
    ).encode()


def rubric_evaluator(_evidence, options, *, judge, prose=None):
    """A task evaluator that consults the Judge only through the generic rubric and its catches."""
    run = {**RUN, "prose": prose} if prose is not None else RUN
    scored = rubric_facts.evaluate(
        "sample", [run], accepted=True, execution_pass=True, judge=judge, card=CARD, run_digest="run-1"
    )
    assert scored is not None
    evaluation = Path(options["evaluation"])
    evaluation.mkdir(parents=True)
    (evaluation / "report.json").write_text(json.dumps({"passed": True}))
    (evaluation / "evaluation.json").write_text(json.dumps(scored))
    return {
        "execution": True,
        "acceptance": True,
        "quality": {key: scored[key] for key in ("status", "score_0_10", "normalized_reward")},
    }


def judge_world(root: Path) -> tuple[Path, Path]:
    """A source-matched native record and an inspected Judge wrapper identity for UPSTREAM."""
    record = native_record(root / "record", source_manifest())
    wrapper = root / "wrapper.py"
    wrapper.write_text("# inspected judge wrapper\n")
    inspection = root / "inspection.json"
    inspection.write_text(
        json.dumps(
            {
                "schema": "sapi-lab-wrapper-identity/v1",
                "endpoint": UPSTREAM,
                "dispatch": "codex-exec",
                "response_substitution": False,
                "wrapper_retries": 0,
                "model": MODEL,
                "provider_internal_retries": "unknown",
                "files": [{"name": "wrapper.py", "path": str(wrapper), "sha256": sha256(wrapper)}],
            }
        )
    )
    return record, inspection


def fresh_factory(inspection: Path, transport, *, upstream=UPSTREAM, **options):
    """The explicit composition a task root supplies to evaluate_record."""

    def build(*, directory, native_identity, reserved, **_context):
        return FixtureJudge.fresh(
            directory,
            native_identity,
            CARD,
            MODEL,
            reserved=reserved,
            transport=transport,
            upstream=upstream,
            inspection=inspection,
            **options,
        )

    return build


def valid_wrapper(upstream, prompt, timeout, maximum):
    return envelope(answer(asked(prompt)).decode())


class FreshDispatchTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.record, self.inspection = judge_world(self.root)

    def factory(self, transport, *, upstream=UPSTREAM, **options):
        return fresh_factory(self.inspection, transport, upstream=upstream, **options)

    def judged(self, transport, name="judged", evaluator=rubric_evaluator, **request):
        output = self.root / name
        result = evaluate_record(
            TASK,
            evaluator,
            {"record": str(self.record), "output": str(output), "dispatch": True, **request},
            judge_factory=self.factory(transport),
        )
        return result, output

    def test_reservation_and_start_receipt_are_durable_before_the_only_transport_call(self):
        calls = []
        output = self.root / "judged"

        def transport(upstream, prompt, timeout, maximum):
            events = json.loads((output / "ledger.json").read_text())["events"]
            receipt = json.loads((output / "judge/receipt.json").read_text())
            calls.append(
                (upstream, timeout, [(e["phase"], e["status"]) for e in events], receipt["state"], receipt["origin"])
            )
            return envelope(answer(asked(prompt)).decode())

        result, _ = self.judged(transport)
        self.assertEqual(calls, [(UPSTREAM, 180, [("judge", "unknown")], "dispatch_started", "fresh")])
        self.assertEqual(result["quality"]["status"], "complete")
        self.assertEqual(json.loads((output / "ledger.json").read_text())["events"][0]["status"], "passed")
        receipt = json.loads((output / "judge/receipt.json").read_text())
        self.assertEqual(
            (receipt["state"], receipt["origin"], receipt["new_invocations"], receipt["reported_model"]),
            ("completed", "fresh", 1, MODEL),
        )
        self.assertEqual(receipt["reservation"]["index"], 0)
        for name, field in (
            ("response.txt", "response_digest"),
            ("wrapper.json", "wrapper_digest"),
            ("reply.json", "reply_digest"),
            ("context.json", "context_digest"),
        ):
            self.assertEqual(sha256(output / "judge" / name), receipt[field], name)

    def test_second_request_changed_source_and_wrong_host_events_refuse_before_dispatch(self):
        calls = []

        def transport(upstream, prompt, timeout, maximum):
            calls.append(prompt)
            return envelope(answer(asked(prompt)).decode())

        def twice(evidence, options, *, judge):
            result = rubric_evaluator(evidence, options, judge=judge)
            with self.assertRaisesRegex(ValueError, "exactly one request"):
                judge.judge(REQUEST)
            return result

        result, _ = self.judged(transport, "twice", evaluator=twice)
        self.assertEqual((len(calls), result["quality"]["status"]), (1, "complete"))

        def moved(evidence, options, *, judge):
            with patch("sapi_config_lab.coordinate.native_evaluation.source_manifest", return_value={}):
                return rubric_evaluator(evidence, options, judge=judge)

        with self.assertRaisesRegex(ValueError, "Judge dispatch incomplete"):
            self.judged(transport, "moved", evaluator=moved)
        receipt = json.loads((self.root / "moved/judge/receipt.json").read_text())
        self.assertEqual((receipt["state"], receipt["new_invocations"]), ("failed", 0))
        self.assertTrue(receipt["failure"].startswith("reservation refused"))
        self.assertFalse((self.root / "moved/ledger.json").exists())

        for fault in ("completed", "phase", "count"):
            with self.subTest(fault=fault):
                ledger = Ledger.open(
                    self.root / f"{fault}.json", ceilings={"judge": 2, "runtime": 1}, stop_after_failure=False
                )
                phase, count = {"phase": ("runtime", 1), "count": ("judge", 2)}.get(fault, ("judge", 1))
                index = ledger.reserve(phase, "host/" + fault, count, self.root / "report.json", 2)
                if fault == "completed":
                    ledger.finish(index, True)
                event = json.loads(ledger.path.read_text())["events"][index]
                proof = {"ledger": str(ledger.path), "index": index, "event": event}
                with self.assertRaisesRegex(ValueError, "Judge dispatch incomplete"):
                    self.judged(transport, fault, reservation=proof)
                self.assertEqual(json.loads(ledger.path.read_text())["events"], [event])
                receipt = json.loads((self.root / fault / "judge/receipt.json").read_text())
                self.assertEqual((receipt["state"], receipt["new_invocations"]), ("failed", 0))
        self.assertEqual(len(calls), 1)

    def test_unproven_dispatch_never_passes_as_fresh_judging(self):
        def transport(upstream, prompt, timeout, maximum):
            return envelope(answer(asked(prompt)).decode())

        self.judged(transport, "earlier")
        mocked = self.root / "mocked"
        FixtureJudge.mock(mocked, self.record.joinpath("native-task.json").read_bytes(), CARD, MODEL, answer).judge(
            REQUEST
        )

        class Rogue:
            """Consumes the real reservation with work that is not the Adapter's own dispatch."""

            mode, model = "wrapper", MODEL

            def __init__(self, directory, reserved, work):
                self.directory, self.reserved, self.work = directory, reserved, work

            def judge(self, request):
                self.reserved(lambda: self.work(self.directory))
                return JudgeReply(
                    {"clarity": "yes"},
                    {"clarity": "fabricated"},
                    {
                        "mode": "wrapper",
                        "model": MODEL,
                        "prompt_version": request.prompt_version,
                        "prompt_digest": "0" * 64,
                        "response_digest": "0" * 64,
                        "run_digest": request.facts.run_digest,
                    },
                )

        def substitute(source):
            def work(directory):
                for path in source.iterdir():
                    shutil.copyfile(path, directory / path.name)
                return {"status": "complete"}

            return work

        works = {
            "no-transport": lambda _directory: {"status": "complete"},
            "mocked-receipt": substitute(mocked),
            "other-reservation": substitute(self.root / "earlier/judge"),
        }
        for name, work in works.items():
            with self.subTest(name=name):
                factory = self.factory(transport)

                def rogue(*, directory, reserved, factory=factory, work=work, **context):
                    factory(directory=directory, reserved=reserved, **context)
                    return Rogue(directory, reserved, work)

                output = self.root / name
                with self.assertRaisesRegex(ValueError, "Judge dispatch incomplete"):
                    evaluate_record(
                        TASK,
                        rubric_evaluator,
                        {"record": str(self.record), "output": str(output), "dispatch": True},
                        judge_factory=rogue,
                    )
                self.assertIsNone(json.loads((output / "result.json").read_text())["quality"]["score_0_10"])
                self.assertEqual(json.loads((output / "ledger.json").read_text())["events"][0]["status"], "failed")

        for name, alter in (
            ("deleted", lambda path: path.unlink()),
            ("tampered", lambda path: path.write_bytes(path.read_bytes().replace(b'"yes"', b'"no"'))),
        ):
            with self.subTest(name=name):

                def altering(*, directory, reserved, alter=alter, **context):
                    def after_dispatch(call):
                        return reserved(lambda: (call(), alter(directory / "response.txt"))[0])

                    return self.factory(transport)(directory=directory, reserved=after_dispatch, **context)

                output = self.root / name
                with self.assertRaisesRegex(ValueError, "Judge dispatch incomplete"):
                    evaluate_record(
                        TASK,
                        rubric_evaluator,
                        {"record": str(self.record), "output": str(output), "dispatch": True},
                        judge_factory=altering,
                    )
                self.assertEqual(json.loads((output / "ledger.json").read_text())["events"][0]["status"], "failed")

        def fabricated(_evidence, options, *, judge):
            return {
                "execution": True,
                "acceptance": True,
                "quality": {"status": "complete", "score_0_10": 10, "normalized_reward": 1},
            }

        with self.assertRaisesRegex(ValueError, "Judge dispatch incomplete"):
            self.judged(transport, "fabricated", evaluator=fabricated)
        receipt = json.loads((self.root / "fabricated/judge/receipt.json").read_text())
        self.assertEqual((receipt["state"], receipt["new_invocations"]), ("skipped", 0))
        self.assertFalse((self.root / "fabricated/ledger.json").exists())

        def saved(*, directory, native_identity, **_context):
            return FixtureJudge.saved(self.root / "earlier/judge", native_identity, CARD, MODEL)

        with self.assertRaisesRegex(ValueError, "initialize an undispatched fresh receipt"):
            evaluate_record(
                TASK,
                rubric_evaluator,
                {"record": str(self.record), "output": str(self.root / "saved"), "dispatch": True},
                judge_factory=saved,
            )
        self.assertFalse((self.root / "saved/ledger.json").exists())

    def test_complete_bad_responses_consume_one_failed_attempt_without_retry(self):
        def answered(**changes):
            document = {**json.loads(answer("{digest}")), **changes}
            return json.dumps(document)

        responses = {
            "wrapper-failed": lambda d: envelope(answer(d).decode(), ok=False, exit_code=1),
            "wrong-model": lambda d: envelope(answer(d).decode(), model="judge-astra"),
            "missing-model": lambda d: envelope(answer(d).decode(), model=None),
            "malformed": lambda d: envelope("not json"),
            "oversized-answer": lambda d: envelope(answered(request_digest=d, reasons={"clarity": "x" * 70000})),
            "oversized-envelope": lambda d: b" " * (1_048_576 + 1),
            "tool-marker": lambda d: envelope(answer(d).decode(), stderr="exec\nls\n"),
            "incomplete": lambda d: envelope(answered(request_digest=d, completeness="incomplete")),
        }
        for name, response in responses.items():
            with self.subTest(name=name):
                calls = []

                def transport(upstream, prompt, timeout, maximum, calls=calls, response=response):
                    calls.append(prompt)
                    return response(asked(prompt))

                with self.assertRaisesRegex(ValueError, "Judge dispatch incomplete"):
                    self.judged(transport, name)
                output = self.root / name
                self.assertEqual(len(calls), 1)
                result = json.loads((output / "result.json").read_text())
                self.assertEqual((result["acceptance"], result["quality"]["score_0_10"]), (True, None))
                self.assertEqual(json.loads((output / "ledger.json").read_text())["events"][0]["status"], "failed")
                receipt = json.loads((output / "judge/receipt.json").read_text())
                state = "incomplete" if name == "incomplete" else "failed"
                self.assertEqual((receipt["state"], receipt["new_invocations"]), (state, 1))
                self.assertFalse((output / "judge/reply.json").exists())
                self.assertEqual(sha256(output / "judge/wrapper.json"), receipt["wrapper_digest"])
                for path in (output / "judge").iterdir():
                    self.assertNotIn(b"tokens used", path.read_bytes(), path.name)

    def test_ambiguous_transport_outcomes_stay_unknown_through_rubric_catches_and_block_spending(self):
        failures = {
            "timeout": TimeoutError("timed out"),
            "url-timeout": URLError(TimeoutError("timed out")),
            "disconnect": RemoteDisconnected("Remote end closed connection without response"),
            "reset": ConnectionResetError("reset by peer"),
            "uncategorized": RuntimeError("transport bug after the start receipt"),
        }
        for name, failure in failures.items():
            with self.subTest(name=name):
                series = self.root / ("series-" + name)
                calls = []

                def transport(upstream, prompt, timeout, maximum, failure=failure, calls=calls):
                    calls.append(prompt)
                    raise failure

                shared = {"series_dir": str(series), "series_ceiling": ["judge=2"]}
                with self.assertRaises(subprocess.TimeoutExpired):
                    self.judged(transport, name, **shared)
                output = self.root / name
                scored = json.loads((output / "evaluation/evaluation.json").read_text())
                self.assertEqual(scored["status"], "judge_failed")
                result = json.loads((output / "result.json").read_text())
                self.assertEqual((result["acceptance"], result["quality"]), (True, None))
                self.assertEqual(json.loads((series / "ledger.json").read_text())["events"][0]["status"], "unknown")
                self.assertIn(
                    json.loads((output / "judge/receipt.json").read_text())["state"], ("unknown", "dispatch_started")
                )
                with self.assertRaisesRegex(ValueError, "Judge dispatch incomplete"):
                    self.judged(transport, name + "-next", **shared)
                self.assertEqual(len(calls), 1)
                self.assertEqual(len(json.loads((series / "ledger.json").read_text())["events"]), 1)
                self.assertIn(
                    "unknown outcome",
                    json.loads((self.root / (name + "-next/judge/receipt.json")).read_text())["failure"],
                )

    def test_local_http_wrapper_completes_or_leaves_timeouts_and_disconnects_unknown(self):
        release = threading.Event()
        self.addCleanup(release.set)

        class Wrapper(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_POST(self):
                prompt = json.loads(self.rfile.read(int(self.headers["Content-Length"])))["prompt"]
                if self.path == "/hang":
                    release.wait(10)
                    return
                if self.path == "/drop":
                    self.close_connection = True
                    return
                raw = envelope(answer(asked(prompt)).decode())
                self.send_response(200)
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

        server = ThreadingHTTPServer(("127.0.0.1", 0), Wrapper)
        self.addCleanup(server.server_close)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.shutdown)
        inspection = json.loads(self.inspection.read_text())
        for path, expected in (("/ok", "completed"), ("/hang", "unknown"), ("/drop", "unknown")):
            with self.subTest(path=path):
                upstream = f"http://127.0.0.1:{server.server_address[1]}{path}"
                self.inspection.write_text(json.dumps({**inspection, "endpoint": upstream}))
                output = self.root / path.strip("/")
                factory = self.factory(request_wrapper, upstream=upstream, timeout=0.5)
                request = {"record": str(self.record), "output": str(output), "dispatch": True}
                if expected == "completed":
                    result = evaluate_record(TASK, rubric_evaluator, request, judge_factory=factory)
                    self.assertEqual(result["quality"]["status"], "complete")
                else:
                    with self.assertRaises(subprocess.TimeoutExpired):
                        evaluate_record(TASK, rubric_evaluator, request, judge_factory=factory)
                self.assertEqual(json.loads((output / "judge/receipt.json").read_text())["state"], expected)
                status = json.loads((output / "ledger.json").read_text())["events"][0]["status"]
                self.assertEqual(status, "passed" if expected == "completed" else "unknown")

    def test_completed_invocation_survives_later_scoring_failure_and_unspent_requests_stay_unreserved(self):
        calls = []

        def transport(upstream, prompt, timeout, maximum):
            calls.append(prompt)
            return envelope(answer(asked(prompt)).decode())

        def scoring_fails(evidence, options, *, judge):
            rubric_evaluator(evidence, options, judge=judge)
            raise RuntimeError("scoring failed after a valid reply")

        with self.assertRaisesRegex(RuntimeError, "scoring failed"):
            self.judged(transport, "scoring", evaluator=scoring_fails)
        self.assertEqual(json.loads((self.root / "scoring/ledger.json").read_text())["events"][0]["status"], "passed")
        self.assertEqual(json.loads((self.root / "scoring/judge/receipt.json").read_text())["state"], "completed")
        self.assertFalse((self.root / "scoring/result.json").exists())

        oversized = partial(rubric_evaluator, prose={"environment": "x" * 65537, "candidate": "report"})
        with self.assertRaisesRegex(ValueError, "Judge dispatch incomplete"):
            self.judged(transport, "oversized", evaluator=oversized)
        receipt = json.loads((self.root / "oversized/judge/receipt.json").read_text())
        self.assertEqual((receipt["state"], receipt["new_invocations"]), ("failed", 0))
        self.assertIn("exceeds 64 KiB", receipt["failure"])
        self.assertFalse((self.root / "oversized/ledger.json").exists())

        def unconsulted(_evidence, _options, *, judge):
            return {"execution": True, "acceptance": True, "quality": None}

        ledger = Ledger.open(self.root / "host.json", ceilings={"judge": 1}, stop_after_failure=False)
        with self.assertRaisesRegex(ValueError, "Judge dispatch incomplete"):
            with ledger.reserved("judge", "host/unconsulted", 1, self.root / "report.json", 1):
                proof = {"ledger": str(ledger.path), "index": 0, "event": ledger.data["events"][0]}
                self.judged(transport, "unconsulted", evaluator=unconsulted, reservation=proof)
        self.assertEqual(json.loads(ledger.path.read_text())["events"][0]["status"], "failed")
        receipt = json.loads((self.root / "unconsulted/judge/receipt.json").read_text())
        self.assertEqual((receipt["state"], receipt["new_invocations"]), ("skipped", 0))
        self.assertEqual(json.loads((self.root / "unconsulted/result.json").read_text())["acceptance"], True)
        self.assertEqual(len(calls), 1)

    def test_judge_timeout_is_bounded_by_the_180_second_ceiling(self):
        for timeout in (0, -1, 181):
            with self.subTest(timeout=timeout), self.assertRaisesRegex(ValueError, "at most 180 seconds"):
                fresh_factory(self.inspection, lambda *_: self.fail("transport called"), timeout=timeout)(
                    directory=self.root / f"timeout-{timeout}", native_identity=b"{}", reserved=lambda call: call()
                )
            self.assertFalse((self.root / f"timeout-{timeout}").exists())

    def test_fresh_bundle_replays_exactly_offline_with_its_original_attribution(self):
        result, output = self.judged(valid_wrapper)
        fresh = json.loads((output / "evaluation/evaluation.json").read_text())
        metadata = json.loads((output / "judge/wrapper.json").read_text())
        self.assertEqual(metadata["output_sha256"], sha256(output / "judge/response.txt"))
        self.assertNotIn("stderr", metadata)
        saved = FixtureJudge.saved(
            output / "judge",
            (self.record / "native-task.json").read_bytes(),
            CARD,
            MODEL,
            transport=lambda *_: self.fail("transport called"),
            replay_receipt=self.root / "replay.json",
        )
        replayed = rubric_facts.evaluate(
            "sample", [RUN], accepted=True, execution_pass=True, judge=saved, card=CARD, run_digest="run-1"
        )
        assert replayed is not None
        self.assertEqual((replayed["status"], replayed["judge"]), ("complete", fresh["judge"]))
        self.assertEqual(replayed["score_0_10"], result["quality"]["score_0_10"])
        replay = json.loads((self.root / "replay.json").read_text())
        self.assertEqual((replay["state"], replay["origin"], replay["new_invocations"]), ("replayed", "fresh", 0))
        changed = FixtureJudge.saved(output / "judge", b"{}", CARD, MODEL, replay_receipt=self.root / "changed.json")
        refused = rubric_facts.evaluate(
            "sample", [RUN], accepted=True, execution_pass=True, judge=changed, card=CARD, run_digest="run-1"
        )
        assert refused is not None
        self.assertEqual((refused["status"], refused["score_0_10"]), ("judge_failed", None))
