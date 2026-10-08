"""Checkout freezes one terminal outcome and retains observations before hard death."""

import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from tests.test_checkout_package_environment import HOOKS, SERVER


class CheckoutFailureEvidenceTests(unittest.TestCase):
    def world(self, root, *, limit=30, upstream=None):
        upstream = upstream or Mock()
        upstream.execute.return_value = {"ok": True, "value": {"observed": True}}
        upstream.finalize.return_value = {"checks": {"completed": False}, "events": []}
        return SERVER.World(upstream, {"limits": {"wall_clock_seconds": limit}}, evidence_path=root / "world")

    def test_reserved_dispatch_is_durable_before_upstream_and_receipt_afterward(self):
        with tempfile.TemporaryDirectory() as temporary, patch.object(SERVER.threading, "Timer"):
            root = Path(temporary)
            world = self.world(root)
            world.prepare()

            def execute(*_args):
                records = sorted((root / "world").glob("*-transport.json"))
                last = json.loads(records[-1].read_text())["events"][-1]
                self.assertEqual((last["state"], last["attempts"]), ("reserved", 1))
                self.assertNotIn("receipt", last)
                return {"ok": True, "value": "observed"}

            world.world.execute.side_effect = execute
            world.call("source.read", operation_id="one")
            records = sorted((root / "world").glob("*-transport.json"))
            last = json.loads(records[-1].read_text())["events"][-1]
            self.assertEqual(last["state"], "received")
            self.assertEqual(last["receipt"]["value"], "observed")
            self.assertFalse(list((root / "world").glob("*snapshot*")))
            with self.assertRaises(FileExistsError):
                self.world(root).prepare()

    def test_prepare_recreates_output_cleared_by_harbor_after_simulator_start(self):
        with tempfile.TemporaryDirectory() as temporary, patch.object(SERVER.threading, "Timer"):
            output = Path(temporary) / "verifier"
            previous = output / "world"
            previous.mkdir(parents=True)
            stale = previous / "author-artifact.json"
            stale.write_text("untrusted previous phase")
            world = self.world(output)
            self.assertEqual(stale.read_text(), "untrusted previous phase")
            stale.unlink()
            previous.rmdir()
            output.rmdir()

            window = world.prepare()
            observations = list((output / "world").glob("*.json"))
            self.assertEqual(len(observations), 1)
            self.assertEqual(json.loads(observations[0].read_text()), window)
            before = observations[0].read_bytes()
            with self.assertRaises(ValueError):
                world.prepare()
            fresh = self.world(output)
            with self.assertRaises(FileExistsError):
                fresh.prepare()
            self.assertIsNone(fresh.started)
            self.assertEqual(observations[0].read_bytes(), before)
            self.assertEqual(list((output / "world").iterdir()), observations)

    def test_unexpected_exception_and_malformed_receipt_keep_reservation_closed(self):
        for outcome in (RuntimeError("unknown completion"), TypeError("invalid upstream response"), None):
            with (
                self.subTest(outcome=outcome),
                tempfile.TemporaryDirectory() as temporary,
                patch.object(SERVER.threading, "Timer"),
            ):
                root = Path(temporary)
                world = self.world(root)
                world.prepare()
                if isinstance(outcome, Exception):
                    world.world.execute.side_effect = outcome
                    with self.assertRaises(type(outcome)):
                        world.call("change", operation_id="reserved", max_attempts=3)
                else:
                    world.world.execute.return_value = outcome
                    result = world.call("change", operation_id="reserved", max_attempts=3)
                    self.assertEqual(result["error"]["code"], "PROJECT_AMBIGUOUS_OUTCOME")
                reserved = json.loads(sorted((root / "world").glob("*-transport.json"))[-1].read_text())
                self.assertTrue(reserved["ambiguous_outcome"])
                self.assertEqual(reserved["events"][-1]["state"], "ambiguous")
                replay = world.call("change", operation_id="reserved", max_attempts=3)
                self.assertEqual(replay["error"]["code"], "PROJECT_AMBIGUOUS_OUTCOME")
                refused = world.call("change", operation_id="another", max_attempts=3)
                self.assertEqual(refused["error"]["code"], "PROJECT_AMBIGUOUS_OUTCOME")
                world.world.execute.assert_called_once()

    def test_finalize_latency_does_not_change_workflow_duration_or_terminal_winner(self):
        with (
            tempfile.TemporaryDirectory() as temporary,
            patch.object(SERVER.threading, "Timer"),
            patch.object(SERVER.time, "monotonic", return_value=100) as clock,
        ):
            root = Path(temporary)
            world = self.world(root)
            world.prepare()
            clock.return_value = 101

            def finalize():
                clock.return_value = 1000
                return {"checks": {"completed": False}}

            world.world.finalize.side_effect = finalize
            snapshot = world.snapshot()
            self.assertEqual(snapshot["window"]["duration_seconds"], 1)
            self.assertEqual(snapshot["window"]["termination_reason"], "finished")
            before = {p.name: p.read_bytes() for p in (root / "world").iterdir()}
            world._deadline()
            self.assertEqual(world.snapshot(), snapshot)
            self.assertEqual({p.name: p.read_bytes() for p in (root / "world").iterdir()}, before)
            world.world.finalize.assert_called_once()
            self.assertEqual(world.call("late")["error"]["code"], "RUN_CLOSED")

    def test_dead_worker_needs_no_finish_request_for_deadline_snapshot(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            world = self.world(root, limit=0.02)
            finished = threading.Event()
            original = world._deadline

            def deadline():
                try:
                    original()
                finally:
                    finished.set()

            world._deadline = deadline
            world.prepare()
            self.assertTrue(finished.wait(2))
            snapshot = world.snapshot()
            self.assertEqual(snapshot["window"]["termination_reason"], "timeout")
            self.assertEqual(len(list((root / "world").glob("*-terminal-window.json"))), 1)
            self.assertEqual(len(list((root / "world").glob("*-snapshot.json"))), 1)

    def test_finish_and_deadline_race_publish_one_timeout_winner(self):
        with tempfile.TemporaryDirectory() as temporary, patch.object(SERVER.threading, "Timer"):
            root = Path(temporary)
            world = self.world(root)
            world.prepare()
            world.started -= world.limit_seconds + 1
            barrier = threading.Barrier(3)
            results = []

            def finish():
                barrier.wait()
                results.append(world.snapshot())

            threads = [threading.Thread(target=finish) for _ in range(2)]
            for thread in threads:
                thread.start()
            barrier.wait()
            for thread in threads:
                thread.join(timeout=2)
                self.assertFalse(thread.is_alive())
            self.assertEqual(len(results), 2)
            self.assertEqual(results[0], results[1])
            self.assertEqual(results[0]["window"]["termination_reason"], "timeout")
            world.world.finalize.assert_called_once()
            self.assertEqual(len(list((root / "world").glob("*-terminal-window.json"))), 1)

    def test_interrupted_evidence_write_can_resume_identical_bytes_only(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "record.json"
            HOOKS._record_once(path, {"observed": True})
            before = path.read_bytes()
            HOOKS._record_once(path, {"observed": True})
            with self.assertRaises(ValueError):
                HOOKS._record_once(path, {"observed": False})
            self.assertEqual(path.read_bytes(), before)
            self.assertEqual(list(path.parent.iterdir()), [path])

    def test_finalizer_error_is_frozen_without_fabricating_environment(self):
        with tempfile.TemporaryDirectory() as temporary, patch.object(SERVER.threading, "Timer"):
            world = self.world(Path(temporary))
            world.prepare()
            world.world.finalize.side_effect = OSError("private implementation error")
            first = world.snapshot()
            self.assertIsNone(first["environment"])
            self.assertEqual(first["evidence_errors"], [{"source": "environment", "error": "OSError"}])
            self.assertEqual(world.snapshot(), first)
            world.world.finalize.assert_called_once()

    def test_missing_simulator_records_unknown_without_claiming_terminal_environment(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            context = {
                "output": root,
                "evidence": root / "evidence",
                "submission": root / "missing.yaml",
                "options": {},
                "record": {"status": "unknown", "output": None},
            }
            window = {"run_id": "run", "started_at": "start", "deadline_at": 100, "limit_seconds": 30}
            with (
                patch.object(HOOKS, "_post", return_value=window),
                patch.object(HOOKS, "_credentials", return_value={"tool_token": "token"}),
            ):
                HOOKS.prepare(context)
            with patch.object(HOOKS, "_post", side_effect=OSError("dead simulator")):
                trial = HOOKS.snapshot(context)
            self.assertEqual(trial["termination_reason"], "unknown")
            self.assertIsNone(trial["native_execution"])
            self.assertIsNone(trial["duration_seconds"])
            self.assertFalse(trial["terminal_completion"])
            self.assertFalse((context["evidence"] / "environment-evidence.json").exists())
            self.assertFalse((context["evidence"] / "transport-evidence.json").exists())
            before = {p.name: p.read_bytes() for p in context["evidence"].iterdir()}
            context["record"] = {"status": "success", "output": {"final_answer": "late", "incident_summary": "late"}}
            self.assertEqual(HOOKS.snapshot(context), trial)
            self.assertEqual({p.name: p.read_bytes() for p in context["evidence"].iterdir()}, before)
