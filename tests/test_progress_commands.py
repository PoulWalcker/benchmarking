"""Commands report their real stages through the one progress display; Docker, Harbor and models are faked."""

import contextlib
import io
import json
from pathlib import Path
import re
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from sapi_config_lab.coordinate import controls
from sapi_config_lab.coordinate import evaluation as evaluate_command
from sapi_config_lab.coordinate.runs import Run
from sapi_config_lab.execute.host import LOGGED_OBSERVER, LoggedEvent, report_logged

DURATION = r"(?:<1s|\d+s|\d+m\d\ds)"
HARBOR_STAGES = ["setup", "local tests", "task plans", "transport", "native images", "oracle", "nop", "finalizing"]


def transitions(stderr: str, command: str) -> list[tuple[str, str]]:
    """The (stage, state) pairs a command printed, in order."""
    pattern = re.compile(rf"^\[{re.escape(command)}\] \d+/\d+ (.+) · (running|done|failed|unknown|interrupted)\b")
    return [match.groups() for line in stderr.splitlines() if (match := pattern.match(line))]


class HarborProgressTests(unittest.TestCase):
    def run_controls(self, *, local_tests=None, failed_agent=None):
        """Run `sapi-lab harbor` with every host tool faked; return code, stdout, stderr and report."""
        directory = Path(tempfile.mkdtemp())
        output = directory / "run"

        def native_tasks(run, tasks, **kwargs):
            run.native_tasks = tasks

        def transport(run, **kwargs):
            # The provider is silent: only run_logged facts reach the display, never an inner phase.
            log = run.output / "transport.log"
            report_logged(LoggedEvent("started", "transport", log, 0, f"[transport] started · log {log}"))
            for second in (15, 120):
                line = f"[transport] {second // 60}m{second % 60:02d}s · log {log} · last: (no output yet) · quiet {second}s"
                report_logged(LoggedEvent("heartbeat", "transport", log, second, line, "(no output yet)", second))
            report_logged(LoggedEvent("exited", "transport", log, 121, "[transport] exit 0 after 2m01s", code=0))
            return {"exit_code": 0, "passed": True}

        def harbor(argv, task, jobs, job, agent, log, **kwargs):
            trial = jobs / job / "trial"
            (trial / "verifier").mkdir(parents=True)
            reward = 1.0 if agent == "oracle" else 0.0
            (trial / "result.json").write_text(
                json.dumps(
                    {"task_name": task.name, "verifier_result": {"rewards": {"reward": reward}}, "exception_info": None}
                )
            )
            (trial / "verifier/result.json").write_text(
                json.dumps({"execution": True, "acceptance": agent == "oracle", "quality": None})
            )
            log.write_text(agent + " reason\n")
            return 7 if agent == failed_agent else 0

        stdout, stderr = io.StringIO(), io.StringIO()
        with (
            patch("sapi_config_lab.coordinate.runs.running_containers", return_value=""),
            patch("sapi_config_lab.coordinate.runs.checked_harbor", return_value=(["harbor"], "0.21.0")),
            patch(
                "sapi_config_lab.coordinate.runs.docker_preflight",
                return_value={"server_version": "27.0", "context": "test"},
            ),
            patch("sapi_config_lab.coordinate.controls.run_logged", side_effect=local_tests, return_value=0),
            patch("sapi_config_lab.coordinate.controls.invoke", return_value={}),
            patch("sapi_config_lab.coordinate.controls.transport_probe", side_effect=transport),
            patch.object(Run, "use_image"),
            patch.object(Run, "use_native_tasks", native_tasks),
            patch("sapi_config_lab.coordinate.runs.run_job", side_effect=harbor) as dispatch,
            contextlib.redirect_stdout(stdout),
            contextlib.redirect_stderr(stderr),
        ):
            try:
                code = controls.main(["--report-dir", str(output), "--scenario", "invoice-total"])
            except KeyboardInterrupt:
                code = 130
        self.assertIsNone(LOGGED_OBSERVER.get())
        report = json.loads((output / "report.json").read_text())
        return code, stdout.getvalue(), stderr.getvalue(), report, dispatch

    def test_known_stages_in_order_with_silent_transport_and_unchanged_stdout(self):
        code, stdout, stderr, report, dispatch = self.run_controls()
        self.assertEqual(code, 0)
        self.assertEqual(report["status"], "passed")
        self.assertEqual(
            transitions(stderr, "sapi-lab harbor"),
            [(stage, state) for stage in HARBOR_STAGES for state in ("running", "done")],
        )
        output = Path(report["harbor_jobs"]["oracle-invoice-total"]["path"]).parent
        self.assertEqual(stdout.splitlines(), [stdout.strip()])
        self.assertEqual(json.loads(stdout), {"status": "passed", "report": json.loads(stdout)["report"]})
        self.assertTrue(json.loads(stdout)["report"].endswith("/run/report.json"))
        self.assertEqual(output, Path("jobs"))
        self.assertIn("[transport] 2m00s", stderr)
        self.assertIn("quiet 120s", stderr)
        self.assertIn("[sapi-lab harbor]   Harbor job: oracle-invoice-total · task invoice-total", stderr)
        lines = stderr.splitlines()
        for job in ("4/8 transport", "6/8 oracle"):
            start, end = (
                lines.index(f"[sapi-lab harbor] {job} · running"),
                lines.index(f"[sapi-lab harbor] {job} · done <1s"),
            )
            opaque = "\n".join(lines[start + 1 : end]).replace("Harbor builds and runs", "")
            for invented in ("building", "verifying", "responding", "%", "ETA"):
                self.assertNotIn(invented, opaque)
        self.assertRegex(stderr, rf"\[sapi-lab harbor\] PASSED · {DURATION} · 8/8 stages done\n$")
        self.assertEqual(dispatch.call_count, 2)

    def test_failed_verifier_keeps_diagnostics_and_never_reports_later_stages_as_done(self):
        code, stdout, stderr, report, _ = self.run_controls(failed_agent="oracle")
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(stdout)["status"], "failed")
        self.assertEqual(report["failure_stage"], "harbor_oracle")
        self.assertIn(("oracle", "failed"), transitions(stderr, "sapi-lab harbor"))
        self.assertNotIn(("nop", "running"), transitions(stderr, "sapi-lab harbor"))
        self.assertIn(("finalizing", "done"), transitions(stderr, "sapi-lab harbor"))
        self.assertIn("failed at harbor_oracle: Control check failed: harbor_oracle", stderr)
        self.assertIn("  inspect: tail -n 200 ", stderr)
        self.assertRegex(stderr, rf"FAILED · {DURATION} · 6/8 stages done · Failed: oracle · Not run: nop\n$")
        self.assertEqual(stderr.count("FAILED · "), 1)

    def test_timeout_and_interrupt_end_once_with_original_semantics(self):
        cases = (
            (
                subprocess.TimeoutExpired(["tests"], 300),
                1,
                "failed",
                "OUTCOME UNKNOWN · ",
                "unknown",
                "Outcome unknown",
            ),
            (KeyboardInterrupt(), 130, "interrupted", "INTERRUPTED · ", "interrupted", "Interrupted"),
        )
        for error, expected_code, status, final, state, label in cases:
            with self.subTest(error=type(error).__name__):
                code, stdout, stderr, report, dispatch = self.run_controls(local_tests=error)
                self.assertEqual(code, expected_code)
                self.assertEqual(report["status"], status)
                self.assertIn(("local tests", state), transitions(stderr, "sapi-lab harbor"))
                self.assertIn(("finalizing", "done"), transitions(stderr, "sapi-lab harbor"))
                self.assertEqual(stderr.count(final), 1)
                self.assertIn(f"{label}: local tests", stderr.splitlines()[-1])
                dispatch.assert_not_called()
                if status == "interrupted":
                    self.assertEqual(stdout, "")
                else:
                    self.assertEqual(report["failure_category"], "timeout_unknown_outcome")
                    self.assertEqual(json.loads(stdout)["status"], "failed")


RESULT = {"execution": True, "acceptance": True, "quality": {"status": "complete", "score_0_10": 7.0}}


class EvaluateProgressTests(unittest.TestCase):
    def evaluate(self, *arguments, outcome=None):
        root = Path(tempfile.mkdtemp())
        (root / "record").mkdir()
        (root / "record/native-task.json").write_text("{}")
        stdout, stderr = io.StringIO(), io.StringIO()
        with (
            patch(
                "sapi_config_lab.coordinate.native_evaluation.reevaluate_native",
                side_effect=outcome,
                return_value=RESULT,
            ) as reevaluate,
            patch("sapi_config_lab.coordinate.evaluation._require_fresh") as fresh,
            contextlib.redirect_stdout(stdout),
            contextlib.redirect_stderr(stderr),
        ):
            code = evaluate_command.main(["--record", str(root / "record"), "--output", str(root / "out"), *arguments])
        return code, stdout.getvalue(), stderr.getvalue(), reevaluate, fresh

    def test_offline_replay_shows_no_loader_and_keeps_stdout(self):
        for arguments in ((), ("--judgement", "/saved/reply.json")):
            with self.subTest(arguments=arguments):
                code, stdout, stderr, reevaluate, _ = self.evaluate(*arguments)
                self.assertEqual(code, 0)
                self.assertEqual(stdout, json.dumps(RESULT) + "\n")
                self.assertEqual(stderr, "")
                self.assertFalse(reevaluate.call_args.kwargs["dispatch"])

    def test_requested_judge_dispatch_reports_its_stages_once(self):
        endpoint = (
            "--judge-model",
            "judge-x",
            "--judge-upstream",
            "http://judge",
            "--judge-wrapper-evidence",
            "/w.json",
        )
        code, stdout, stderr, reevaluate, fresh = self.evaluate("--dispatch-judge", *endpoint)
        self.assertEqual(code, 0)
        self.assertEqual(stdout, json.dumps(RESULT) + "\n")
        reevaluate.assert_called_once()
        fresh.assert_called_once()
        self.assertEqual(
            transitions(stderr, "sapi-lab evaluate"),
            [
                ("re-evaluate and judge", "running"),
                ("re-evaluate and judge", "done"),
                ("receipt check", "running"),
                ("receipt check", "done"),
            ],
        )
        self.assertIn("[sapi-lab evaluate]   task evaluator process with the requested Judge dispatch", stderr)
        self.assertRegex(stderr, rf"PASSED · {DURATION} · 2/2 stages done\n$")

    def test_failed_unknown_and_rejected_judging_keep_stdout_and_exit_code(self):
        cases = (
            (subprocess.CalledProcessError(1, ["task"], stderr="judge reason"), "failed", "FAILED · ", "failed"),
            (subprocess.TimeoutExpired(["task"], 420), "unknown", "OUTCOME UNKNOWN · ", "unknown"),
        )
        for error, judge, final, state in cases:
            with self.subTest(judge=judge):
                code, stdout, stderr, _, fresh = self.evaluate("--dispatch-judge", outcome=error)
                self.assertEqual(code, 1)
                self.assertEqual(json.loads(stdout)["judge"], judge)
                self.assertEqual(len(stdout.splitlines()), 1)
                self.assertIn(("re-evaluate and judge", state), transitions(stderr, "sapi-lab evaluate"))
                self.assertEqual(stderr.count(final), 1)
                self.assertNotIn("judge reason", stderr)
                fresh.assert_not_called()
        rejected = {**RESULT, "acceptance": False}
        code, stdout, stderr, _, _ = self.evaluate("--dispatch-judge", outcome=lambda *a, **k: rejected)
        self.assertEqual((code, stdout), (1, json.dumps(rejected) + "\n"))
        self.assertRegex(stderr, rf"FAILED · {DURATION} · 1/1 stages done · Result not accepted\n$")


if __name__ == "__main__":
    unittest.main()
