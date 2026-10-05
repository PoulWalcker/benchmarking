"""Durable lifecycle decisions through its public interface; no Docker or models."""

import copy
from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from sapi_config_lab.paths import workspace_root
from sapi_config_lab.runtime.contracts import CompiledWorkflow
from sapi_config_lab.runtime.lifecycle import LifecycleController
from sapi_config_lab.runtime.lifecycle import durable_json
from sapi_config_lab.runtime.n8n.adapter import N8nBackend
from sapi_config_lab.runtime.contracts import CompileOptions
from sapi_config_lab.paths import CATALOG
from sapi_config_lab.core import profile


class DigestBackend:
    name = "digest-fixture"

    def __init__(self):
        self.calls = []
        self.reject_count = 0

    def compile(self, config, bindings, options):
        profile.validate(config, bindings)
        return CompiledWorkflow(self.name, copy.deepcopy(config), {}, 120, options)

    def execute(self, compiled, artifact_dir):
        self.calls.append(compiled)
        articles = compiled.document["workflow"]["inputs"]["articles"]
        output = {
            "mode": "preview",
            "text": "A warehouse opened in Dubai; payment option added.",
            "article_ids": [article["id"] for article in articles],
        }
        if len(self.calls) <= self.reject_count:
            output["article_ids"] = ["invented-article"]
        return {
            "status": "success",
            "output": output,
            "result": {"output": output},
            "workflow_id": "native",
            "execution_id": str(len(self.calls)),
        }


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.config = profile.read(workspace_root() / "configs/05-digest-lifecycle.yaml")
        self.backend = DigestBackend()

    def test_callback_releases_exact_tested_definition_and_duplicate_survives_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            controller = LifecycleController(Path(directory), backend=self.backend)
            ref = controller.register(self.config)
            event = controller.callback(ref, "test-001")
            self.assertEqual(event["state"], "passed")
            self.assertEqual(controller.snapshot()["families"]["daily-digest"]["active"], "daily-digest@1")
            restarted = LifecycleController(Path(directory), backend=self.backend)
            self.assertEqual(restarted.callback(ref, "test-001"), event)
            self.assertEqual(len(self.backend.calls), 1)
            self.assertEqual(self.backend.calls[0].options.admission["kind"], "Callback")
            self.assertEqual(self.backend.calls[0].options.admission["workflow_ref"], ref)
            self.assertEqual(restarted.snapshot()["definitions"]["daily-digest@1"]["config"], self.config)

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
            controller = LifecycleController(Path(directory), backend=self.backend, rebuilder=rebuild)
            ref = controller.register(self.config)
            first = controller.callback(ref, "test-fork")
            state = controller.snapshot()
        self.assertEqual(first["state"], "failed")
        self.assertEqual(len(calls), 1)
        self.assertIn("article IDs", calls[0][1]["findings"][0])
        self.assertEqual(state["families"]["daily-digest"]["active"], "daily-digest@2")
        self.assertEqual(state["definitions"]["daily-digest@1"]["status"], "archived")
        self.assertEqual(state["definitions"]["daily-digest@2"]["forked_from"], "daily-digest@1")
        kinds = [transition["kind"] for transition in state["transitions"]]
        self.assertLess(kinds.index("fork_persisted"), kinds.index("archived"))
        self.assertEqual([call.options.admission["workflow_ref"]["revision"] for call in self.backend.calls], [1, 2])

    def test_cron_admits_only_current_due_minute_and_deduplicates_across_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            controller = LifecycleController(Path(directory), backend=self.backend)
            ref = controller.register(self.config)
            self.assertEqual(controller.tick(datetime(2026, 10, 4, 5, 0, tzinfo=timezone.utc)), [])
            controller.callback(ref, "release")
            self.assertEqual(controller.tick(datetime(2026, 10, 4, 5, 1, tzinfo=timezone.utc)), [])
            event = controller.tick(datetime(2026, 10, 5, 5, 0, tzinfo=timezone.utc))[0]
            self.assertEqual(event["admission"]["kind"], "Cron")
            restarted = LifecycleController(Path(directory), backend=self.backend)
            restarted.tick(datetime(2026, 10, 5, 5, 0, tzinfo=timezone.utc))
            self.assertEqual(len(self.backend.calls), 2)
            self.assertEqual(event["state"], "passed")

    def test_exhausted_rebuilds_suspend_and_create_adhoc_without_accepted_revision(self):
        self.backend.reject_count = 10

        def rebuild(source, findings, target, artifacts):
            source["workflow"]["revision"] = target["revision"]
            source["activation"]["workflow_ref"] = target
            return source

        with tempfile.TemporaryDirectory() as directory:
            controller = LifecycleController(Path(directory), backend=self.backend, rebuilder=rebuild)
            controller.callback(controller.register(self.config), "exhaust")
            state = controller.snapshot()
            self.assertEqual(controller.tick(datetime(2026, 10, 5, 5, 0, tzinfo=timezone.utc)), [])
        self.assertEqual(len(self.backend.calls), 3)
        self.assertEqual(len(state["rebuilds"]), 2)
        self.assertTrue(state["families"]["daily-digest"]["suspended"])
        self.assertIsNone(state["families"]["daily-digest"]["active"])
        self.assertEqual(state["adhoc"][0]["reason"], "Rebuild limit exhausted")

    def test_crash_after_reservation_is_unknown_on_restart_and_never_repeats(self):
        def crash(compiled, artifacts):
            raise SystemExit("simulated process loss after admission")

        self.backend.execute = crash
        with tempfile.TemporaryDirectory() as directory:
            controller = LifecycleController(Path(directory), backend=self.backend)
            ref = controller.register(self.config)
            with self.assertRaises(SystemExit):
                controller.callback(ref, "crash")
            restarted = LifecycleController(Path(directory), backend=DigestBackend())
            restarted.drain()
            state = restarted.snapshot()
            self.assertTrue(state["families"]["daily-digest"]["suspended"])
            self.assertEqual(next(iter(state["events"].values()))["state"], "unknown")
            self.assertEqual(restarted.backend.calls, [])

    def test_restore_archived_definition_never_replays_or_retargets_cron(self):
        self.backend.reject_count = 1

        def rebuild(source, findings, target, artifacts):
            source["workflow"]["revision"] = target["revision"]
            source["activation"]["workflow_ref"] = target
            return source

        with tempfile.TemporaryDirectory() as directory:
            controller = LifecycleController(Path(directory), backend=self.backend, rebuilder=rebuild)
            ref = controller.register(self.config)
            controller.callback(ref, "fork")
            count = len(self.backend.calls)
            restored = controller.restore(ref)
            self.assertEqual(restored["status"], "draft")
            self.assertEqual(len(self.backend.calls), count)
            self.assertEqual(controller.snapshot()["families"]["daily-digest"]["active"], "daily-digest@2")

    def test_overlapping_cron_is_skipped_while_admitted_run_keeps_its_revision(self):
        with tempfile.TemporaryDirectory() as directory:
            controller = LifecycleController(Path(directory), backend=self.backend)
            ref = controller.register(self.config)
            controller.callback(ref, "release")
            execute = self.backend.execute
            overlaps = []

            def during_run(compiled, artifacts):
                overlaps.extend(controller.tick(datetime(2026, 10, 6, 5, 0, tzinfo=timezone.utc)))
                return execute(compiled, artifacts)

            self.backend.execute = during_run
            result = controller.tick(datetime(2026, 10, 5, 5, 0, tzinfo=timezone.utc))
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
            controller = LifecycleController(Path(directory), backend=self.backend, rebuilder=relax)
            controller.callback(controller.register(self.config), "bad-repair")
            state = controller.snapshot()
        self.assertTrue(state["families"]["daily-digest"]["suspended"])
        self.assertEqual(list(state["definitions"]), ["daily-digest@1"])
        self.assertEqual(state["definitions"]["daily-digest@1"]["config"], self.config)

    def test_admitted_candidate_compiles_with_exact_event_provenance_and_deadline(self):
        admission = {
            "kind": "Callback",
            "rule_id": "digest-test-requested",
            "event_id": "actual-event",
            "workflow_ref": {"id": "daily-digest", "revision": 1},
            "purpose": "test",
        }
        backend = N8nBackend()
        bindings = profile.read_bindings(CATALOG)
        with self.assertRaises(profile.Unsupported):
            backend.compile(self.config, bindings, CompileOptions())
        compiled = backend.compile(self.config, bindings, CompileOptions(admission=admission, deadline_at=9000000000))
        fixture = next(n for n in compiled.document["nodes"] if n["name"] == "Fixture")
        self.assertIn('"input_source": "event"', fixture["parameters"]["jsCode"])
        self.assertIn('"event_id": "actual-event"', fixture["parameters"]["jsCode"])
        with self.assertRaisesRegex(profile.Invalid, "Admission revision"):
            backend.compile(
                self.config,
                bindings,
                CompileOptions(
                    admission={**admission, "workflow_ref": {"id": "daily-digest", "revision": 2}},
                    deadline_at=9000000000,
                ),
            )

    def test_new_candidate_waits_for_in_flight_cron_and_restore_does_not_replay(self):
        with tempfile.TemporaryDirectory() as directory:
            controller = LifecycleController(Path(directory), backend=self.backend)
            original_ref = controller.register(self.config)
            controller.callback(original_ref, "release-original")
            execute = self.backend.execute
            proposed = copy.deepcopy(self.config)
            proposed["workflow"]["revision"] = 2
            proposed["activation"]["workflow_ref"]["revision"] = 2

            def during_old_run(compiled, artifacts):
                if compiled.options.admission["kind"] == "Cron":
                    new_ref = controller.register(proposed, forked_from=original_ref)
                    queued = controller.callback(new_ref, "test-new-candidate")
                    self.assertEqual(queued["state"], "queued")
                    self.assertEqual(compiled.options.admission["workflow_ref"], original_ref)
                return execute(compiled, artifacts)

            self.backend.execute = during_old_run
            controller.tick(datetime(2026, 10, 5, 5, 0, tzinfo=timezone.utc))
            state = controller.snapshot()
            self.assertEqual(state["families"]["daily-digest"]["active"], "daily-digest@2")
            kinds = [t["kind"] for t in state["transitions"]]
            self.assertLess(kinds.index("cron_result"), len(kinds) - 1)
            last_release = state["transitions"][-1]
            self.assertEqual(last_release["kind"], "released")
            self.assertEqual(last_release["data"]["definition"], "daily-digest@2")

    def test_restart_finishes_a_durable_native_result_without_reexecuting(self):
        def lose_process_after_write(path, value):
            durable_json(path, value)
            if path.name == "native-record.json":
                raise SystemExit("crash between durable result and registry commit")

        with tempfile.TemporaryDirectory() as directory:
            controller = LifecycleController(Path(directory), backend=self.backend)
            ref = controller.register(self.config)
            with patch("sapi_config_lab.runtime.lifecycle.durable_json", side_effect=lose_process_after_write):
                with self.assertRaises(SystemExit):
                    controller.callback(ref, "recover-result")
            restarted = LifecycleController(Path(directory), backend=self.backend)
            restarted.drain()
            self.assertEqual(len(self.backend.calls), 1)
            self.assertEqual(restarted.snapshot()["families"]["daily-digest"]["active"], "daily-digest@1")

    def test_empty_callback_condition_and_invalid_cron_cannot_start_native_work(self):
        with tempfile.TemporaryDirectory() as directory:
            controller = LifecycleController(Path(directory), backend=self.backend)
            ref = controller.register(self.config)
            event = controller.callback(ref, "not-ready", condition=False)
            self.assertEqual(event["state"], "dismissed")
            self.assertEqual(self.backend.calls, [])
        self.config["activation"]["schedule"] = "* * * * *"
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(profile.Invalid, "fixed minute/hour"):
                LifecycleController(Path(directory)).register(self.config)

    def test_literal_preview_is_rejected_even_if_its_output_looks_plausible(self):
        self.config["workflow"]["steps"][-1]["with"]["summary"] = {
            "text": "A plausible but fixed summary",
            "article_ids": ["a1", "a2"],
        }
        with tempfile.TemporaryDirectory() as directory:
            controller = LifecycleController(Path(directory), backend=self.backend)
            event = controller.callback(controller.register(self.config), "literal-source")
            self.assertEqual(event["state"], "failed")
            self.assertIn("source", event["decision"]["findings"][0])
            self.assertIsNone(controller.snapshot()["families"]["daily-digest"]["active"])


if __name__ == "__main__":
    unittest.main()
