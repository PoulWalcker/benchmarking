"""Pinned-source integrity is independent of any benchmark world or retired host."""

import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

from sapi_config_lab.pinned_source import PinnedSource, fetch_source
from tests.support.checkout_evaluation import SERVER

REVISION = "0123456789abcdef0123456789abcdef01234567"


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


class CheckoutRetryPolicyTests(unittest.TestCase):
    def world(self, execute):
        upstream = Mock()
        upstream.execute = execute
        upstream.finalize.return_value = {"tool_calls": 0}
        world = SERVER.World(upstream, {"limits": {"wall_clock_seconds": 60}})
        world.prepare()
        self.addCleanup(world.snapshot)
        return world

    def test_nonretryable_receipts_and_explicit_attempt_bounds_are_preserved(self):
        transient = {"ok": False, "error": {"code": "TRANSIENT", "message": "temporary", "retryable": True}}
        denied = {"ok": False, "error": {"code": "DENIED", "message": "denied", "retryable": False}}
        execute = Mock(return_value=denied)
        world = self.world(execute)
        self.assertEqual(world.call("source.read", operation_id="denied", max_attempts=3), denied)
        self.assertEqual(execute.call_count, 1)
        execute.return_value = transient
        self.assertEqual(world.call("source.read", operation_id="one-attempt"), transient)
        self.assertEqual(execute.call_count, 2)
        self.assertEqual(world.call("source.read", operation_id="bounded", max_attempts=3), transient)
        self.assertEqual(execute.call_count, 5)
        for arguments in ({"max_attempts": 2}, {"operation_id": "too-many", "max_attempts": 4}):
            with self.subTest(arguments=arguments), self.assertRaises(ValueError):
                world.call("source.read", **arguments)
        self.assertEqual(execute.call_count, 5)

    def test_known_retry_receipts_and_transport_policy_reset_in_each_fresh_world(self):
        transient = {"ok": False, "error": {"code": "TRANSIENT", "message": "temporary", "retryable": True}}
        success = {"ok": True, "value": {"effect": "committed"}}
        tokens = []
        for _ in range(2):
            execute = Mock(side_effect=[transient, success])
            world = self.world(execute)
            tokens.append(world._candidate_token)
            first = world.call("checkout.patch", operation_id="same-key", max_attempts=2)
            replay = world.call("checkout.patch", operation_id="same-key", max_attempts=2)
            self.assertEqual(first, success)
            self.assertEqual(replay, success)
            self.assertEqual(execute.call_count, 2)
            transport = world.transport_evidence()
            self.assertEqual(transport["events"][0]["attempts"], 2)
            self.assertEqual(transport["events"][1]["kind"], "receipt_replayed")
            self.assertFalse(transport["ambiguous_outcome"])
        self.assertNotEqual(tokens[0], tokens[1])


if __name__ == "__main__":
    unittest.main()
