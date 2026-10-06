"""Pinned-source verification is offline; comparison cannot update the pin."""

import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from infra.verify_spec_source import compare, verify


class SpecSourceTests(unittest.TestCase):
    def fixture(self, root):
        (root / "provenance").mkdir()
        (root / "provenance/spec.hs").write_bytes(b"pinned\n")
        manifest = {
            "schema": "sapi-lab-spec-source/v1",
            "repository": "https://github.com/example/spec",
            "revision": "a" * 40,
            "files": [
                {
                    "upstream_path": "docs/spec.hs",
                    "snapshot": "provenance/spec.hs",
                    "sha256": hashlib.sha256(b"pinned\n").hexdigest(),
                }
            ],
        }
        (root / "provenance/spec-source.json").write_text(json.dumps(manifest))

    def test_offline_verification_rejects_modified_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.fixture(root)
            self.assertTrue(verify(root)["verified"])
            (root / "provenance/spec.hs").write_text("changed")
            with self.assertRaisesRegex(ValueError, "hash"):
                verify(root)

    def test_explicit_comparison_records_diff_without_updating_snapshot_or_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.fixture(root)
            original_manifest = (root / "provenance/spec-source.json").read_bytes()
            urls = []

            def fetch(url):
                urls.append(url)
                return json.dumps({"sha": "b" * 40}).encode() if "api.github.com" in url else b"candidate\n"

            result = compare(root, "main", root / "comparison", fetch=fetch)
            self.assertEqual(result["resolved_revision"], "b" * 40)
            self.assertEqual(result["changed_files"], ["docs/spec.hs"])
            self.assertIn("-pinned", (root / "comparison/changes.diff").read_text())
            self.assertEqual((root / "provenance/spec.hs").read_bytes(), b"pinned\n")
            self.assertEqual((root / "provenance/spec-source.json").read_bytes(), original_manifest)
            self.assertEqual(len(urls), 2)
            with self.assertRaises(FileExistsError):
                compare(root, "main", root / "comparison", fetch=fetch)
