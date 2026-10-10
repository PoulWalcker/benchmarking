"""The shared CLI progress tracker: stage state, TTY and line rendering, lifecycle."""

import contextlib
import io
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

from sapi_config_lab.coordinate import progress
from sapi_config_lab.coordinate.progress import SPINNER, Tracker, tracking
from sapi_config_lab.execute.host import LOGGED_OBSERVER, LoggedEvent, run_logged


class FakeClock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


def line_tracker(stages=("setup", "work", "finalizing"), **options):
    stream, clock = io.StringIO(), FakeClock()
    tracker = Tracker("sapi-lab demo", stages, stream=stream, clock=clock, interactive=False, **options)
    return tracker, stream, clock


class LineModeTests(unittest.TestCase):
    def test_transitions_and_final_summary_are_plain_ordered_lines(self):
        tracker, stream, clock = line_tracker()
        for name, seconds in (("setup", 3), ("work", 62), ("finalizing", 1)):
            with tracker.stage(name):
                clock.now += seconds
        tracker.finish("passed")
        self.assertEqual(
            stream.getvalue().splitlines(),
            [
                "[sapi-lab demo] started · stages: setup → work → finalizing",
                "[sapi-lab demo] 1/3 setup · running",
                "[sapi-lab demo] 1/3 setup · done 0m03s",
                "[sapi-lab demo] 2/3 work · running",
                "[sapi-lab demo] 2/3 work · done 1m02s",
                "[sapi-lab demo] 3/3 finalizing · running",
                "[sapi-lab demo] 3/3 finalizing · done 0m01s",
                "[sapi-lab demo] passed after 1m06s · 3/3 stages done",
            ],
        )
        self.assertNotIn("\x1b", stream.getvalue())

    def test_stage_exceptions_propagate_unchanged_and_end_the_stage_truthfully(self):
        cases = (
            (RuntimeError("boom"), "failed"),
            (subprocess.TimeoutExpired("harbor", 5), "unknown"),
            (KeyboardInterrupt(), "interrupted"),
        )
        for error, state in cases:
            with self.subTest(state=state):
                tracker, stream, _ = line_tracker()
                with self.assertRaises(type(error)) as raised, tracker.stage("work"):
                    raise error
                self.assertIs(raised.exception, error)
                self.assertIn(f"[sapi-lab demo] 2/3 work · {state} 0m00s", stream.getvalue())

    def test_pending_stage_is_never_presented_as_done_and_finish_renders_once(self):
        tracker, stream, clock = line_tracker()
        tracker.end("work", "done")
        tracker.end("absent", "done")
        with tracker.stage("setup"):
            clock.now += 1
        tracker.start("work")
        tracker.finish("failed")
        tracker.finish("passed")
        tracker.end("work", "done")
        tracker.start("finalizing")
        lines = stream.getvalue().splitlines()
        self.assertNotIn("work · done", stream.getvalue())
        self.assertEqual(
            lines[-1],
            "[sapi-lab demo] failed after 0m01s · 1/3 stages done · unknown: work · not run: finalizing",
        )
        self.assertEqual(sum("failed after" in line for line in lines), 1)
        with self.assertRaises(ValueError):
            tracker.end("setup", "passed")

    def test_unknown_outcome_and_rejected_result_are_distinct_from_failure(self):
        tracker, stream, _ = line_tracker(("work",))
        with self.assertRaises(RuntimeError), tracker.stage("work"):
            raise RuntimeError("wrapper completion is missing")
        tracker.unknown_outcome()
        tracker.finish("unknown")
        self.assertTrue(stream.getvalue().endswith("outcome unknown after 0m00s · 0/1 stages done · unknown: work\n"))
        tracker, stream, _ = line_tracker(("work",))
        with tracker.stage("work"):
            pass
        tracker.finish("failed")
        self.assertTrue(stream.getvalue().endswith("failed after 0m00s · 1/1 stages done · result not accepted\n"))

    def test_late_and_unexpected_stages_follow_the_latest_started_stage(self):
        tracker, stream, _ = line_tracker(("setup", "preflight", "finalizing"))
        with tracker.stage("setup"), tracker.stage("preflight"):
            pass
        tracker.add_stages(["case 1/2 a", "case 2/2 b", "setup"])
        with tracker.stage("case 1/2 a"):
            pass
        with tracker.stage("surprise"):
            pass
        self.assertEqual(
            [stage.name for stage in tracker.stages],
            ["setup", "preflight", "case 1/2 a", "surprise", "case 2/2 b", "finalizing"],
        )
        self.assertIn("[sapi-lab demo] 4/6 surprise · done", stream.getvalue())

    def test_subprocess_lines_pass_through_and_heartbeat_is_bounded(self):
        tracker, stream, clock = line_tracker(("transport",), heartbeat=15)
        log = Path("/runs/r/transport.log")
        tracker.start("transport")
        tracker.observe(LoggedEvent("started", "transport", log, 0, "[transport] started · log /runs/r/transport.log"))
        for second in range(1, 121):
            clock.now += 1
            tracker.refresh()
            if second % 15 == 0:
                line = f"[transport] {second // 60}m{second % 60:02d}s · log {log} · last: (no output yet) · quiet {second}s"
                tracker.observe(LoggedEvent("heartbeat", "transport", log, second, line, "(no output yet)", second))
        tracker.observe(LoggedEvent("exited", "transport", log, 120, "[transport] exit 0 after 2m00s", code=0))
        for _ in range(40):
            clock.now += 1
            tracker.refresh()
        lines = stream.getvalue().splitlines()
        self.assertEqual(sum(line.startswith("[transport] ") for line in lines), 10)
        self.assertIn("[transport] 2m00s · log /runs/r/transport.log · last: (no output yet) · quiet 120s", lines)
        own = [line for line in lines if line.startswith("[sapi-lab demo] 1/1 transport · running ")]
        self.assertEqual(
            own, ["[sapi-lab demo] 1/1 transport · running 2m15s", "[sapi-lab demo] 1/1 transport · running 2m30s"]
        )
        self.assertTrue(all(word not in stream.getvalue() for word in ("building", "verifying", "responding", "%")))


class Terminal(io.StringIO):
    def isatty(self):
        return True


def screen(text: str) -> list[str]:
    """Replay the cursor-up/clear protocol into the lines a terminal finally shows."""
    rows: list[str] = []
    for chunk in re.split(r"(\x1b\[\d+A\r|\x1b\[J)", text):
        if not chunk:
            continue
        if chunk == "\x1b[J":
            continue
        move = re.fullmatch(r"\x1b\[(\d+)A\r", chunk)
        if move:
            del rows[len(rows) - int(move.group(1)) :]
            continue
        rows.extend(chunk.splitlines())
    return rows


def tty_tracker(stages=("setup", "transport", "oracle", "finalizing"), size=(100, 40)):
    stream, clock = Terminal(), FakeClock()
    tracker = Tracker("sapi-lab harbor", stages, stream=stream, clock=clock, size=lambda: size)
    return tracker, stream, clock


class TerminalModeTests(unittest.TestCase):
    def test_frame_shows_header_ordered_stages_nested_context_and_elapsed(self):
        tracker, stream, clock = tty_tracker()
        self.assertTrue(tracker.interactive)
        with tracker.stage("setup"):
            clock.now += 3
        tracker.start("transport")
        log = Path("/runs/r/transport.log")
        with tracker.detail("Harbor job transport · no inner phase reported"):
            tracker.observe(LoggedEvent("started", "transport", log, 0, "ignored on a terminal"))
            clock.now += 15
            tracker.observe(LoggedEvent("heartbeat", "transport", log, 15, "ignored", "(no output yet)", 15))
            clock.now += 5
            tracker.refresh()
            frame = screen(stream.getvalue())
        spin = SPINNER[1]
        self.assertEqual(
            frame,
            [
                f"{spin} sapi-lab harbor · 0m23s · stage 2/4 transport",
                "  ✓ setup       0m03s",
                f"  {spin} transport   0m20s",
                "      Harbor job transport · no inner phase reported",
                "      transport 0m20s · last: (no output yet) · quiet 15s",
                "      log /runs/r/transport.log",
                "  · oracle",
                "  · finalizing",
            ],
        )
        self.assertNotIn("ignored", stream.getvalue())

    def test_notes_stay_above_the_frame_and_final_frame_has_no_spinner(self):
        tracker, stream, clock = tty_tracker(("setup", "oracle"))
        with tracker.stage("setup"):
            clock.now += 1
        tracker.start("oracle")
        tracker.note("oracle: 1 Harbor tasks through real n8n")
        log = Path("/runs/r/oracle.log")
        tracker.observe(LoggedEvent("exited", "oracle-x", log, 9, "[oracle-x] exit 7 after 0m09s", code=7))
        with self.assertRaises(RuntimeError), tracker.stage("oracle"):
            raise RuntimeError("Control check failed")
        tracker.finish("failed")
        tracker.refresh()
        tracker.note("after the end")
        frame = screen(stream.getvalue())
        self.assertEqual(frame[:2], ["oracle: 1 Harbor tasks through real n8n", "[oracle-x] exit 7 after 0m09s"])
        final = frame.index("after the end")
        self.assertEqual(
            frame[final + 1 :],
            [
                "✗ sapi-lab harbor failed after 0m01s · 1/2 stages done · failed: oracle",
                "  ✓ setup   0m01s",
                "  ✗ oracle  0m00s",
            ],
        )
        self.assertFalse(any(mark in "".join(frame) for mark in SPINNER))

    def test_narrow_and_short_terminals_never_wrap_or_overflow(self):
        stages = [f"case {n}/30 invoice-total/case-{n}" for n in range(1, 31)]
        tracker, stream, clock = tty_tracker(stages, size=(32, 12))
        for name in stages[:20]:
            with tracker.stage(name):
                clock.now += 1
        tracker.start(stages[20])
        frame = screen(stream.getvalue())
        self.assertLessEqual(len(frame), 12)
        self.assertTrue(all(len(line) < 32 for line in frame))
        self.assertIn("  ✓ 20 earlier stages done", frame)
        self.assertTrue(frame[-1].startswith("  · ") and frame[-1].endswith("pending"))

    def test_dumb_terminal_and_redirected_stderr_use_plain_lines(self):
        with patch.dict(os.environ, {"TERM": "dumb"}):
            dumb = Tracker("sapi-lab check", ["unittest"], stream=Terminal())
        self.assertFalse(dumb.interactive)
        redirected = Tracker("sapi-lab check", ["unittest"], stream=io.StringIO())
        self.assertFalse(redirected.interactive)
        with dumb.stage("unittest"):
            pass
        dumb.finish("passed")
        self.assertNotIn("\x1b", dumb.stream.getvalue())


class LifecycleTests(unittest.TestCase):
    def running_threads(self):
        return [thread for thread in threading.enumerate() if thread.name == "progress"]

    def test_one_display_owns_logged_subprocesses_without_duplicate_heartbeats(self):
        script = "import time; print('working', flush=True); time.sleep(.25)"
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory) / "child.log"
            for stream in (Terminal(), io.StringIO()):
                with self.subTest(interactive=stream.isatty()), contextlib.redirect_stdout(io.StringIO()) as out:
                    with tracking("sapi-lab demo", ["child"], stream=stream, refresh=0.02) as tracker:
                        with tracker.stage("child"):
                            code = run_logged(
                                [sys.executable, "-c", script], log, stage="child", timeout=None, heartbeat=0.1
                            )
                        tracker.finish("passed")
                    self.assertEqual(code, 0)
                    self.assertEqual(out.getvalue(), "")
                    text = stream.getvalue()
                    self.assertIsNone(LOGGED_OBSERVER.get())
                    self.assertEqual(self.running_threads(), [])
                    if stream.isatty():
                        self.assertNotIn("[child] ", text)
                        self.assertIn("last: working", text)
                        self.assertEqual(screen(text)[0].split(" after ")[0], "✓ sapi-lab demo passed")
                    else:
                        self.assertEqual(text.count("[child] started"), 1)
                        self.assertGreaterEqual(text.count("last: working"), 1)
                        self.assertNotIn("\x1b", text)

    def test_nested_tracking_reuses_the_outer_display(self):
        stream = io.StringIO()
        with tracking("sapi-lab generate", ["controls"], stream=stream, refresh=None) as outer:
            with tracking("sapi-lab evaluate", ["judge"], stream=Terminal(), refresh=None) as inner:
                self.assertIs(inner, outer)
                with progress.stage("controls"):
                    progress.note("inner note")
            self.assertIsNone(outer.outcome)
            outer.finish("passed")
        self.assertEqual(stream.getvalue().count("started · stages"), 1)
        self.assertIn("inner note", stream.getvalue())

    def test_exit_paths_finalize_once_and_restore_state(self):
        cases = (
            (None, "outcome unknown after"),
            (RuntimeError("boom"), "failed after"),
            (subprocess.TimeoutExpired("x", 1), "outcome unknown after"),
            (KeyboardInterrupt(), "interrupted after"),
        )
        for error, final in cases:
            with self.subTest(error=type(error).__name__):
                stream = Terminal()
                with contextlib.suppress(type(error) if error else ()):
                    with tracking("sapi-lab demo", ["work"], stream=stream, refresh=0.01):
                        with progress.stage("work"), progress.detail("Harbor job x"):
                            if error is not None:
                                raise error
                frame = screen(stream.getvalue())
                self.assertIn(final, frame[0])
                self.assertEqual(sum(final in line for line in frame), 1)
                self.assertFalse(any(mark in line for line in frame for mark in SPINNER))
                self.assertNotIn("Harbor job x", frame)
                self.assertIsNone(progress.ACTIVE.get())
                self.assertIsNone(LOGGED_OBSERVER.get())
                self.assertEqual(self.running_threads(), [])

    def test_module_helpers_fall_back_to_plain_stderr_without_a_display(self):
        with contextlib.redirect_stderr(io.StringIO()) as stderr:
            with progress.stage("anything"), progress.detail("nothing shown"):
                progress.note("plain line")
        self.assertEqual(stderr.getvalue(), "plain line\n")

    def test_failure_lines_name_log_jobs_tail_and_inspection(self):
        log, jobs = Path("/r/oracle.log"), Path("/r/jobs/oracle")
        self.assertEqual(
            progress.failure_lines(log, [str(n) for n in range(30)], jobs=jobs),
            ["  log: /r/oracle.log", "  jobs: /r/jobs/oracle", "  last lines:"]
            + [f"    {n}" for n in range(10, 30)]
            + ["  inspect: tail -n 200 /r/oracle.log"],
        )
        self.assertEqual(progress.failure_lines(None, []), [])


if __name__ == "__main__":
    unittest.main()
