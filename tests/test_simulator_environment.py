"""Pinned-source integrity and the real upstream simulator behind the hosted tool listener; no models."""

import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from sapi_config_lab.execute import autowfbench
from sapi_config_lab.execute.autowfbench import start_environment
from sapi_config_lab.pinned_source import PinnedSource, fetch_source
from tests.support.checkout_controls import CHECKOUT_INCOMPLETE_ACTIONS, CHECKOUT_ORACLE_ACTIONS
from tests.support.pinned import AVAILABLE, SOURCE

REVISION = "0123456789abcdef0123456789abcdef01234567"


def post(connection, path, request, *, token=None):
    token = connection["access_token"] if token is None else token
    req = Request(
        connection["base_url"] + path,
        data=json.dumps(request).encode(),
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + token},
    )
    try:
        with urlopen(req, timeout=5) as response:
            return json.load(response)
    except HTTPError as error:
        error.close()
        raise


class SourceCacheTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.manifest = self.root / "manifest.json"
        self.content = b"# pinned fixture, not a simulator\n"
        self.manifest.write_text(
            json.dumps(
                {
                    "schema": "sapi-lab-upstream-source/v1",
                    "repository": "https://github.com/aleski-green/AutoWFBench",
                    "revision": REVISION,
                    "files": {"module.py": hashlib.sha256(self.content).hexdigest()},
                }
            )
        )
        self.source = PinnedSource(self.manifest, self.root / "cache" / REVISION)

    def test_explicit_fetch_is_pinned_atomic_and_reused_offline(self):
        calls = []

        def download(url):
            calls.append(url)
            return self.content

        source = fetch_source(self.source, fetch=download)
        self.assertEqual(source.name, REVISION)
        self.assertTrue(self.source.verify()["verified"])
        self.assertEqual(fetch_source(self.source, fetch=download), source)
        self.assertEqual(len(calls), 1)
        self.assertEqual(f"https://raw.githubusercontent.com/aleski-green/AutoWFBench/{REVISION}/module.py", calls[0])
        (source / "module.py").write_text("tampered")
        with self.assertRaisesRegex(ValueError, "hash mismatch"):
            fetch_source(self.source, fetch=download)
        self.assertEqual(len(calls), 1)
        self.assertEqual((source / "module.py").read_text(), "tampered")

    def test_failed_download_never_publishes_partial_cache(self):
        with self.assertRaisesRegex(ValueError, "hash mismatch"):
            fetch_source(self.source, fetch=lambda url: b"incorrect")
        self.assertEqual(list((self.root / "cache").iterdir()), [])

    def test_import_shadow_and_symlink_sources_are_rejected(self):
        source = fetch_source(self.source, fetch=lambda url: self.content)
        (source / "json.py").write_text("untrusted import")
        with self.assertRaisesRegex(ValueError, "Unexpected files"):
            self.source.verify()
        (source / "json.py").unlink()
        (source / "module.py").unlink()
        outside = self.root / "outside.py"
        outside.write_bytes(self.content)
        (source / "module.py").symlink_to(outside)
        with self.assertRaisesRegex(ValueError, "unsafe"):
            self.source.verify()


@unittest.skipUnless(AVAILABLE, "Requires the pinned upstream source cache and the benchmark extra")
class OriginalEnvironmentTests(unittest.TestCase):
    def test_known_receipt_replay_does_not_duplicate_side_effects(self):
        operation, arguments = CHECKOUT_ORACLE_ACTIONS[3]
        with start_environment(SOURCE) as session:
            request = {"operation": operation, "arguments": arguments, "operation_id": "patch"}
            # Discard the first response; the retained receipt must prevent another write.
            post(session.connection, "/tools", request)
            replay = post(session.connection, "/tools", request)
            self.assertTrue(replay["ok"])
            conflict = session.call(operation, {**arguments, "unexpected": True}, operation_id="patch")
            self.assertEqual(conflict["error"]["code"], "PROJECT_OPERATION_ID_CONFLICT")
            evidence = session.finalize()
            self.assertEqual(evidence["tool_calls"], 1)
            self.assertEqual(len(evidence["events"]), 1)
            self.assertIn(arguments["new"], evidence["final"]["source"])
            self.assertNotIn(arguments["old"], evidence["final"]["source"])

    def test_unknown_response_after_committed_side_effect_stops_all_redispatch(self):
        operation, arguments = CHECKOUT_ORACLE_ACTIONS[3]
        with start_environment(SOURCE) as session:

            def lost_response(*args, original=autowfbench._post, **kwargs):
                original(*args, **kwargs)
                raise TimeoutError("Injected response loss after upstream commit")

            with patch("sapi_config_lab.execute.autowfbench._post", side_effect=lost_response) as dispatch:
                first = session.call(operation, arguments, operation_id="effect", max_attempts=3)
                same = session.call(operation, arguments, operation_id="effect", max_attempts=3)
                different = session.call(operation, arguments, operation_id="different")
                self.assertEqual(dispatch.call_count, 1)
            self.assertEqual(first, same)
            self.assertEqual(different["error"]["code"], "PROJECT_AMBIGUOUS_OUTCOME")
            self.assertFalse(first["error"]["retryable"])
            self.assertTrue(session.transport_evidence()["ambiguous_outcome"])
            evidence = session.finalize()
            self.assertEqual(evidence["tool_calls"], 1)
            self.assertEqual(len(evidence["events"]), 1)
            self.assertIn(arguments["new"], evidence["final"]["source"])

    def test_nonretryable_errors_and_explicit_attempt_limits_are_preserved(self):
        transient = {"ok": False, "error": {"code": "TRANSIENT", "message": "temporary", "retryable": True}}
        denied = {"ok": False, "error": {"code": "DENIED", "message": "denied", "retryable": False}}
        with start_environment(SOURCE) as session:
            with patch("sapi_config_lab.execute.autowfbench._post", return_value=denied) as dispatch:
                result = session.call("source.read", operation_id="denied", max_attempts=3)
                self.assertEqual(result, denied)
                self.assertEqual(dispatch.call_count, 1)
            with patch("sapi_config_lab.execute.autowfbench._post", return_value=transient) as dispatch:
                result = session.call("source.read", operation_id="one-attempt")
                self.assertEqual(result, transient)
                self.assertEqual(dispatch.call_count, 1)
                result = session.call("source.read", operation_id="bounded", max_attempts=3)
                self.assertEqual(result, transient)
                self.assertEqual(dispatch.call_count, 4)
            with self.assertRaises(ValueError):
                session.call("source.read", max_attempts=2)
            with self.assertRaises(ValueError):
                session.call("source.read", operation_id="too-many", max_attempts=4)
            self.assertEqual(session.finalize()["tool_calls"], 0)

    def test_operation_receipts_and_transient_retry_policy_reset_with_new_session(self):
        operation, arguments = CHECKOUT_ORACLE_ACTIONS[3]
        transient = {"ok": False, "error": {"code": "TRANSIENT", "message": "temporary", "retryable": True}}
        original = autowfbench._post
        initial = []
        for _ in range(2):
            with start_environment(SOURCE) as session:
                attempts = 0

                def dispatch(*args, **kwargs):
                    nonlocal attempts
                    attempts += 1
                    return transient if attempts == 1 else original(*args, **kwargs)

                with patch("sapi_config_lab.execute.autowfbench._post", side_effect=dispatch):
                    result = session.call(operation, arguments, operation_id="same-key", max_attempts=2)
                    replay = session.call(operation, arguments, operation_id="same-key", max_attempts=2)
                self.assertTrue(result["ok"])
                self.assertEqual(replay, result)
                self.assertEqual(attempts, 2)
                evidence = session.finalize()
                initial.append(evidence["initial"]["source"])
                self.assertIn(arguments["old"], evidence["initial"]["source"])
                self.assertEqual(evidence["tool_calls"], 1)
                events = session.transport_evidence()["events"]
                self.assertEqual(events[0]["attempts"], 2)
                self.assertEqual(events[1]["kind"], "receipt_replayed")
                self.assertFalse(session.transport_evidence()["ambiguous_outcome"])
        self.assertEqual(initial[0], initial[1])

    def test_oracle_uses_original_effects_events_and_checks(self):
        with start_environment(SOURCE, seed=2) as session:
            self.assertEqual(session.definition["limits"], {"wall_clock_seconds": 120, "tool_calls": 30})
            observed = []
            for operation, arguments in CHECKOUT_ORACLE_ACTIONS:
                observed.append(post(session.connection, "/tools", {"operation": operation, "arguments": arguments}))
            self.assertFalse(observed[2]["value"]["passed"])
            self.assertTrue(observed[4]["value"]["passed"])
            evidence = session.finalize()
            self.assertTrue(all(evidence["checks"].values()))
            self.assertEqual(evidence["tool_calls"], 5)
            self.assertEqual(len(evidence["events"]), 5)
            self.assertEqual({event["source"] for event in evidence["events"]}, {"environment"})
            self.assertEqual({event["source"] for event in evidence["verification"]}, {"verification"})
            evidence["checks"]["eur_fixed"] = False
            self.assertTrue(session.finalize()["checks"]["eur_fixed"])
            late = post(session.connection, "/tools", {"operation": "tests.run", "arguments": {}})
            self.assertEqual(late["error"]["code"], "RUN_CLOSED")
            self.assertEqual(session.finalize()["tool_calls"], 5)

    def test_new_session_resets_state_and_does_not_accept_other_session_token(self):
        with start_environment(SOURCE) as first, start_environment(SOURCE) as second:
            for operation, arguments in CHECKOUT_ORACLE_ACTIONS:
                first.call(operation, arguments)
            for operation, arguments in CHECKOUT_INCOMPLETE_ACTIONS:
                second.call(operation, arguments)
            self.assertTrue(first.finalize()["checks"]["eur_fixed"])
            self.assertFalse(second.finalize()["checks"]["eur_fixed"])
            with self.assertRaises(HTTPError) as error:
                post(
                    second.connection,
                    "/tools",
                    {"operation": "tests.run", "arguments": {}},
                    token=first.connection["access_token"],
                )
            self.assertEqual(error.exception.code, 401)
        with start_environment(SOURCE) as reset:
            self.assertFalse(reset.call("tests.run")["value"]["passed"])
            self.assertFalse(reset.finalize()["checks"]["eur_fixed"])

    def test_candidate_cannot_finalize_read_state_or_change_tool_contract(self):
        with start_environment(SOURCE) as session:
            for path in ("/admin/finalize", "/state", "/evaluate"):
                with self.assertRaises(HTTPError) as error:
                    post(session.connection, path, {})
                self.assertEqual(error.exception.code, 404)
            with self.assertRaises(HTTPError) as error:
                post(
                    session.connection, "/tools", {"operation": "tests.run", "arguments": {}, "url": "http://elsewhere"}
                )
            self.assertEqual(error.exception.code, 400)
            unknown = post(session.connection, "/tools", {"operation": "admin.finalize", "arguments": {}})
            self.assertEqual(unknown["error"]["code"], "UNKNOWN_TOOL")
            self.assertEqual(session.finalize()["tool_calls"], 1)

    def test_original_tool_budget_and_patch_restrictions_are_not_replaced(self):
        with start_environment(SOURCE) as session:
            invalid = session.call("checkout.patch", {"old": "def checkout", "new": "import os\ndef checkout"})
            self.assertFalse(invalid["ok"])
            for _ in range(29):
                session.call("source.read")
            exhausted = session.call("tests.run")
            self.assertEqual(exhausted["error"]["code"], "TOOL_LIMIT")
            evidence = session.finalize()
            self.assertEqual(evidence["tool_calls"], 31)
            self.assertFalse(evidence["checks"]["safe_patch"])
            self.assertEqual(evidence["initial"]["source"], evidence["final"]["source"])

    def test_mutated_cache_is_rejected_before_startup(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source"
            shutil.copytree(SOURCE.root, source)
            path = source / "autowfbench/runtime/environment.py"
            path.write_text(path.read_text() + "\n# changed\n")
            with self.assertRaisesRegex(ValueError, "hash mismatch"):
                start_environment(PinnedSource(SOURCE.manifest, source))

    def test_close_is_idempotent_and_stops_candidate_calls(self):
        session = start_environment(SOURCE)
        connection = session.connection
        session.close()
        session.close()
        with self.assertRaisesRegex(RuntimeError, "closed"):
            session.call("tests.run")
        with self.assertRaises(OSError):
            post(connection, "/tools", {"operation": "tests.run", "arguments": {}})


if __name__ == "__main__":
    unittest.main()
