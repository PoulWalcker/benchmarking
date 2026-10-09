"""Durable lifecycle decisions through its public interface; no Docker or models."""

import copy
from datetime import UTC, datetime
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from sapi_config_lab import profile
from sapi_config_lab.contracts import CompiledWorkflow, CompileOptions, RunBinding
from sapi_config_lab.coordinate.lifecycle import LifecycleController
from sapi_config_lab.evidence import durable_json
from sapi_config_lab.paths import workspace_root
from tests.support.invoice import CATALOG, OPERATION_SOURCE
from tests.support.lifecycle import FixtureN8n, acceptance
from tests.support.lifecycle import bindings as fixture_bindings
from tests.support.lifecycle import config as fixture_config
from tests.support.native import SimulatedN8n


def record_error(record):
    return json.dumps(record["error"])


class LifecycleBackend:
    name = "lifecycle-fixture"

    def __init__(self):
        self.calls = []
        self.bindings = []
        self.reject_count = 0

    def compile(self, config, bindings, options):
        profile.validate(config, bindings)
        return CompiledWorkflow(self.name, copy.deepcopy(config), {}, 120, options)

    def execute(self, compiled, artifact_dir, binding):
        self.calls.append(compiled)
        self.bindings.append(binding)
        output = {"value": json.loads(compiled.document["workflow"]["inputs"]["text"])}
        if len(self.calls) <= self.reject_count:
            output["value"] = {"count": -1}
        return {
            "status": "success",
            "output": output,
            "result": {"output": output},
            "workflow_id": "native",
            "execution_id": str(len(self.calls)),
        }


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.config = fixture_config()
        self.backend = LifecycleBackend()

    def test_missing_trusted_backend_refuses_before_execution_reservation(self):
        with tempfile.TemporaryDirectory() as directory:
            controller = LifecycleController(Path(directory), verifier=acceptance, bindings=fixture_bindings())
            reference = controller.register(fixture_config())
            with self.assertRaisesRegex(ValueError, "explicit trusted backend"):
                controller.callback(reference, "no-backend")
            snapshot = controller.snapshot()
            self.assertTrue(all(event["state"] == "queued" for event in snapshot["events"].values()))
            self.assertFalse(any(row["kind"] == "execution_reserved" for row in snapshot["transitions"]))

    def test_no_acceptance_default_or_name_dispatch(self):
        with tempfile.TemporaryDirectory() as directory:
            controller = LifecycleController(Path(directory), backend=self.backend, bindings=fixture_bindings())
            with self.assertRaisesRegex(profile.Invalid, "explicit acceptance callable"):
                controller.register(self.config)
            self.assertEqual(controller.snapshot()["definitions"], {})
            self.assertEqual(self.backend.calls, [])

    def test_restart_without_callable_refuses_queued_work_before_reserving_or_dispatching(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            controller = LifecycleController(
                root, backend=self.backend, bindings=fixture_bindings(), verifier=acceptance
            )
            ref = controller.register(self.config)
            controller._admit(ref, "Callback", "pending")
            before = controller.snapshot()
            restarted = LifecycleController(root, backend=self.backend, bindings=fixture_bindings())
            with self.assertRaisesRegex(profile.Invalid, "explicit acceptance callable"):
                restarted.drain()
            self.assertEqual(restarted.snapshot(), before)
            self.assertEqual(self.backend.calls, [])

    def test_wrong_decision_identity_fails_before_release(self):
        def wrong(config, record):
            return {"verifier": "unrelated", "passed": True, "findings": []}

        with tempfile.TemporaryDirectory() as directory:
            controller = LifecycleController(
                Path(directory), backend=self.backend, bindings=fixture_bindings(), verifier=wrong
            )
            with self.assertRaisesRegex(profile.Invalid, "Invalid operational verifier"):
                controller.callback(controller.register(self.config), "wrong-decision")
            self.assertIsNone(controller.snapshot()["families"]["lifecycle-fixture"]["active"])

    def test_callback_releases_exact_tested_definition_and_duplicate_survives_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            controller = LifecycleController(
                Path(directory), verifier=acceptance, bindings=fixture_bindings(), backend=self.backend
            )
            ref = controller.register(self.config)
            event = controller.callback(ref, "test-001")
            self.assertEqual(event["state"], "passed")
            self.assertEqual(controller.snapshot()["families"]["lifecycle-fixture"]["active"], "lifecycle-fixture@1")
            restarted = LifecycleController(
                Path(directory), verifier=acceptance, bindings=fixture_bindings(), backend=self.backend
            )
            self.assertEqual(restarted.callback(ref, "test-001"), event)
            self.assertEqual(len(self.backend.calls), 1)
            self.assertEqual(self.backend.bindings[0].admission["kind"], "Callback")
            self.assertEqual(self.backend.bindings[0].admission["workflow_ref"], ref)
            self.assertEqual(restarted.snapshot()["definitions"]["lifecycle-fixture@1"]["config"], self.config)

    def test_rejection_builds_a_persisted_fork_then_archives_and_tests_new_revision(self):
        self.backend.reject_count = 1
        calls = []

        def rebuild(source, findings, target, artifacts):
            calls.append((copy.deepcopy(source), findings, target))
            candidate = copy.deepcopy(source)
            candidate["workflow"]["revision"] = target["revision"]
            candidate["activation"]["workflow_ref"] = target
            return candidate

        with tempfile.TemporaryDirectory() as directory:
            controller = LifecycleController(
                Path(directory),
                verifier=acceptance,
                bindings=fixture_bindings(),
                backend=self.backend,
                rebuilder=rebuild,
            )
            ref = controller.register(self.config)
            first = controller.callback(ref, "test-fork")
            state = controller.snapshot()
        self.assertEqual(first["state"], "failed")
        self.assertEqual(len(calls), 1)
        self.assertIn("Decoded value", calls[0][1]["findings"][0])
        self.assertEqual(state["families"]["lifecycle-fixture"]["active"], "lifecycle-fixture@2")
        self.assertEqual(state["definitions"]["lifecycle-fixture@1"]["status"], "archived")
        self.assertEqual(state["definitions"]["lifecycle-fixture@2"]["forked_from"], "lifecycle-fixture@1")
        kinds = [transition["kind"] for transition in state["transitions"]]
        self.assertLess(kinds.index("fork_persisted"), kinds.index("archived"))
        self.assertEqual([binding.admission["workflow_ref"]["revision"] for binding in self.backend.bindings], [1, 2])

    def test_cron_admits_only_current_due_minute_and_deduplicates_across_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            controller = LifecycleController(
                Path(directory), verifier=acceptance, bindings=fixture_bindings(), backend=self.backend
            )
            ref = controller.register(self.config)
            self.assertEqual(controller.tick(datetime(2026, 10, 4, 5, 0, tzinfo=UTC)), [])
            controller.callback(ref, "release")
            self.assertEqual(controller.tick(datetime(2026, 10, 4, 5, 1, tzinfo=UTC)), [])
            event = controller.tick(datetime(2026, 10, 5, 5, 0, tzinfo=UTC))[0]
            self.assertEqual(event["admission"]["kind"], "Cron")
            restarted = LifecycleController(
                Path(directory), verifier=acceptance, bindings=fixture_bindings(), backend=self.backend
            )
            restarted.tick(datetime(2026, 10, 5, 5, 0, tzinfo=UTC))
            self.assertEqual(len(self.backend.calls), 2)
            self.assertEqual(event["state"], "passed")

    def test_exhausted_rebuilds_suspend_and_create_adhoc_without_accepted_revision(self):
        self.backend.reject_count = 10

        def rebuild(source, findings, target, artifacts):
            source["workflow"]["revision"] = target["revision"]
            source["activation"]["workflow_ref"] = target
            return source

        with tempfile.TemporaryDirectory() as directory:
            controller = LifecycleController(
                Path(directory),
                verifier=acceptance,
                bindings=fixture_bindings(),
                backend=self.backend,
                rebuilder=rebuild,
            )
            controller.callback(controller.register(self.config), "exhaust")
            state = controller.snapshot()
            self.assertEqual(controller.tick(datetime(2026, 10, 5, 5, 0, tzinfo=UTC)), [])
        self.assertEqual(len(self.backend.calls), 3)
        self.assertEqual(len(state["rebuilds"]), 2)
        self.assertTrue(state["families"]["lifecycle-fixture"]["suspended"])
        self.assertIsNone(state["families"]["lifecycle-fixture"]["active"])
        self.assertEqual(state["adhoc"][0]["reason"], "Rebuild limit exhausted")

    def test_crash_after_reservation_is_unknown_on_restart_and_never_repeats(self):
        def crash(compiled, artifacts, binding):
            raise SystemExit("simulated process loss after admission")

        self.backend.execute = crash
        with tempfile.TemporaryDirectory() as directory:
            controller = LifecycleController(
                Path(directory), verifier=acceptance, bindings=fixture_bindings(), backend=self.backend
            )
            ref = controller.register(self.config)
            with self.assertRaises(SystemExit):
                controller.callback(ref, "crash")
            restarted = LifecycleController(
                Path(directory), verifier=acceptance, bindings=fixture_bindings(), backend=LifecycleBackend()
            )
            restarted.drain()
            state = restarted.snapshot()
            self.assertTrue(state["families"]["lifecycle-fixture"]["suspended"])
            self.assertEqual(next(iter(state["events"].values()))["state"], "unknown")
            self.assertEqual(restarted.backend.calls, [])

    def test_restore_archived_definition_never_replays_or_retargets_cron(self):
        self.backend.reject_count = 1

        def rebuild(source, findings, target, artifacts):
            source["workflow"]["revision"] = target["revision"]
            source["activation"]["workflow_ref"] = target
            return source

        with tempfile.TemporaryDirectory() as directory:
            controller = LifecycleController(
                Path(directory),
                verifier=acceptance,
                bindings=fixture_bindings(),
                backend=self.backend,
                rebuilder=rebuild,
            )
            ref = controller.register(self.config)
            controller.callback(ref, "fork")
            count = len(self.backend.calls)
            restored = controller.restore(ref)
            self.assertEqual(restored["status"], "draft")
            self.assertEqual(len(self.backend.calls), count)
            self.assertEqual(controller.snapshot()["families"]["lifecycle-fixture"]["active"], "lifecycle-fixture@2")

    def test_overlapping_cron_is_skipped_while_admitted_run_keeps_its_revision(self):
        with tempfile.TemporaryDirectory() as directory:
            controller = LifecycleController(
                Path(directory), verifier=acceptance, bindings=fixture_bindings(), backend=self.backend
            )
            ref = controller.register(self.config)
            controller.callback(ref, "release")
            execute = self.backend.execute
            overlaps = []

            def during_run(compiled, artifacts, binding):
                overlaps.extend(controller.tick(datetime(2026, 10, 6, 5, 0, tzinfo=UTC)))
                return execute(compiled, artifacts, binding)

            self.backend.execute = during_run
            result = controller.tick(datetime(2026, 10, 5, 5, 0, tzinfo=UTC))
            self.assertEqual(result[0]["state"], "passed")
            self.assertEqual(overlaps[0]["state"], "skipped_overlap")
            self.assertEqual(len(self.backend.calls), 2)

    def test_rebuilder_cannot_relax_acceptance_or_mutate_original_definition(self):
        self.backend.reject_count = 1

        def relax(source, findings, target, artifacts):
            source["workflow"]["revision"] = target["revision"]
            source["activation"]["workflow_ref"] = target
            source["workflow"]["acceptance"] = "Any text passes."
            return source

        with tempfile.TemporaryDirectory() as directory:
            controller = LifecycleController(
                Path(directory), verifier=acceptance, bindings=fixture_bindings(), backend=self.backend, rebuilder=relax
            )
            controller.callback(controller.register(self.config), "bad-repair")
            state = controller.snapshot()
        self.assertTrue(state["families"]["lifecycle-fixture"]["suspended"])
        self.assertEqual(list(state["definitions"]), ["lifecycle-fixture@1"])
        self.assertEqual(state["definitions"]["lifecycle-fixture@1"]["config"], self.config)

    def test_admitted_candidate_compiles_once_and_takes_its_event_and_deadline_at_run_time(self):
        admission = {
            "kind": "Callback",
            "rule_id": "fixture-test-requested",
            "event_id": "actual-event",
            "workflow_ref": {"id": "lifecycle-fixture", "revision": 1},
            "purpose": "test",
        }
        backend = FixtureN8n()
        bindings = fixture_bindings()
        with self.assertRaises(profile.Unsupported):
            backend.compile(self.config, bindings, CompileOptions())
        with self.assertRaises(ValueError):
            CompileOptions(activation="event")
        options = CompileOptions(activation="event", bound_deadline=True)
        compiled = backend.compile(self.config, bindings, options)
        # Identical definition and options give byte-identical artifacts; no run value is inside.
        self.assertEqual(
            json.dumps(backend.compile(self.config, bindings, options).document), json.dumps(compiled.document)
        )
        self.assertNotIn("actual-event", json.dumps(compiled.document))

        def run(binding):
            directory = Path(tempfile.mkdtemp())
            self.addCleanup(shutil.rmtree, directory)
            return backend.execute(compiled, directory, binding)

        with self.assertRaisesRegex(RuntimeError, "absolute deadline"):
            run(RunBinding(admission=admission))
        with self.assertRaisesRegex(RuntimeError, "admitted event"):
            run(RunBinding(deadline_at=9000000000))
        record = run(RunBinding(deadline_at=9000000000.5, admission=admission))
        self.assertEqual(record["result"]["admission"], admission)
        self.assertEqual(record["result"]["input_source"], "event")
        fixture = record["run_data"]["Fixture"][0]["data"]["main"][0][0]["json"]
        self.assertEqual(fixture["deadline_at_ms"], 9000000000.5 * 1000)
        other = {**admission, "workflow_ref": {"id": "lifecycle-fixture", "revision": 2}}
        self.assertIn(
            "Admission revision differs", record_error(run(RunBinding(deadline_at=9000000000, admission=other)))
        )
        wrong_rule = {**admission, "rule_id": "fixture-scheduled"}
        self.assertIn("Invalid lifecycle admission", record_error(run(RunBinding(9000000000, wrong_rule))))

    def test_an_ordinary_case_needs_no_binding_and_ignores_a_supplied_event(self):
        config = profile.read(workspace_root() / "tasks/invoice-total/solution/config.yaml")
        backend = SimulatedN8n(OPERATION_SOURCE)
        compiled = backend.compile(config, profile.read_bindings(CATALOG), CompileOptions())
        self.assertNotIn("SAPI_RUN_BINDING", json.dumps(compiled.document))
        with tempfile.TemporaryDirectory() as directory:
            record = backend.execute(compiled, Path(directory), RunBinding())
        self.assertEqual(record["status"], "success")
        self.assertNotIn("deadline_at_ms", record["run_data"]["Fixture"][0]["data"]["main"][0][0]["json"])

    def test_new_candidate_waits_for_in_flight_cron_and_restore_does_not_replay(self):
        with tempfile.TemporaryDirectory() as directory:
            controller = LifecycleController(
                Path(directory), verifier=acceptance, bindings=fixture_bindings(), backend=self.backend
            )
            original_ref = controller.register(self.config)
            controller.callback(original_ref, "release-original")
            execute = self.backend.execute
            proposed = copy.deepcopy(self.config)
            proposed["workflow"]["revision"] = 2
            proposed["activation"]["workflow_ref"]["revision"] = 2

            def during_old_run(compiled, artifacts, binding):
                if binding.admission["kind"] == "Cron":
                    new_ref = controller.register(proposed, forked_from=original_ref)
                    queued = controller.callback(new_ref, "test-new-candidate")
                    self.assertEqual(queued["state"], "queued")
                    self.assertEqual(binding.admission["workflow_ref"], original_ref)
                return execute(compiled, artifacts, binding)

            self.backend.execute = during_old_run
            controller.tick(datetime(2026, 10, 5, 5, 0, tzinfo=UTC))
            state = controller.snapshot()
            self.assertEqual(state["families"]["lifecycle-fixture"]["active"], "lifecycle-fixture@2")
            kinds = [t["kind"] for t in state["transitions"]]
            self.assertLess(kinds.index("cron_result"), len(kinds) - 1)
            last_release = state["transitions"][-1]
            self.assertEqual(last_release["kind"], "released")
            self.assertEqual(last_release["data"]["definition"], "lifecycle-fixture@2")

    def test_restart_finishes_a_durable_native_result_without_reexecuting(self):
        def lose_process_after_write(path, value):
            durable_json(path, value)
            if path.name == "native-record.json":
                raise SystemExit("crash between durable result and registry commit")

        with tempfile.TemporaryDirectory() as directory:
            controller = LifecycleController(
                Path(directory), verifier=acceptance, bindings=fixture_bindings(), backend=self.backend
            )
            ref = controller.register(self.config)
            with patch("sapi_config_lab.coordinate.lifecycle.durable_json", side_effect=lose_process_after_write):
                with self.assertRaises(SystemExit):
                    controller.callback(ref, "recover-result")
            restarted = LifecycleController(
                Path(directory), verifier=acceptance, bindings=fixture_bindings(), backend=self.backend
            )
            restarted.drain()
            self.assertEqual(len(self.backend.calls), 1)
            self.assertEqual(restarted.snapshot()["families"]["lifecycle-fixture"]["active"], "lifecycle-fixture@1")

    def test_empty_callback_condition_and_invalid_cron_cannot_start_native_work(self):
        with tempfile.TemporaryDirectory() as directory:
            controller = LifecycleController(
                Path(directory), verifier=acceptance, bindings=fixture_bindings(), backend=self.backend
            )
            ref = controller.register(self.config)
            event = controller.callback(ref, "not-ready", condition=False)
            self.assertEqual(event["state"], "dismissed")
            self.assertEqual(self.backend.calls, [])
        self.config["activation"]["schedule"] = "* * * * *"
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(profile.Invalid, "fixed minute/hour"):
                LifecycleController(Path(directory), verifier=acceptance, bindings=fixture_bindings()).register(
                    self.config
                )

    def test_literal_decode_is_rejected_even_if_its_output_looks_plausible(self):
        self.config["workflow"]["steps"][-1]["with"]["text"] = '{"count": 7}'
        with tempfile.TemporaryDirectory() as directory:
            controller = LifecycleController(
                Path(directory), verifier=acceptance, bindings=fixture_bindings(), backend=self.backend
            )
            event = controller.callback(controller.register(self.config), "literal-source")
            self.assertEqual(event["state"], "failed")
            self.assertIn("source", event["decision"]["findings"][0])
            self.assertIsNone(controller.snapshot()["families"]["lifecycle-fixture"]["active"])


if __name__ == "__main__":
    unittest.main()
