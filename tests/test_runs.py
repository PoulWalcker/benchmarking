"""Run finalization and ledger reservations; Docker, Harbor and models are faked."""

import contextlib
import fcntl
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from sapi_config_lab.coordinate.ledger import Ledger
from sapi_config_lab.coordinate.live import main as live
from sapi_config_lab.coordinate.native_tasks import select_tasks
from sapi_config_lab.coordinate.runs import Run, run_experiment
from sapi_config_lab.evaluate.records import load_trials, read_report
from sapi_config_lab.paths import workspace_root


class FakeHost:
    """Patches the host tools a run touches; records what ran."""

    def __init__(self, test, *, containers=("one", "one")):
        self.ran = []
        listing = iter(containers)
        patches = {
            "running_containers": lambda: next(listing) + "\n",
            "checked_harbor": lambda: (["harbor"], "0.21.0"),
            "image_id": lambda tag: "sha256:fixed",
            "pin_base_image": lambda identity, prefix: prefix + "-base:fixed",
            "run_job": lambda harbor, tasks, jobs, job, agent, log, **kw: self.ran.append([*harbor, job]) or 0,
        }
        for name, fake in patches.items():
            patcher = patch("sapi_config_lab.coordinate.runs." + name, side_effect=fake)
            patcher.start()
            test.addCleanup(patcher.stop)


class RunTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.output = self.root / "run"
        quiet = patch("sapi_config_lab.coordinate.runs.progress")
        quiet.start()
        self.addCleanup(quiet.stop)

    def saved(self):
        return json.loads((self.output / "report.json").read_text())

    def test_a_setup_failure_still_writes_a_failed_report(self):
        FakeHost(self)
        with patch("sapi_config_lab.coordinate.runs.checked_harbor", side_effect=RuntimeError("no harbor")):
            run_experiment(self.output, {}, lambda run: None, prefix="t")
        self.assertEqual(self.saved()["status"], "failed")
        self.assertIn("no harbor", self.saved()["error"])

    def test_the_body_failure_is_kept_apart_from_cleanup_failures(self):
        FakeHost(self, containers=("one", "two"))

        def body(run):
            raise RuntimeError("boom")

        run_experiment(self.output, {}, body, prefix="t")
        report = self.saved()
        self.assertIn("boom", report["error"])
        self.assertEqual([e["stage"] for e in report["cleanup_errors"]], ["containers"])
        self.assertFalse(report["existing_container_identities_preserved"])

    def test_a_native_run_passes_its_final_check(self):
        host = FakeHost(self)

        def body(run):
            run.use_image("lab")
            run.native_tasks = select_tasks(workspace_root() / "tasks", ("invoice-total",))
            run.harbor("oracle", run.native_tasks[0], "oracle")
            run.report["status"] = "passed"

        run_experiment(self.output, {}, body, prefix="sapi-test")
        report = self.saved()
        self.assertEqual(report["status"], "passed", report.get("cleanup_errors"))
        self.assertEqual(report["phases"][-1]["phase"], "final")
        self.assertEqual(len(host.ran), 1)

    def test_a_pinned_input_changed_after_the_last_job_fails_the_run(self):
        FakeHost(self)
        pinned = self.root / "selection.json"
        pinned.write_text("{}")

        def body(run):
            run.pin("selection", pinned)
            run.check("before")
            run.report["status"] = "passed"
            pinned.write_text('{"edited": true}')

        run_experiment(self.output, {}, body, prefix="t")
        report = self.saved()
        self.assertEqual(report["status"], "failed")
        self.assertEqual([e["stage"] for e in report["cleanup_errors"]], ["final_check"])
        self.assertTrue(report["source_unchanged"])

    def test_native_job_writes_partial_references_without_collection_and_cannot_repeat(self):
        FakeHost(self)

        def interrupted(harbor, tasks, jobs, job, agent, log, **kw):
            trial = jobs / job / "native-trial"
            (trial / "verifier/world").mkdir(parents=True)
            (trial / "config.json").write_text(json.dumps({"task": {"path": str(tasks)}}))
            (trial / "verifier/world/receipt.json").write_text('{"reserved": true}')
            raise RuntimeError("interrupted")

        def body(run):
            run.use_image("lab")
            run.native_tasks = select_tasks(workspace_root() / "tasks", ("invoice-total",))
            with self.assertRaisesRegex(RuntimeError, "interrupted"):
                run.harbor("oracle", run.native_tasks[0], "oracle")
            with self.assertRaisesRegex(ValueError, "twice"):
                run.harbor("oracle", run.native_tasks[0], "oracle")
            raise RuntimeError("job failed")

        with patch("sapi_config_lab.coordinate.runs.run_job", side_effect=interrupted) as dispatch:
            run_experiment(self.output, {}, body, prefix="t")
        self.assertEqual(dispatch.call_count, 1)
        saved = self.saved()
        row = saved["harbor_jobs"]["oracle"]["trials"][0]
        self.assertTrue(row["partial"])
        self.assertEqual(row["trial_path"], "jobs/oracle/native-trial")
        self.assertEqual(row["task_name"], "invoice-total")
        self.assertNotIn("result", row)
        self.assertTrue((self.output / row["evidence_path"] / "world/receipt.json").exists())
        self.assertNotIn("harbor_timeouts", saved)
        partial = load_trials(self.output / "jobs/oracle")
        self.assertEqual(len(partial), 1)
        self.assertIsNone(partial[0]["result"]["acceptance"])

    def test_job_references_do_not_duplicate_the_authoritative_derived_trial(self):
        FakeHost(self)
        original = {"execution": True, "acceptance": True, "quality": None}
        derived = {
            "execution": True,
            "acceptance": True,
            "quality": {"status": "complete", "score_0_10": 7.32, "normalized_reward": 0.732},
        }

        def dispatch(harbor, task, jobs, job, agent, log, **kw):
            trial = jobs / job / "native-trial"
            (trial / "verifier/evaluation").mkdir(parents=True)
            (trial / "result.json").write_text(
                json.dumps(
                    {"task_name": task.name, "verifier_result": {"rewards": {"reward": 1.0}}, "exception_info": None}
                )
            )
            (trial / "verifier/result.json").write_text(json.dumps(original))
            return 0

        def body(run):
            run.native_tasks = select_tasks(workspace_root() / "tasks", ("invoice-total",))
            _, trials = run.harbor("live", run.native_tasks[0], "oracle")
            trial = trials[0]
            output = Path(trial["verdict_path"]).parent / "paid-evaluation"
            output.mkdir()
            (output / "result.json").write_text(json.dumps(derived))
            trial.update(
                result=derived,
                verdict_path=str(output / "result.json"),
                evaluation_path=str(output / "evaluation/report.json"),
            )
            run.report.update(status="passed", trials=trials)

        with patch("sapi_config_lab.coordinate.runs.run_job", side_effect=dispatch):
            run_experiment(self.output, {}, body, prefix="t")
        saved = read_report(self.output / "report.json", root=self.output)
        self.assertEqual(len(saved["trials"]), 1)
        self.assertEqual(saved["trials"][0]["result"], derived)
        self.assertEqual(saved["trials"][0]["rewards"], {"reward": 1.0})
        reference = saved["recorded"]["harbor_jobs"]["live"]["trials"][0]
        self.assertNotIn("result_path", reference)
        self.assertEqual(json.loads((self.output / reference["evidence_path"] / "result.json").read_text()), original)
        verdict = Path(saved["trials"][0]["verdict_path"])
        verdict.unlink()
        missing = read_report(self.output / "report.json", root=self.output)["trials"]
        self.assertEqual(len(missing), 1)
        self.assertEqual(missing[0]["result"], {"execution": None, "acceptance": None, "quality": None})
        verdict.write_text("{}")
        with self.assertRaises(ValueError):
            read_report(self.output / "report.json", root=self.output)

    def test_native_source_mismatch_prevents_dispatch(self):
        host = FakeHost(self)

        def body(run):
            run.use_image("lab")
            run.native_tasks = select_tasks(workspace_root() / "tasks", ("invoice-total",))
            run.sources = {"changed": "source"}
            run.harbor("oracle", run.native_tasks[0], "oracle")

        run_experiment(self.output, {}, body, prefix="t")
        self.assertEqual(host.ran, [])
        self.assertIn("Sources changed (before oracle)", self.saved()["error"])

    def test_native_admission_preserves_verifier_environment_arguments(self):
        FakeHost(self)

        def body(run):
            run.use_image("lab")
            run.native_tasks = select_tasks(workspace_root() / "tasks", ("checkout-recovery",))
            run.harbor(
                "admission", run.native_tasks[0], "oracle", admission=True, verifier_env=["SAPI_CASE_NAME=chosen"]
            )
            run.report["status"] = "passed"

        with patch("sapi_config_lab.coordinate.runs.run_job", return_value=0) as dispatch:
            run_experiment(self.output, {}, body, prefix="t")
        self.assertEqual(
            dispatch.call_args.kwargs["verifier_env"], ["SAPI_CASE_NAME=chosen", "SAPI_HOSTED_ADMISSION=1"]
        )
        self.assertEqual(self.saved()["status"], "passed")

    def test_a_timeout_is_an_unknown_outcome_and_an_existing_directory_is_refused(self):
        FakeHost(self)

        def body(run):
            raise subprocess.TimeoutExpired("harbor", 1)

        run_experiment(self.output, {}, body, prefix="t")
        self.assertEqual(self.saved()["failure_category"], "timeout_unknown_outcome")
        with self.assertRaisesRegex(ValueError, "never overwritten"):
            run_experiment(self.output, {}, body, prefix="t")


class LedgerTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.report = self.root / "run/report.json"

    def ledger(self, ceiling=4, stop=False):
        return Ledger.open(self.root / "ledger.json", ceilings={"runtime": ceiling}, stop_after_failure=stop)

    def test_run_and_ledger_ceilings_are_both_enforced(self):
        ledger = self.ledger(ceiling=4)
        with ledger.reserved("runtime", "a", 2, self.report, 3) as outcome:
            outcome.passed = True
        with self.assertRaisesRegex(ValueError, "Run ceiling"):
            ledger.reserve("runtime", "b", 2, self.report, 3)
        other = self.root / "other/report.json"
        ledger.finish(ledger.reserve("runtime", "c", 2, other, 3), True)
        with self.assertRaisesRegex(ValueError, "Ledger ceiling"):
            ledger.reserve("runtime", "d", 1, other, 3)
        self.assertEqual(ledger.spent("runtime"), 4)

    def test_an_unknown_outcome_is_never_released(self):
        ledger = self.ledger()
        ledger.reserve("runtime", "crashed", 1, self.report, 4)
        with self.assertRaisesRegex(ValueError, "unknown outcome"):
            self.ledger().reserve("runtime", "next", 1, self.report, 4)
        other = Ledger.open(self.root / "timeout.json", ceilings={"runtime": 4}, stop_after_failure=False)
        with self.assertRaises(subprocess.TimeoutExpired), other.reserved("runtime", "slow", 1, self.report, 4):
            raise subprocess.TimeoutExpired("harbor", 1)
        self.assertEqual(other.data["events"][0]["status"], "unknown")
        self.assertEqual(other.spent("runtime"), 1)

    def test_failure_policy_repeats_and_reopening_are_fixed(self):
        continuing = self.ledger(stop=False)
        with self.assertRaises(RuntimeError), continuing.reserved("runtime", "a", 1, self.report, 4):
            raise RuntimeError("gate failed")
        continuing.finish(continuing.reserve("runtime", "b", 1, self.report, 4), True)
        with self.assertRaisesRegex(ValueError, "cannot be repeated"):
            continuing.reserve("runtime", "a", 1, self.report, 4)
        with self.assertRaisesRegex(ValueError, "ceilings differ"):
            Ledger.open(self.root / "ledger.json", ceilings={"runtime": 5}, stop_after_failure=False)
        self.assertEqual(
            Ledger.open(self.root / "ledger.json", ceilings=None, stop_after_failure=None).spent("runtime"), 2
        )
        stopping = Ledger.open(self.root / "stop.json", ceilings={"runtime": 4}, stop_after_failure=True)
        stopping.finish(stopping.reserve("runtime", "a", 1, self.report, 4), False)
        with self.assertRaisesRegex(ValueError, "failure latch"):
            stopping.reserve("runtime", "b", 1, self.report, 4)
        with patch("sapi_config_lab.coordinate.ledger.source_manifest", return_value={"edited": "x"}):
            with self.assertRaisesRegex(ValueError, "Sources changed"):
                Ledger.open(self.root / "ledger.json", ceilings=None, stop_after_failure=None)

    def test_a_ledger_held_by_another_process_admits_nothing(self):
        ledger = self.ledger()
        with (self.root / "ledger.lock").open("a") as held:
            fcntl.flock(held.fileno(), fcntl.LOCK_EX)
            with self.assertRaisesRegex(ValueError, "Another process"):
                ledger.reserve("runtime", "a", 1, self.report, 4)
        self.assertEqual(ledger.spent("runtime"), 0)


class LiveCeilingTests(unittest.TestCase):
    def test_a_cohort_larger_than_max_calls_never_reaches_harbor_or_the_ledger(self):
        root = Path(tempfile.mkdtemp())
        host = FakeHost(self)
        stub = root / "control.json"
        stub.write_text("{}")
        gate = {"oracle": {"trials": [{"task_name": "checkout-recovery"}]}}
        stdout, stderr = io.StringIO(), io.StringIO()
        with (
            patch("sapi_config_lab.coordinate.live.validate_control", return_value=gate),
            patch.object(Run, "use_native_tasks"),
        ):
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                code = live(
                    [
                        "--stub-report",
                        str(stub),
                        "--scenario",
                        "checkout-recovery",
                        "--max-calls",
                        "0",
                        "--report-dir",
                        str(root / "run"),
                        "--preflight-only",
                    ]
                )
        report = json.loads((root / "run/report.json").read_text())
        self.assertEqual(code, 1)
        self.assertIn("needs up to 1 model calls; --max-calls allows 0", report["error"])
        self.assertEqual(host.ran, [])
        self.assertFalse((root / "run/ledger.json").exists())
        # Progress is for people and goes to stderr; stdout stays exactly one JSON document.
        self.assertEqual(json.loads(stdout.getvalue())["status"], "failed")
        self.assertEqual(len(stdout.getvalue().splitlines()), 1)
        self.assertIn("controls: checking", stderr.getvalue())
        self.assertIn("failed: ValueError", stderr.getvalue())
        self.assertIn("finalizing", stderr.getvalue())
