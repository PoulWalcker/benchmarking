"""Entrypoint composition selects trusted material without loading legacy execution."""

import contextlib
import io
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from sapi_config_lab.coordinate import cli
from sapi_config_lab.paths import workspace_root


class CliCompositionTests(unittest.TestCase):
    def test_sigint_exits_130_without_traceback_after_reporting_and_reaping_child(self):
        script = """
import sys
from unittest.mock import patch
from sapi_config_lab.coordinate import cli, controls
from sapi_config_lab.execute.host import run_logged
def child(command, log, **kwargs):
    return run_logged(
        [sys.executable, '-c',
         'import os,signal,time; signal.signal(signal.SIGINT,signal.SIG_IGN); '
         'print(os.getpid(),flush=True); time.sleep(30)'], log, **kwargs)
with (
    patch('sapi_config_lab.coordinate.runs.docker_preflight',
          return_value={'server_version': '29.4.0', 'context': 'test'}),
    patch('sapi_config_lab.coordinate.runs.running_containers', return_value=''),
    patch('sapi_config_lab.coordinate.runs.checked_harbor', return_value=(['harbor'], '0.21.0')),
    patch.object(controls, 'run_logged', side_effect=child),
):
    raise SystemExit(cli.main(['harbor', '--report-dir', sys.argv[1]]))
"""
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "run"
            log = output / "local-tests.log"
            process = subprocess.Popen(
                [sys.executable, "-c", script, str(output)], stdout=subprocess.PIPE, stderr=subprocess.PIPE
            )
            try:
                deadline = time.monotonic() + 10
                while not log.exists() or not log.read_text().strip():
                    self.assertIsNone(process.poll(), "CLI exited before starting the child")
                    self.assertLess(time.monotonic(), deadline, "child did not become ready")
                    time.sleep(0.01)
                child_pid = int(log.read_text().strip())
                process.send_signal(signal.SIGINT)
                stdout, stderr = process.communicate(timeout=10)
            finally:
                if process.poll() is None:
                    process.kill()
                    process.communicate()
            with self.assertRaises(ProcessLookupError):
                os.kill(child_pid, 0)
            self.assertEqual(process.returncode, 130, stderr.decode())
            self.assertNotIn(b"Traceback", stderr)
            self.assertIn(b"interrupted at local tests", stderr)
            self.assertEqual(stdout, b"")
            report = json.loads((output / "report.json").read_text())
            self.assertEqual(report["status"], "interrupted")
            self.assertEqual(report["interrupted_stage"], "local tests")
            self.assertEqual(report["logs"], {"local-tests": "local-tests.log"})
            for field in ("failure_stage", "error", "failure_category", "log_tail"):
                self.assertNotIn(field, report)

    def test_native_listing_and_compilation_never_import_descriptors(self):
        script = """
import contextlib, io, sys, tempfile
from unittest.mock import patch
from sapi_config_lab.coordinate import cli
from sapi_config_lab.paths import workspace_root
original = __import__
def guarded(name, *args, **kwargs):
    if name in {"sapi_config_lab.benchmark", "sapi_config_lab.benchmark_loading"}:
        raise AssertionError("descriptor imported")
    return original(name, *args, **kwargs)
with patch("builtins.__import__", side_effect=guarded), tempfile.TemporaryDirectory() as tmp, contextlib.redirect_stdout(io.StringIO()):
    root = workspace_root()
    assert cli.main(["benchmarks"]) == 0
    assert cli.main(["compile", str(root / "tasks/invoice-total/solution/config.yaml"), "--output", tmp + "/invoice.json"]) == 0
    assert cli.main(["build", "--output-dir", tmp + "/all"]) == 0
"""
        result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_historical_detached_compile_needs_no_experiment_workspace(self):
        with tempfile.TemporaryDirectory() as temporary, contextlib.redirect_stdout(io.StringIO()):
            source = Path(temporary) / "candidate.yaml"
            source.write_bytes((workspace_root() / "tasks/invoice-total/solution/config.yaml").read_bytes())
            with patch(
                "sapi_config_lab.coordinate.compilation.benchmark_root", side_effect=RuntimeError("no resources")
            ):
                self.assertEqual(
                    cli.main(
                        [
                            "compile",
                            str(source),
                            "--bindings",
                            str(workspace_root() / "tasks/invoice-total/bindings.yaml"),
                            "--operations",
                            str(workspace_root() / "tasks/invoice-total/operations.js"),
                            "--output",
                            temporary + "/out.json",
                        ]
                    ),
                    0,
                )
                with contextlib.redirect_stderr(io.StringIO()):
                    self.assertEqual(
                        cli.main(
                            ["compile", str(source), "--scenario", "invoice-total", "--output", temporary + "/bad.json"]
                        ),
                        2,
                    )
            self.assertTrue((Path(temporary) / "out.json").is_file())
            self.assertFalse((Path(temporary) / "bad.json").exists())

    def test_detached_yaml_can_select_native_task_but_not_executable_paths(self):
        with tempfile.TemporaryDirectory() as temporary, contextlib.redirect_stdout(io.StringIO()):
            base = Path(temporary)
            source = base / "candidate.yaml"
            source.write_bytes((workspace_root() / "tasks/invoice-total/solution/config.yaml").read_bytes())
            self.assertEqual(
                cli.main(["compile", str(source), "--scenario", "invoice-total", "--output", str(base / "out.json")]), 0
            )
            self.assertFalse(json.loads((base / "out.json").read_text())["active"])
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(
                    cli.main(
                        ["compile", str(source), "--scenario", "../candidate.py", "--output", str(base / "bad.json")]
                    ),
                    2,
                )
            self.assertFalse((base / "bad.json").exists())
