"""Unpaid guards for the live replay runner; no Docker or real wrapper calls."""

import contextlib
import copy
import io
from io import BytesIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from sapi_config_lab.coordinate.live import audit_records, main, validate_control
from sapi_config_lab.coordinate.live_evidence import reconcile_dispatches
from sapi_config_lab.coordinate.replay import load_selection
from sapi_config_lab.paths import CATALOG
from sapi_config_lab.evidence import digest
from sapi_config_lab.execute.agency import DispatchAudit, execute
from sapi_config_lab.profile import read_bindings


class LiveEvidenceTests(unittest.TestCase):
    def test_dispatch_response_and_native_records_must_all_agree(self):
        request = {
            "invocation_id": "wf/step/1",
            "operation": "ticket.classify",
            "actor": None,
            "inputs": {"ticket": {"id": "T1", "text": "late", "days_overdue": 3}},
        }
        output = {"category": "delivery", "priority": "high"}
        response = {"invocation_id": request["invocation_id"], "status": "completed", "output": output, "usage": None}
        native = [{"request": request, "response": response}]
        with tempfile.TemporaryDirectory() as directory:
            budget = {"max_attempts": 1, "operations": {"ticket.classify": 1}, "model": "gpt-6-astra"}
            audit = DispatchAudit(Path(directory) / "audit.jsonl", budget)
            wrapper = {
                "ok": True,
                "exit_code": 0,
                "output": json.dumps(output),
                "stderr": "model: gpt-6-astra\ntokens used\n14\n",
            }
            with patch("sapi_config_lab.execute.agency.urlopen", return_value=BytesIO(json.dumps(wrapper).encode())):
                execute(request, read_bindings(CATALOG), "http://unused", 1, audit=audit)
            audit.append(
                {
                    "event": "agency_response",
                    "invocation_id": request["invocation_id"],
                    "operation": request["operation"],
                    "inputs_sha256": digest(request["inputs"]),
                    "http_status": 200,
                    "status": "completed",
                    "model": "gpt-6-astra",
                }
            )
            records = audit.records()
            self.assertEqual(len(reconcile_dispatches(native, records, "gpt-6-astra")), 1)
            for corrupt in (
                "orphan_completion",
                "duplicate",
                "response_hash",
                "input_hash",
                "model",
                "sequence",
                "unknown_id",
                "forged_success",
            ):
                bad = copy.deepcopy(records)
                if corrupt == "orphan_completion":
                    bad.pop(0)
                elif corrupt == "duplicate":
                    bad += copy.deepcopy(records)
                elif corrupt == "sequence":
                    bad[0], bad[1] = bad[1], bad[0]
                elif corrupt == "model":
                    bad[1]["wrapper"]["model"] = "fake"
                elif corrupt == "unknown_id":
                    bad[1]["invocation_id"] = "unmatched"
                elif corrupt == "forged_success":
                    bad[1]["wrapper"]["ok"] = False
                else:
                    bad[1]["response_sha256" if corrupt == "response_hash" else "inputs_sha256"] = "0" * 64
                with self.subTest(corrupt=corrupt), self.assertRaises(ValueError):
                    reconcile_dispatches(native, bad, "gpt-6-astra")

    def test_source_and_image_gate_rejects_success_from_other_sources(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "source.json").write_text(json.dumps({"src/example.py": "abc"}))
            gate = {
                "schema": "sapi-lab-harbor/v1",
                "status": "passed",
                "mode": "stub",
                "source_unchanged": True,
                "source_manifest": "source.json",
                "image_id": "sha256:abc",
                "transport": {"passed": True},
                "oracle": {"passed": True},
                "nop": {"passed": True},
                "checks": [{"passed": True}],
            }
            path = root / "report.json"
            path.write_text(json.dumps(gate))
            validate_control(path, {"src/example.py": "abc"}, "sha256:abc")
            for sources, image in (
                ({"src/example.py": "edited"}, "sha256:abc"),
                ({"src/example.py": "abc"}, "sha256:changed"),
            ):
                with self.assertRaises(ValueError):
                    validate_control(path, sources, image)
            for field in ("source_unchanged", "transport", "oracle", "nop"):
                path.write_text(
                    json.dumps({**gate, field: False if field == "source_unchanged" else {"passed": False}})
                )
                with self.assertRaises(ValueError):
                    validate_control(path, {"src/example.py": "abc"}, "sha256:abc")

    def test_missing_or_tampered_selection_and_existing_output_never_dispatch(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "selection.json"
            with patch("sapi_config_lab.execute.agency.subprocess.Popen") as dispatch:
                for manifest in (
                    {},
                    {"schema": "fake", "entries": []},
                    {"source_report": {"path": str(root / "absent")}},
                ):
                    path.write_text(json.dumps(manifest))
                    with self.assertRaises((ValueError, FileNotFoundError)):
                        load_selection(path)
                for argv, message in (
                    (["--report-dir", str(root), "--preflight-only"], "never overwritten"),
                    (["--report-dir", str(root / "new"), "--max-calls", "8"], "requires --wrapper-evidence"),
                ):
                    with contextlib.redirect_stderr(io.StringIO()) as errors:
                        with self.assertRaises(SystemExit) as caught:
                            main(["--stub-report", str(root / "absent"), "--max-calls", "8", *argv])
                    self.assertEqual(caught.exception.code, 2)
                    self.assertIn(message, errors.getvalue())
                self.assertFalse((root / "new").exists())
                dispatch.assert_not_called()

    def test_truncated_audit_is_never_accepted(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "audit.jsonl"
            for invalid in ('{"event":"completion"', "null\n", "[]\n", "42\n"):
                path.write_text(invalid)
                with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                    audit_records(path)

    def test_canonical_input_hash_is_key_order_independent_and_rejects_nonfinite(self):
        self.assertEqual(digest({"b": [2], "a": "é"}), digest({"a": "é", "b": [2]}))
        with self.assertRaises(ValueError):
            digest({"x": float("nan")})
