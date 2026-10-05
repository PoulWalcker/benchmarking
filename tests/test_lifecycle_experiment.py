"""Finite lifecycle admission fails closed before any external execution."""

import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from sapi_config_lab.experiments.lifecycle_run import (
    LifecycleSeries,
    AdmittedNativeBackend,
    author,
    file_hash,
    bind_authoring_evidence,
)
from sapi_config_lab.experiments.tasks import stage_tasks
from sapi_config_lab.paths import workspace_root
from sapi_config_lab.scenarios import select_scenarios
from sapi_config_lab.workflow import profile
from verification.lifecycle_submission import validate_task
from verification.lifecycle_submission import verify_submission

MODULE = "sapi_config_lab.experiments.lifecycle_run"


class LifecycleExperimentTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.directory = Path(self.temporary.name) / "series"
        self.source = patch(MODULE + ".source_manifest", return_value={"frozen.py": "abc"})
        self.source.start()
        self.addCleanup(self.source.stop)
        self.addCleanup(self.temporary.cleanup)

    def finish_author(self, series):
        series.begin("authoring")
        number = series.grant("authoring", {"prompt_sha256": "fixed"})
        (self.directory / "submission.yaml").write_text("original authored bytes\n")
        series.complete(number)
        series.finish({"status": "passed", "submission_sha256": file_hash(self.directory / "submission.yaml")})

    def test_reservation_is_durable_before_dispatch_and_unknown_cannot_resume(self):
        with LifecycleSeries(self.directory) as series:
            series.begin("authoring")
            series.grant("authoring", {"prompt_sha256": "fixed"})
            saved = json.loads(series.path.read_text())
            self.assertEqual(saved["phases"][0]["grants"][0]["status"], "unknown")
            with self.assertRaises(ValueError):
                series.grant("authoring", {"prompt_sha256": "another"})
        with LifecycleSeries(self.directory) as reopened:
            with self.assertRaises(ValueError):
                reopened.begin("authoring")
            with self.assertRaises(ValueError):
                reopened.begin("authored")

    def test_fixed_policy_source_and_order_cannot_change_on_reopen(self):
        with LifecycleSeries(self.directory) as series:
            self.finish_author(series)
        original = json.loads((self.directory / "series.json").read_text())
        for change in ("cap", "boolean", "order", "source"):
            data = copy.deepcopy(original)
            if change == "cap":
                data["ceilings"]["total"] = 10
            elif change == "boolean":
                data["policy"]["authoring"]["authoring"] = True
            elif change == "order":
                data["phases"][0]["name"] = "authored"
            else:
                data["source_manifest"]["frozen.py"] = "drift"
            (self.directory / "series.json").write_text(json.dumps(data))
            with self.subTest(change=change), self.assertRaises(ValueError):
                with LifecycleSeries(self.directory):
                    pass

    def test_exclusive_lock_prevents_concurrent_admission(self):
        with LifecycleSeries(self.directory):
            with self.assertRaises(BlockingIOError):
                with LifecycleSeries(self.directory):
                    pass

    def test_callbacks_cron_and_rebuilds_follow_declared_caps(self):
        with LifecycleSeries(self.directory) as series:
            self.finish_author(series)
            series.begin("authored")
            with self.assertRaises(ValueError):
                series.grant("runtime", {"kind": "Cron", "event_id": "early"})
            number = series.grant("runtime", {"kind": "Callback", "event_id": "first"})
            series.complete(number)
            with self.assertRaises(ValueError):
                series.grant("runtime", {"kind": "Callback", "event_id": "second"})
            number = series.grant("runtime", {"kind": "Cron", "event_id": "due"})
            series.complete(number)
            with self.assertRaises(ValueError):
                series.grant("runtime", {"kind": "Cron", "event_id": "again"})
            series.finish({"status": "passed"})
            series.begin("mutation")
            for kind, identity in [
                ("runtime", {"kind": "Callback", "revision": 1}),
                ("rebuild", {"target_revision": 2}),
                ("runtime", {"kind": "Callback", "revision": 2}),
                ("rebuild", {"target_revision": 3}),
                ("runtime", {"kind": "Callback", "revision": 3}),
                ("runtime", {"kind": "Cron", "revision": 3}),
            ]:
                series.complete(series.grant(kind, identity))
            with self.assertRaises(ValueError):
                series.grant("rebuild", {"target_revision": 4})
            series.finish({"status": "passed"})
            self.assertEqual(sum(len(r["grants"]) for r in series.data["phases"]), 9)
            with self.assertRaises(ValueError):
                series.begin("mutation")

    def test_failed_or_incomplete_gate_blocks_next_phase(self):
        with LifecycleSeries(self.directory) as series:
            series.begin("authoring")
            report = {"status": "passed"}
            series.finish(report)
            self.assertEqual(report["status"], "failed")
            with self.assertRaises(ValueError):
                series.begin("authored")

    def test_final_report_or_original_yaml_change_blocks_live(self):
        with LifecycleSeries(self.directory) as series:
            self.finish_author(series)
            report = self.directory / "authoring/report.json"
            saved = report.read_bytes()
            report.write_text('{"status":"failed"}')
            with self.assertRaises(ValueError):
                series.begin("authored")
            report.write_bytes(saved)
            (self.directory / "submission.yaml").write_text("rewritten")
            with self.assertRaises(ValueError):
                series.begin("authored")

    def test_failed_harbor_authoring_retains_report_and_latches_without_second_call(self):
        with (
            LifecycleSeries(self.directory) as series,
            patch(MODULE + ".native_controls", return_value={"image": "lab:test", "status": "passed"}),
            patch(MODULE + ".harbor_command", return_value=["harbor"]),
            patch(MODULE + ".subprocess.run", return_value=subprocess.CompletedProcess([], 1)) as dispatch,
            patch(MODULE + ".summarize_trials", side_effect=ValueError("malformed trial")),
        ):
            report = author(series, upstream="http://127.0.0.1:8765/run", image="lab:test")
            self.assertEqual(report["status"], "failed")
            self.assertTrue((self.directory / "authoring/report.json").is_file())
            self.assertEqual(dispatch.call_count, 1)
            with self.assertRaises(ValueError):
                series.begin("authored")

    def test_task_extension_is_scoped_and_has_no_reference_solution(self):
        path = Path(self.temporary.name) / "tasks"
        stage_tasks(path, mode="generation", scenarios=("daily-digest", "invoice-total"))
        digest = (path / "daily-digest/instruction.md").read_text()
        invoice = (path / "invoice-total/instruction.md").read_text()
        self.assertIn("TASK-SPECIFIC LIFECYCLE EXTENSION", digest)
        self.assertNotIn("TASK-SPECIFIC LIFECYCLE EXTENSION", invoice)
        self.assertFalse((path / "daily-digest/solution").exists())
        self.assertFalse((path / "daily-digest/environment/base.yaml").exists())
        self.assertEqual(tuple(select_scenarios()), ("invoice-total", "ticket-routing", "competitor-report"))

    def test_control_failure_prevents_authoring_reservation_and_dispatch(self):
        with (
            LifecycleSeries(self.directory) as series,
            patch(MODULE + ".native_controls", side_effect=ValueError("native gate failed")),
            patch(MODULE + ".subprocess.run") as outgoing,
        ):
            report = author(series, upstream="http://127.0.0.1:8765/run", image="lab:test")
            self.assertEqual(report["status"], "failed")
            self.assertEqual(series.data["phases"][0]["grants"], [])
            outgoing.assert_not_called()

    def test_on_disk_ledger_change_blocks_next_grant(self):
        with LifecycleSeries(self.directory) as series:
            series.begin("authoring")
            altered = json.loads(series.path.read_text())
            altered["ceilings"]["total"] = 10
            series.path.write_text(json.dumps(altered))
            with self.assertRaises(ValueError):
                series.grant("authoring", {"prompt_sha256": "not-dispatched"})

    def test_task_constraints_and_extra_model_calls_fail_before_outgoing_grant(self):
        config = profile.read(workspace_root() / "configs/05-digest-lifecycle.yaml")
        validate_task(config)
        config["lifecycle"]["on_test_fail"]["max_rebuilds"] = 3
        with self.assertRaises(AssertionError):
            validate_task(config)
        config["workflow"]["steps"].append(copy.deepcopy(config["workflow"]["steps"][1]))
        with LifecycleSeries(self.directory) as series:
            backend = AdmittedNativeBackend(series, self.directory, "http://127.0.0.1:8765/run")
            with patch.object(series, "grant") as grant, self.assertRaises(ValueError):
                backend.compile(config, {}, None)
            grant.assert_not_called()

    def test_authoring_acceptance_is_bound_to_original_prompt_yaml_and_fixtures(self):
        trial_dir = Path(self.temporary.name) / "trial"
        (trial_dir / "agent").mkdir(parents=True)
        (trial_dir / "verifier").mkdir()
        raw, prompt = b"original YAML bytes\n", b"frozen task prompt"
        submission_hash = hashlib.sha256(raw).hexdigest()
        prompt_hash = hashlib.sha256(prompt).hexdigest()
        generation = {"submission_sha256": submission_hash, "prompt_sha256": prompt_hash}
        acceptance = {
            "passed": True,
            "submission_sha256": submission_hash,
            "fixture_sha256": "private",
            "runtime_source_manifest": {"src/a.py": "frozen"},
        }
        (trial_dir / "agent/submission.yaml").write_bytes(raw)
        (trial_dir / "agent/prompt.txt").write_bytes(prompt)
        (trial_dir / "agent/generation.json").write_text(json.dumps(generation))
        (trial_dir / "verifier/submission.yaml").write_bytes(raw)
        (trial_dir / "verifier/report.json").write_text(json.dumps(acceptance))
        trial = {"result_path": str(trial_dir / "result.json"), "generation": generation, "acceptance": acceptance}
        frozen = {"src/a.py": "frozen"}
        self.assertEqual(bind_authoring_evidence(trial, prompt_hash, "private", frozen)[0], raw)
        for path in ["agent/prompt.txt", "agent/submission.yaml", "verifier/submission.yaml"]:
            file = trial_dir / path
            original = file.read_bytes()
            file.write_bytes(b"other valid content")
            with self.subTest(path=path), self.assertRaises(ValueError):
                bind_authoring_evidence(trial, prompt_hash, "private", frozen)
            file.write_bytes(original)
        with self.assertRaises(ValueError):
            bind_authoring_evidence(trial, prompt_hash, "different private corpus", frozen)

    def test_harbor_report_does_not_require_opening_the_shared_log_mount(self):
        report_dir = Path(self.temporary.name) / "shared-verifier-log"
        original_open = os.open

        def mount_open(path, flags, *args, **kwargs):
            if Path(path) == report_dir:
                raise PermissionError("Harbor log mount cannot be opened for directory fsync")
            return original_open(path, flags, *args, **kwargs)

        with patch("sapi_config_lab.runtime.lifecycle.os.open", side_effect=mount_open):
            report = verify_submission(Path(self.temporary.name) / "missing.yaml", report_dir)
        self.assertFalse(report["passed"])
        self.assertEqual(report["error_type"], "FileNotFoundError")
        self.assertEqual(json.loads((report_dir / "report.json").read_text()), report)


if __name__ == "__main__":
    unittest.main()
