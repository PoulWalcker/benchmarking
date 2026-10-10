"""Run finalization and ledger reservations; Docker, Harbor and models are faked."""

import contextlib
import fcntl
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

from sapi_config_lab.coordinate.ledger import Ledger
from sapi_config_lab.coordinate.live import main as live
from sapi_config_lab.coordinate.native_tasks import select_tasks
from sapi_config_lab.coordinate.provenance import source_manifest
from sapi_config_lab.coordinate.runs import Run, progress, run_experiment
from sapi_config_lab.evaluate.records import load_trials, read_report
from sapi_config_lab.paths import workspace_root


class FakeHost:
    """Patches the host tools a run touches; records what ran."""

    def __init__(self, test, *, containers=("one", "one")):
        self.ran = []
        listing = iter(containers)
        patches = {
            "docker_preflight": lambda: {"server_version": "29.4.0", "context": "test"},
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

    def test_invalid_task_is_refused_before_build_or_image_reuse(self):
        task = self.root / "unsafe-task"
        task.mkdir()
        (task / "images.Dockerfile").write_text("FROM sapi-native-public-base:phase1 AS public\nCOPY . /app/public/\n")
        self.output.mkdir()
        for build in (True, False):
            with (
                self.subTest(build=build),
                patch("sapi_config_lab.coordinate.runs.run_logged") as child,
                patch("sapi_config_lab.coordinate.runs.image_id") as image,
            ):
                run = Run(self.output, {}, source_manifest(), "test")
                with self.assertRaisesRegex(ValueError, "unsafe-task: S5:"):
                    run.use_native_tasks((task,), build=build)
                child.assert_not_called()
                image.assert_not_called()

    def test_docker_preflight_failure_stops_setup_and_records_original_cause(self):
        for cause in (
            "docker CLI not found on PATH",
            "Docker daemon not reachable (context dead): Cannot connect",
            "Docker daemon did not answer within 20s (context slow)",
        ):
            with self.subTest(cause=cause):
                FakeHost(self)
                body = Mock()
                error = RuntimeError(cause)
                with (
                    patch("sapi_config_lab.coordinate.runs.docker_preflight", side_effect=error),
                    patch("sapi_config_lab.coordinate.runs.running_containers") as containers,
                    patch("sapi_config_lab.coordinate.runs.checked_harbor") as harbor,
                    patch("sapi_config_lab.coordinate.runs.progress", wraps=progress),
                    contextlib.redirect_stderr(io.StringIO()) as stderr,
                ):
                    report = run_experiment(self.root / str(len(cause)), {}, body, prefix="t")
                self.assertEqual(report["status"], "failed")
                self.assertEqual(report["failure_stage"], "docker preflight")
                self.assertEqual(report["error"], "RuntimeError: " + cause)
                self.assertIsNone(report["log"])
                self.assertEqual(report["logs"], {})
                self.assertNotIn("timeout_unknown_outcome", report.values())
                self.assertIn("failed at docker preflight: " + cause, stderr.getvalue())
                containers.assert_not_called()
                harbor.assert_not_called()
                body.assert_not_called()

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

    def test_nested_failure_keeps_the_inner_stage_log_and_exception(self):
        FakeHost(self)
        error = subprocess.CalledProcessError(7, ["child"])
        stderr, stdout = io.StringIO(), io.StringIO()

        def body(run):
            with self.assertRaises(subprocess.CalledProcessError) as raised:
                with run.step("outer", log="outer"):
                    with run.step("inner", log="inner") as log:
                        log.write_bytes(b"old\r\x1b[31mreason\x1b[0m\n\xff\n\n")
                        raise error
            self.assertIs(raised.exception, error)
            raise raised.exception

        with (
            patch("sapi_config_lab.coordinate.runs.progress", progress),
            contextlib.redirect_stderr(stderr),
            contextlib.redirect_stdout(stdout),
            patch("sapi_config_lab.coordinate.runs.checked_harbor", return_value=(["harbor"], "0.21.0")),
        ):
            run_experiment(
                self.output, {}, body, prefix="t", classify=lambda seen: self.assertIs(seen, error) or "child"
            )
        report = self.saved()
        self.assertEqual(report["failure_stage"], "inner")
        self.assertEqual(report["log"], "inner.log")
        self.assertEqual(report["log_tail"], ["old", "reason", "�"])
        self.assertEqual(report["logs"], {"outer": "outer.log", "inner": "inner.log"})
        self.assertEqual(report["error"], str(type(error).__name__) + ": " + str(error))
        self.assertIn("failed at inner: exit 7\n", stderr.getvalue())
        self.assertIn(f"  log: {self.output.resolve() / 'inner.log'}\n", stderr.getvalue())
        self.assertIn("  last lines:\n    old\n    reason\n    �\n", stderr.getvalue())
        self.assertIn(f"  inspect: tail -n 200 {self.output.resolve() / 'inner.log'}\n", stderr.getvalue())
        self.assertEqual(stdout.getvalue(), "")

    def test_log_tail_and_terminal_are_bounded(self):
        FakeHost(self)
        stderr = io.StringIO()

        def body(run):
            with run.step("bounded", log="bounded") as log:
                log.write_text("discard me\n" + "\n".join(f"{n}:" + "界" * 500 for n in range(80)))
                raise RuntimeError("first line\nsecond line")

        with patch("sapi_config_lab.coordinate.runs.progress", progress), contextlib.redirect_stderr(stderr):
            run_experiment(self.output, {}, body, prefix="t")
        tail = self.saved()["log_tail"]
        self.assertTrue(tail)
        self.assertTrue(tail[-1].startswith("79:"))
        self.assertLessEqual(len(tail), 40)
        self.assertTrue(all(len(line) <= 300 for line in tail))
        self.assertLessEqual(len("\n".join(tail).encode()), 8192)
        self.assertNotIn("discard me", "\n".join(tail))
        self.assertIn("failed at bounded: first line second line\n", stderr.getvalue())

    def test_terminal_shows_only_the_last_twenty_of_forty_tail_lines(self):
        FakeHost(self)
        stderr = io.StringIO()

        def body(run):
            with run.step("many lines", log="many") as log:
                log.write_text("\n".join(f"line {n}" for n in range(60)))
                raise RuntimeError("boom")

        with patch("sapi_config_lab.coordinate.runs.progress", progress), contextlib.redirect_stderr(stderr):
            run_experiment(self.output, {}, body, prefix="t")
        self.assertEqual(self.saved()["log_tail"], [f"line {n}" for n in range(20, 60)])
        self.assertNotIn("    line 39\n", stderr.getvalue())
        self.assertIn("    line 40\n", stderr.getvalue())
        self.assertIn("    line 59\n", stderr.getvalue())

    def test_removed_or_unreadable_log_does_not_replace_the_error(self):
        FakeHost(self)
        for unreadable in (False, True):
            with self.subTest(unreadable=unreadable):
                output = self.root / str(unreadable)
                run = Run(output, {}, {}, "t")
                output.mkdir()
                error = subprocess.CalledProcessError(9, ["child"], stderr="do not substitute stderr")
                with self.assertRaises(subprocess.CalledProcessError) as raised:
                    with run.step("removed", log="gone") as log:
                        if unreadable:
                            log.mkdir()
                        raise error
                self.assertIs(raised.exception, error)
                self.assertEqual(run.report["log_tail"], [])

    def test_timeout_stage_and_reservation_keep_an_unknown_outcome(self):
        FakeHost(self)
        stderr = io.StringIO()
        error = subprocess.TimeoutExpired("paid child", 2.5)
        ledger = Ledger.open(self.root / "ledger.json", ceilings={"runtime": 1}, stop_after_failure=False)

        def body(run):
            with ledger.reserved("runtime", "slow", 1, run.output / "report.json", 1):
                with run.step("runtime", log="runtime") as log:
                    log.write_text("possibly dispatched\n")
                    raise error

        with (
            patch("sapi_config_lab.coordinate.runs.progress", progress),
            contextlib.redirect_stderr(stderr),
            patch("sapi_config_lab.coordinate.runs.checked_harbor", return_value=(["harbor"], "0.21.0")),
        ):
            run_experiment(self.output, {}, body, prefix="t")
        report = self.saved()
        self.assertEqual(report["failure_category"], "timeout_unknown_outcome")
        self.assertEqual(report["failure_stage"], "runtime")
        self.assertEqual(report["log_tail"], ["possibly dispatched"])
        self.assertIn("outcome unknown at runtime: timed out after 2.5s (not a failure verdict)", stderr.getvalue())
        self.assertNotIn("failed", stderr.getvalue())
        self.assertEqual(ledger.data["events"][0]["status"], "unknown")
        self.assertEqual(ledger.spent("runtime"), 1)

    def test_classified_unknown_outcome_is_never_printed_as_a_failure_verdict(self):
        FakeHost(self)
        stderr = io.StringIO()

        def body(run):
            with run.step("runtime"):
                raise RuntimeError("wrapper completion is missing")

        with patch("sapi_config_lab.coordinate.runs.progress", progress), contextlib.redirect_stderr(stderr):
            run_experiment(self.output, {}, body, prefix="t", classify=lambda error: "timeout_unknown_outcome")
        self.assertEqual(self.saved()["failure_category"], "timeout_unknown_outcome")
        self.assertIn(
            "outcome unknown at runtime: wrapper completion is missing (not a failure verdict)", stderr.getvalue()
        )
        self.assertNotIn("failed", stderr.getvalue())

    def test_interrupt_keeps_the_inner_stage_and_same_object_without_classifying(self):
        FakeHost(self)
        interrupt = KeyboardInterrupt()
        stderr = io.StringIO()
        classify = Mock()

        def body(run):
            with run.step("outer"):
                with run.step("x", log="x"):
                    raise interrupt

        with (
            patch("sapi_config_lab.coordinate.runs.progress", progress),
            contextlib.redirect_stderr(stderr),
            self.assertRaises(KeyboardInterrupt) as raised,
        ):
            run_experiment(self.output, {}, body, prefix="t", classify=classify)
        self.assertIs(raised.exception, interrupt)
        report = self.saved()
        self.assertEqual(report["status"], "interrupted")
        self.assertEqual(report["interrupted_stage"], "x")
        self.assertEqual(report["logs"], {"x": "x.log"})
        for key in ("failure_stage", "log", "log_tail", "error", "failure_category"):
            self.assertNotIn(key, report)
        classify.assert_not_called()
        self.assertIn("interrupted at x; in-flight work has an unknown outcome", stderr.getvalue())

    def test_setup_interrupt_survives_cleanup_errors(self):
        FakeHost(self)
        interrupt = KeyboardInterrupt()
        with (
            patch("sapi_config_lab.coordinate.runs.running_containers", side_effect=interrupt),
            self.assertRaises(KeyboardInterrupt) as raised,
        ):
            run_experiment(self.output, {}, lambda run: None, prefix="t")
        self.assertIs(raised.exception, interrupt)
        report = self.saved()
        self.assertEqual(report["status"], "interrupted")
        self.assertEqual([row["stage"] for row in report["cleanup_errors"]], ["containers"])
        self.assertNotIn("error", report)

    def test_other_base_exceptions_pass_through_steps_without_diagnostics(self):
        FakeHost(self)
        error = SystemExit(9)

        def body(run):
            with run.step("exit", log="exit"):
                raise error

        with self.assertRaises(SystemExit) as raised:
            run_experiment(self.output, {}, body, prefix="t")
        self.assertIs(raised.exception, error)
        report = self.saved()
        for key in ("interrupted_stage", "failure_stage", "log", "log_tail", "error", "failure_category"):
            self.assertNotIn(key, report)

    def test_native_build_transport_and_harbor_failures_name_their_logs(self):
        task = select_tasks(workspace_root() / "tasks", ("invoice-total",))[0]
        cases = (
            ("native build", "native-build"),
            ("transport", "transport"),
            ("oracle-invoice-total", "oracle-invoice-total"),
        )
        for stage, stem in cases:
            with self.subTest(stage=stage):
                FakeHost(self)
                output = self.root / stem
                error = subprocess.CalledProcessError(7, ["host child"])
                stderr = io.StringIO()

                def dispatch(*args, stage=stage, error=error, **kwargs):
                    log = args[5] if stage == "oracle-invoice-total" else args[1]
                    log.write_text("host child reason\n")
                    if stage == "native build":
                        return 7
                    raise error

                def body(run, stage=stage):
                    run.native_tasks = (task,)
                    if stage == "native build":
                        run.use_native_tasks((task,), build=True)
                    elif stage == "transport":
                        run.transport(task)
                    else:
                        run.harbor(stage, task, "oracle")

                with (
                    patch("sapi_config_lab.coordinate.runs.run_logged", side_effect=dispatch),
                    patch("sapi_config_lab.coordinate.runs.run_job", side_effect=dispatch),
                    patch("sapi_config_lab.coordinate.runs.progress", progress),
                    contextlib.redirect_stderr(stderr),
                ):
                    report = run_experiment(output, {}, body, prefix="t")
                self.assertEqual(report["failure_stage"], stage)
                self.assertEqual(report["log"], stem + ".log")
                self.assertEqual(report["log_tail"], ["host child reason"])
                self.assertEqual(report["logs"], {stem: stem + ".log"})
                self.assertIn(f"failed at {stage}: exit 7", stderr.getvalue())
                if stage in ("transport", "oracle-invoice-total"):
                    self.assertIn(f"  jobs: {output.resolve() / 'jobs' / stem}", stderr.getvalue())

    def test_report_lists_bridge_and_other_top_level_logs_even_when_empty(self):
        FakeHost(self, containers=("one", "one", "one", "one"))

        def body(run):
            (run.output / "other.log").write_text("other")
            with run.bridge({}, "case", bindings=self.root / "unused"):
                pass
            run.report["status"] = "passed"

        def start(*args, **kwargs):
            args[5].write_text("bridge")

        with (
            patch("sapi_config_lab.coordinate.runs.start_bridge", side_effect=start),
            patch("sapi_config_lab.coordinate.runs.stop_bridge"),
        ):
            run_experiment(self.output, {}, body, prefix="t")
        self.assertEqual(self.saved()["logs"], {"case-bridge": "case-bridge.log", "other": "other.log"})
        run_experiment(self.root / "empty", {}, lambda run: None, prefix="t")
        self.assertEqual(json.loads((self.root / "empty/report.json").read_text())["logs"], {})


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

    def test_interrupted_reservation_stays_unknown_and_blocks_retry(self):
        ledger = self.ledger()
        interrupt = KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt) as raised:
            with ledger.reserved("runtime", "interrupted", 1, self.report, 4):
                raise interrupt
        self.assertIs(raised.exception, interrupt)
        self.assertEqual(ledger.data["events"][0]["status"], "unknown")
        self.assertEqual(ledger.spent("runtime"), 1)
        with self.assertRaisesRegex(ValueError, "unknown outcome"):
            self.ledger().reserve("runtime", "retry", 1, self.report, 4)

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
        self.assertEqual(report["failure_stage"], "preflight")
        self.assertIn("failed at preflight: The cohort needs", stderr.getvalue())
        self.assertIn("finalizing", stderr.getvalue())
