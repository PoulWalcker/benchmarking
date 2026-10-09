"""Offline fixture Judge at the public Judge Interface."""

from dataclasses import replace
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import unittest

from sapi_config_lab.coordinate.fixture_judge import FixtureJudge, canonical_bytes
from verification.rubric import Criterion, JudgeRequest, JudgeView, RubricCard, RunFacts, score
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

    def test_live_transport_stays_disabled(self):
        with tempfile.TemporaryDirectory() as temp, self.assertRaisesRegex(ValueError, "transport is disabled"):
            FixtureJudge(
                Path(temp) / "bundle",
                {},
                CARD,
                "judge-a",
                origin="fresh",
                transport=lambda _: self.fail("transport called"),
            )

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
