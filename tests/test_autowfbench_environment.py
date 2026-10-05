"""Pinned-source integrity and actual local upstream simulator controls; no models."""

import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from sapi_config_lab.runtime.autowfbench import PINNED_REVISION, fetch_source, start_environment, verify_source
from sapi_config_lab.runtime import autowfbench
from tests.support.checkout_controls import CHECKOUT_INCOMPLETE_ACTIONS, CHECKOUT_ORACLE_ACTIONS

SOURCE = Path(
    os.environ.get("SAPI_AUTOWFBENCH_SOURCE", Path(__file__).parents[1] / ".cache/autowfbench" / PINNED_REVISION)
)


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
                    "revision": PINNED_REVISION,
                    "files": {"module.py": hashlib.sha256(self.content).hexdigest()},
                }
            )
        )
        self.patcher = patch("sapi_config_lab.runtime.autowfbench.MANIFEST", self.manifest)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def test_explicit_fetch_is_pinned_atomic_and_reused_offline(self):
        calls = []

        def download(url):
            calls.append(url)
            return self.content

        source = fetch_source(self.root / "cache", fetch=download)
        self.assertEqual(source.name, PINNED_REVISION)
        self.assertTrue(verify_source(source)["verified"])
        self.assertEqual(fetch_source(self.root / "cache", fetch=download), source)
        self.assertEqual(len(calls), 1)
        self.assertIn(f"/{PINNED_REVISION}/module.py", calls[0])
        (source / "module.py").write_text("tampered")
        with self.assertRaisesRegex(ValueError, "hash mismatch"):
            fetch_source(self.root / "cache", fetch=download)
        self.assertEqual(len(calls), 1)
        self.assertEqual((source / "module.py").read_text(), "tampered")

    def test_failed_download_never_publishes_partial_cache(self):
        with self.assertRaisesRegex(ValueError, "hash mismatch"):
            fetch_source(self.root / "cache", fetch=lambda url: b"incorrect")
        self.assertEqual(list((self.root / "cache").iterdir()), [])

    def test_import_shadow_and_symlink_sources_are_rejected(self):
        source = fetch_source(self.root / "cache", fetch=lambda url: self.content)
        (source / "json.py").write_text("untrusted import")
        with self.assertRaisesRegex(ValueError, "Unexpected files"):
            verify_source(source)
        (source / "json.py").unlink()
        (source / "module.py").unlink()
        outside = self.root / "outside.py"
        outside.write_bytes(self.content)
        (source / "module.py").symlink_to(outside)
        with self.assertRaisesRegex(ValueError, "unsafe"):
            verify_source(source)


@unittest.skipUnless(SOURCE.is_dir(), "Explicitly fetch the pinned AutoWFBench source cache first")
class OriginalEnvironmentTests(unittest.TestCase):
    def test_crm_original_case_passes_with_explicit_bounded_transient_retry(self):
        with start_environment(SOURCE, "crm-lead-qualification", seed=2) as session:
            inquiry = session.call("inquiry.read")["value"]
            session.call("documents.read")
            session.call("research.read")
            facts = session.call("customer.ask", {"questions": ["Please confirm budget, timeline and support needs."]})[
                "value"
            ]
            changes = {**facts, "status": "Qualified", "next_action": "discovery_call", "owner": "sales_coordinator"}
            result = session.call("crm.update", {"changes": changes}, operation_id="update", max_attempts=2)
            self.assertTrue(result["ok"])
            session.call(
                "followup.create",
                {"lead_id": inquiry["lead_id"], "type": "discovery_call", "status": "pending_scheduling"},
                operation_id="followup",
            )
            session.call(
                "customer.send",
                {
                    "recipient": inquiry["contact"],
                    "body": "Please share availability for discovery; scope and timing remain subject to assessment.",
                },
                operation_id="send",
            )
            session.call("crm.read")
            evidence = session.finalize()
            self.assertTrue(all(evidence["checks"].values()))
            updates = [e for e in evidence["events"] if e["data"]["operation"] == "crm.update"]
            self.assertEqual([e["data"]["result"]["ok"] for e in updates], [False, True])
            self.assertEqual(evidence["tool_calls"], 9)
            policy = session.transport_evidence()
            self.assertEqual(next(e for e in policy["events"] if e.get("operation_id") == "update")["attempts"], 2)
            self.assertFalse(policy["ambiguous_outcome"])

    def test_known_receipt_replay_does_not_duplicate_create_or_send(self):
        with start_environment(SOURCE, "crm-lead-qualification") as session:
            inquiry = session.call("inquiry.read")["value"]
            actions = [
                (
                    "followup.create",
                    {"lead_id": inquiry["lead_id"], "type": "discovery_call", "status": "pending_scheduling"},
                ),
                ("customer.send", {"recipient": inquiry["contact"], "body": "Discovery is pending."}),
            ]
            for op, args in actions:
                request = {"operation": op, "arguments": args, "operation_id": op}
                # Discard the first response as if the client lost it. The proxy
                # already retained the receipt; replay must not dispatch again.
                post(session.connection, "/tools", request)
                replay = post(session.connection, "/tools", request)
                self.assertTrue(replay["ok"])
                conflict = session.call(op, {**args, "unexpected": True}, operation_id=op)
                self.assertEqual(conflict["error"]["code"], "PROJECT_OPERATION_ID_CONFLICT")
            evidence = session.finalize()
            self.assertEqual(evidence["tool_calls"], 3)
            self.assertEqual(len(evidence["final"]["followups"]), 1)
            self.assertEqual(len(evidence["final"]["messages"]), 1)

    def test_unknown_response_after_committed_side_effect_stops_all_redispatch(self):
        for operation, arguments in (
            ("followup.create", {"lead_id": "LEAD-1007", "type": "discovery_call", "status": "pending_scheduling"}),
            ("customer.send", {"recipient": "customer@crescent.example", "body": "Discovery pending."}),
        ):
            with self.subTest(operation=operation), start_environment(SOURCE, "crm-lead-qualification") as session:
                original = autowfbench._post

                def lost_response(*args, **kwargs):
                    original(*args, **kwargs)
                    raise TimeoutError("Injected response loss after upstream commit")

                with patch("sapi_config_lab.runtime.autowfbench._post", side_effect=lost_response) as dispatch:
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
                effects = (
                    evidence["final"]["followups"] if operation == "followup.create" else evidence["final"]["messages"]
                )
                self.assertEqual(len(effects), 1)

    def test_nonretryable_errors_and_explicit_attempt_limits_are_preserved(self):
        with start_environment(SOURCE, "crm-lead-qualification") as session:
            result = session.call(
                "customer.send",
                {"recipient": "wrong.example", "body": "Message"},
                operation_id="denied",
                max_attempts=3,
            )
            self.assertEqual(result["error"]["code"], "UNAUTHORIZED_RECIPIENT")
            result = session.call("crm.update", {"changes": {"status": "Qualified"}}, operation_id="one-attempt")
            self.assertEqual(result["error"]["code"], "CRM_TEMPORARILY_UNAVAILABLE")
            with self.assertRaises(ValueError):
                session.call("crm.update", {}, max_attempts=2)
            with self.assertRaises(ValueError):
                session.call("crm.update", {}, operation_id="too-many", max_attempts=4)
            evidence = session.finalize()
            self.assertEqual(evidence["tool_calls"], 2)
            self.assertEqual(evidence["final"]["messages"], [])
            self.assertEqual(evidence["final"]["lead"]["status"], "New")

    def test_operation_receipts_and_crm_failure_budget_reset_with_new_session(self):
        for _ in range(2):
            with start_environment(SOURCE, "crm-lead-qualification") as session:
                result = session.call(
                    "crm.update", {"changes": {"status": "Qualified"}}, operation_id="same-key", max_attempts=2
                )
                self.assertTrue(result["ok"])
                evidence = session.finalize()
                self.assertEqual(evidence["initial"]["lead"]["status"], "New")
                self.assertEqual(evidence["tool_calls"], 2)
                self.assertEqual(len(session.transport_evidence()["events"]), 1)

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
            shutil.copytree(SOURCE, source)
            path = source / "autowfbench/runtime/environment.py"
            path.write_text(path.read_text() + "\n# changed\n")
            with self.assertRaisesRegex(ValueError, "hash mismatch"):
                start_environment(source)

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
