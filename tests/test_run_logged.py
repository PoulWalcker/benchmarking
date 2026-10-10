"""Logged subprocess progress and cleanup."""

import _thread
import contextlib
import io
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest

from sapi_config_lab.execute.host import run_logged


class RunLoggedTests(unittest.TestCase):
    def test_reports_start_heartbeat_and_exit_to_stderr_only(self):
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory) / "child.log"
            stderr = io.StringIO()
            stdout = io.StringIO()
            with contextlib.redirect_stderr(stderr), contextlib.redirect_stdout(stdout):
                code = run_logged(
                    [sys.executable, "-c", "import time; print('working', flush=True); time.sleep(.13)"],
                    log,
                    stage="sample",
                    timeout=None,
                    heartbeat=0.05,
                )
            self.assertEqual(code, 0)
            self.assertEqual(stdout.getvalue(), "")
            self.assertEqual(log.read_text(), "working\n")
            self.assertIn(f"[sample] started · log {log.resolve()}", stderr.getvalue())
            self.assertIn(f"· log {log.resolve()} · last: working", stderr.getvalue())
            self.assertIn("quiet <1s", stderr.getvalue())
            self.assertIn("[sample] exit 0 after", stderr.getvalue())

    def test_last_line_handles_carriage_returns_ansi_invalid_utf8_and_long_logs(self):
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory) / "child.log"
            stderr = io.StringIO()
            script = (
                "import os,time; "
                "os.write(1,b'x'*9000+b'\\r\\x1b[32mstep one\\x1b[0m\\rfinal \\xff\\n'); "
                "time.sleep(.12)"
            )
            with contextlib.redirect_stderr(stderr):
                run_logged([sys.executable, "-c", script], log, stage="progress", timeout=None, heartbeat=0.05)
            self.assertIn("last: final �", stderr.getvalue())
            self.assertNotIn("x" * 160, stderr.getvalue())

    def test_empty_log_and_timeout_leave_no_live_child(self):
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory) / "child.log"
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr), self.assertRaises(subprocess.TimeoutExpired):
                run_logged(
                    [
                        sys.executable,
                        "-c",
                        "import os,time; time.sleep(.08); os.write(1,str(os.getpid()).encode()); time.sleep(10)",
                    ],
                    log,
                    stage="slow",
                    timeout=0.2,
                    heartbeat=0.05,
                )
            self.assertIn("last: (no output yet)", stderr.getvalue())
            self.assertIn("quiet <1s", stderr.getvalue())
            self.assertIn("[slow] timed out after", stderr.getvalue())
            self.assertIn("outcome unknown", stderr.getvalue())
            with self.assertRaises(ProcessLookupError):
                os.kill(int(log.read_text()), 0)

    def test_interrupt_kills_and_reaps_child(self):
        for ignore in (False, True):
            with self.subTest(ignore=ignore), tempfile.TemporaryDirectory() as directory:
                log = Path(directory) / "child.log"
                script = "import os,signal,time; "
                if ignore:
                    script += "signal.signal(signal.SIGINT,signal.SIG_IGN); "
                script += "print(os.getpid(),flush=True); time.sleep(10)"
                timer = threading.Timer(0.15, _thread.interrupt_main)
                timer.start()
                try:
                    with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(KeyboardInterrupt):
                        run_logged([sys.executable, "-c", script], log, stage="interrupt", timeout=None, heartbeat=0.05)
                finally:
                    timer.cancel()
                    timer.join()
                with self.assertRaises(ProcessLookupError):
                    os.kill(int(log.read_text()), 0)
