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
from sapi_config_lab.execute.host import LOGGED_OBSERVER, LoggedEvent, duration, run_logged
from sapi_config_lab.paths import display_path


class FakeClock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


def line_tracker(stages=("setup", "work", "finalizing"), **options):
    stream, clock = io.StringIO(), FakeClock()
    tracker = Tracker("sapi-lab demo", stages, stream=stream, clock=clock, interactive=False, **options)
    return tracker, stream, clock


class DisplayFormatTests(unittest.TestCase):
    def test_durations_read_naturally_at_every_scale(self):
        cases = {-1: "<1s", 0: "<1s", 0.99: "<1s", 1: "1s", 7.9: "7s", 59.99: "59s", 60: "1m00s", 112: "1m52s"}
        for seconds, text in cases.items():
            with self.subTest(seconds=seconds):
                self.assertEqual(duration(seconds), text)
        self.assertEqual(duration(3725), "62m05s")

    def test_paths_are_shown_relative_to_the_working_directory_only_when_inside_it(self):
        with tempfile.TemporaryDirectory() as directory:
            cwd = Path(directory).resolve()
            with patch("pathlib.Path.cwd", return_value=cwd):
                self.assertEqual(display_path(cwd / "reports/r/local-tests.log"), Path("reports/r/local-tests.log"))
                self.assertEqual(display_path(Path("/elsewhere/r.log")), Path("/elsewhere/r.log"))
                log = cwd / "reports/r/oracle.log"
                self.assertEqual(
                    progress.failure_lines(log, ["boom"], jobs=cwd / "reports/r/jobs/oracle"),
                    [
                        "  log: reports/r/oracle.log",
                        "  jobs: reports/r/jobs/oracle",
                        "  last lines:",
                        "    boom",
                        "  inspect: tail -n 200 reports/r/oracle.log",
                    ],
                )
            self.assertEqual(log.parent, cwd / "reports/r")


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
                "[sapi-lab demo] 1/3 setup · done 3s",
                "[sapi-lab demo] 2/3 work · running",
                "[sapi-lab demo] 2/3 work · done 1m02s",
                "[sapi-lab demo] 3/3 finalizing · running",
                "[sapi-lab demo] 3/3 finalizing · done 1s",
                "[sapi-lab demo] PASSED · 1m06s · 3/3 stages done",
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
                self.assertIn(f"[sapi-lab demo] 2/3 work · {state} <1s", stream.getvalue())

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
            "[sapi-lab demo] FAILED · 1s · 1/3 stages done · Outcome unknown: work · Not run: finalizing",
        )
        self.assertEqual(sum("FAILED · " in line for line in lines), 1)
        with self.assertRaises(ValueError):
            tracker.end("setup", "passed")

    def test_unknown_outcome_and_rejected_result_are_distinct_from_failure(self):
        tracker, stream, _ = line_tracker(("work",))
        with self.assertRaises(RuntimeError), tracker.stage("work"):
            raise RuntimeError("wrapper completion is missing")
        tracker.unknown_outcome()
        tracker.finish("unknown")
        self.assertTrue(stream.getvalue().endswith("OUTCOME UNKNOWN · <1s · 0/1 stages done · Outcome unknown: work\n"))
        tracker, stream, _ = line_tracker(("work",))
        with tracker.stage("work"):
            pass
        tracker.finish("failed")
        self.assertTrue(stream.getvalue().endswith("FAILED · <1s · 1/1 stages done · Result not accepted\n"))

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


HARBOR = ("setup", "local tests", "task plans", "transport", "native images", "oracle", "nop", "finalizing")


def tty_tracker(stages=("setup", "transport", "oracle", "finalizing"), size=(100, 40)):
    stream, clock = Terminal(), FakeClock()
    tracker = Tracker("sapi-lab harbor", stages, stream=stream, clock=clock, size=lambda: size)
    return tracker, stream, clock


def nested(stream, name):
    """The running stage row named `name` and the context lines nested under it."""
    frame = screen(stream.getvalue())
    at = next(i for i, line in enumerate(frame) if line[4:].startswith(name + " ") and line[2] in SPINNER)
    return frame[at], [line for line in frame[at + 1 :] if line.startswith("      ")]


class TerminalModeTests(unittest.TestCase):
    def test_active_harbor_frame_matches_the_target_layout(self):
        with tempfile.TemporaryDirectory() as directory:
            cwd = Path(directory).resolve()
            log = cwd / "reports/20261010T164637Z/transport.log"
            with patch("pathlib.Path.cwd", return_value=cwd):
                tracker, stream, clock = tty_tracker(HARBOR)
                for name, seconds in (("setup", 0.3), ("local tests", 113), ("task plans", 0.2)):
                    with tracker.stage(name):
                        clock.now += seconds
                tracker.start("transport")
                with tracker.detail("Harbor job: transport · task invoice-total"):
                    clock.now += 1
                    tracker.observe(LoggedEvent("started", "transport", log, 0, "ignored on a terminal"))
                    clock.now += 15
                    tracker.observe(LoggedEvent("heartbeat", "transport", log, 15, "ignored", "(no output yet)", 15))
                    clock.now += 12
                    tracker.refresh()
                    frame = screen(stream.getvalue())
        spin = SPINNER[1]
        self.assertEqual(
            frame,
            [
                f"{spin} sapi-lab harbor · 2m21s · stage 4/8",
                "",
                "  ✓ setup           <1s",
                "  ✓ local tests     1m53s",
                "  ✓ task plans      <1s",
                f"  {spin} transport       28s",
                "      Harbor job: transport · task invoice-total",
                "      Last output: (no output yet)",
                "      Quiet: 27s",
                "      Log: reports/20261010T164637Z/transport.log",
                "",
                "  · native images",
                "  · oracle",
                "  · nop",
                "  · finalizing",
            ],
        )
        self.assertNotIn("ignored", stream.getvalue())

    def test_final_success_frame_matches_the_target_layout(self):
        tracker, stream, clock = tty_tracker(HARBOR)
        timings = (0.3, 113, 0.2, 124, 35, 72, 12, 0.5)
        for name, seconds in zip(HARBOR, timings, strict=True):
            with tracker.stage(name):
                clock.now += seconds
        tracker.finish("passed")
        self.assertEqual(
            screen(stream.getvalue()),
            [
                "✓ sapi-lab harbor · PASSED · 5m57s · 8/8 stages done",
                "",
                "  ✓ setup           <1s",
                "  ✓ local tests     1m53s",
                "  ✓ task plans      <1s",
                "  ✓ transport       2m04s",
                "  ✓ native images   35s",
                "  ✓ oracle          1m12s",
                "  ✓ nop             12s",
                "  ✓ finalizing      <1s",
            ],
        )

    def test_a_just_started_subprocess_adds_only_its_log(self):
        tracker, stream, clock = tty_tracker(("local tests", "mypy"))
        log = Path("/runs/r/local-tests.log")
        tracker.start("local tests")
        clock.now += 1
        tracker.observe(LoggedEvent("started", "local tests", log, 0, "ignored"))
        clock.now += 6
        tracker.refresh()
        row, lines = nested(stream, "local tests")
        self.assertEqual(row, f"  {SPINNER[1]} local tests   7s")
        self.assertEqual(lines, ["      Log: /runs/r/local-tests.log"])

    def test_stage_timer_is_the_only_timer_and_quiet_stays_current_between_heartbeats(self):
        # The reported case: the subprocess starts a second after its stage and stays silent.
        tracker, stream, clock = tty_tracker(("setup", "transport"))
        log = Path("/runs/r/transport.log")
        tracker.start("transport")
        clock.now += 1
        tracker.observe(LoggedEvent("started", "transport", log, 0, "ignored"))
        clock.now += 15
        tracker.observe(LoggedEvent("heartbeat", "transport", log, 15, "ignored", "(no output yet)", 15))
        for second, quiet in ((16, "15s"), (21, "20s"), (28, "27s")):
            clock.now = 100 + second
            tracker.refresh()
            row, lines = nested(stream, "transport")
            self.assertEqual(row.split()[-1], f"{second}s")
            self.assertEqual(
                lines,
                ["      Last output: (no output yet)", f"      Quiet: {quiet}", "      Log: /runs/r/transport.log"],
            )
        clock.now += 2
        tracker.observe(LoggedEvent("heartbeat", "transport", log, 30, "ignored", "listening", None))
        clock.now += 3
        tracker.refresh()
        self.assertEqual(
            nested(stream, "transport")[1], ["      Last output: listening", "      Log: /runs/r/transport.log"]
        )

    def test_a_subprocess_started_well_into_its_stage_shows_its_own_labelled_timer(self):
        tracker, stream, clock = tty_tracker(("setup", "transport"))
        log = Path("/runs/r/transport.log")
        tracker.start("transport")
        clock.now += 30  # Stage work before the subprocess exists.
        tracker.observe(LoggedEvent("started", "transport", log, 0, "ignored"))
        for second in (15, 30, 45, 60, 75):
            clock.now += 15
            tracker.observe(LoggedEvent("heartbeat", "transport", log, second, "ignored", "(no output yet)", second))
        clock.now += 7
        tracker.refresh()
        row, lines = nested(stream, "transport")
        self.assertEqual(row, f"  {SPINNER[1]} transport   1m52s")
        self.assertEqual(
            lines,
            [
                "      Subprocess: running 1m22s",
                "      Last output: (no output yet)",
                "      Quiet: 1m22s",
                "      Log: /runs/r/transport.log",
            ],
        )
        self.assertTrue(all(word not in stream.getvalue() for word in ("building", "verifying", "ETA", "%")))

    def test_a_differently_named_job_is_named_once(self):
        log = Path("/runs/r/oracle-invoice-total.log")
        for details, expected in (
            ((), ["      Subprocess: oracle-invoice-total"]),
            (("Harbor job: oracle-invoice-total · task invoice-total",), []),
        ):
            with self.subTest(details=details):
                tracker, stream, clock = tty_tracker(("setup", "oracle"))
                tracker.start("oracle")
                tracker.details.extend(details)
                tracker.observe(LoggedEvent("started", "oracle-invoice-total", log, 0, "ignored"))
                clock.now += 4
                tracker.refresh()
                _, lines = nested(stream, "oracle")
                self.assertEqual(
                    lines,
                    [f"      {text}" for text in details] + expected + ["      Log: /runs/r/oracle-invoice-total.log"],
                )

    def test_final_summary_leads_with_status_and_wraps_instead_of_truncating(self):
        stages = ("unittest", "ruff check", "ruff format", "mypy", "distribution")
        for width in (100, 40, 24):
            with self.subTest(width=width):
                tracker, stream, clock = tty_tracker(stages, size=(width, 40))
                with tracker.stage("unittest"):
                    clock.now += 5
                tracker.start("ruff check")
                clock.now += 2
                tracker.finish("interrupted")
                frame = screen(stream.getvalue())
                self.assertTrue(all(len(line) < width for line in frame))
                self.assertNotIn("…", "".join(frame))
                summary = frame[: frame.index("")]
                self.assertTrue(summary[0].startswith("! sapi-lab harbor"))
                text = " ".join(line.strip() for line in summary)
                for fact in ("INTERRUPTED", "7s", "1/5 stages done", "Interrupted: ruff check"):
                    self.assertIn(fact, text)
                self.assertIn("Not run: ruff format, mypy, distribution", text)
                if width == 100:
                    self.assertEqual(
                        summary,
                        [
                            "! sapi-lab harbor · INTERRUPTED · 7s · 1/5 stages done",
                            "  Interrupted: ruff check",
                            "  Not run: ruff format, mypy, distribution",
                        ],
                    )
                if width == 24:
                    self.assertEqual(
                        summary,
                        [
                            "! sapi-lab harbor",
                            "    INTERRUPTED · 7s",
                            "    1/5 stages done",
                            "  Interrupted: ruff",
                            "    check",
                            "  Not run: ruff format,",
                            "    mypy, distribution",
                        ],
                    )

    def test_final_summary_for_each_outcome_reads_alike(self):
        cases = (
            ("passed", None, ["✓ sapi-lab harbor · PASSED · 1s · 1/1 stages done"]),
            ("failed", RuntimeError(), ["✗ sapi-lab harbor · FAILED · 1s · 0/1 stages done", "  Failed: work"]),
            (
                "unknown",
                subprocess.TimeoutExpired("x", 1),
                ["? sapi-lab harbor · OUTCOME UNKNOWN · 1s · 0/1 stages done", "  Outcome unknown: work"],
            ),
            (
                "interrupted",
                KeyboardInterrupt(),
                ["! sapi-lab harbor · INTERRUPTED · 1s · 0/1 stages done", "  Interrupted: work"],
            ),
        )
        for outcome, error, expected in cases:
            with self.subTest(outcome=outcome):
                tracker, stream, clock = tty_tracker(("work",))
                with contextlib.suppress(type(error) if error else ()), tracker.stage("work"):
                    clock.now += 1
                    if error is not None:
                        raise error
                tracker.finish(outcome)
                mark = expected[0][0]
                self.assertEqual(screen(stream.getvalue()), [*expected, "", f"  {mark} work   1s"])

    def test_notes_stay_above_the_frame_and_final_frame_has_no_spinner(self):
        tracker, stream, clock = tty_tracker(("setup", "oracle"))
        with tracker.stage("setup"):
            clock.now += 1
        tracker.start("oracle")
        tracker.note("oracle: 1 Harbor tasks through real n8n")
        log = Path("/runs/r/oracle.log")
        tracker.observe(LoggedEvent("exited", "oracle-x", log, 9, "[oracle-x] exit 7 after 9s", code=7))
        with self.assertRaises(RuntimeError), tracker.stage("oracle"):
            raise RuntimeError("Control check failed")
        tracker.finish("failed")
        tracker.refresh()
        tracker.note("after the end")
        frame = screen(stream.getvalue())
        self.assertEqual(frame[:2], ["oracle: 1 Harbor tasks through real n8n", "[oracle-x] exit 7 after 9s"])
        final = frame.index("after the end")
        self.assertEqual(
            frame[final + 1 :],
            [
                "✗ sapi-lab harbor · FAILED · 1s · 1/2 stages done",
                "  Failed: oracle",
                "",
                "  ✓ setup    1s",
                "  ✗ oracle   <1s",
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

    def test_a_terminal_reporting_zero_size_falls_back_to_a_usable_width(self):
        stream = Terminal()
        stream.fileno = lambda: 2  # A pseudo-terminal can report 0x0, e.g. under script(1)
        with patch("sapi_config_lab.coordinate.progress.os.get_terminal_size", return_value=os.terminal_size((0, 0))):
            self.assertEqual(progress.terminal_size(stream), (80, 24))
            tracker = Tracker("sapi-lab harbor", ["local tests"], stream=stream, clock=FakeClock())
            tracker.start("local tests")
        self.assertEqual(screen(stream.getvalue())[2], "  ⠋ local tests   <1s")

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
                        self.assertIn("Last output: working", text)
                        self.assertTrue(screen(text)[0].startswith("✓ sapi-lab demo · PASSED · "))
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
            (None, "OUTCOME UNKNOWN · "),
            (RuntimeError("boom"), "FAILED · "),
            (subprocess.TimeoutExpired("x", 1), "OUTCOME UNKNOWN · "),
            (KeyboardInterrupt(), "INTERRUPTED · "),
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
