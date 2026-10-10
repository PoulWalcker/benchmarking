"""Docker readiness and image identity diagnostics with no real Docker calls."""

import contextlib
import io
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from sapi_config_lab.execute import host


class DockerHostTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.bin = Path(directory.name)
        environ = patch.dict(os.environ, {"PATH": str(self.bin)})
        environ.start()
        self.addCleanup(environ.stop)

    def docker(self, script):
        executable = self.bin / "docker"
        executable.write_text(f"#!{sys.executable} -S\n{script}\n")
        executable.chmod(0o755)

    def test_missing_cli_names_the_preflight_problem(self):
        with self.assertRaisesRegex(RuntimeError, "^docker CLI not found on PATH$"):
            host.docker_preflight()

    def test_dead_daemon_names_context_and_preserves_original_error(self):
        self.docker(
            "import sys\n"
            "if sys.argv[1:3] == ['context', 'show']:\n"
            "    print('dead-context')\n"
            "else:\n"
            "    sys.stderr.write('Cannot connect to socket\\nmore detail\\n')\n"
            "    sys.exit(1)"
        )
        with self.assertRaisesRegex(
            RuntimeError, "^Docker daemon not reachable \\(context dead-context\\): Cannot connect to socket$"
        ) as raised:
            host.docker_preflight()
        self.assertEqual(raised.exception.__cause__.stderr, "Cannot connect to socket\nmore detail\n")

    def test_slow_daemon_is_a_preflight_failure_and_child_is_reaped(self):
        pid_file = self.bin / "pid"
        self.docker(
            "import os,sys,time\n"
            "if sys.argv[1:3] == ['context', 'show']:\n"
            "    print('slow-context')\n"
            "else:\n"
            f"    open({str(pid_file)!r}, 'w').write(str(os.getpid()))\n"
            "    time.sleep(10)"
        )
        with self.assertRaisesRegex(
            RuntimeError, "^Docker daemon did not answer within 1s \\(context slow-context\\)$"
        ) as raised:
            host.docker_preflight(timeout=1)
        self.assertIsInstance(raised.exception.__cause__, subprocess.TimeoutExpired)
        with self.assertRaises(ProcessLookupError):
            os.kill(int(pid_file.read_text()), 0)

    def test_success_records_server_and_best_effort_context(self):
        for context in ("desktop", "failed", "slow"):
            with self.subTest(context=context):
                self.docker(
                    "import sys,time\n"
                    "if sys.argv[1:3] == ['context', 'show']:\n"
                    + (
                        "    sys.stderr.write('context detail\\n'); sys.exit(1)\n"
                        if context == "failed"
                        else "    time.sleep(10)\n"
                        if context == "slow"
                        else "    print('desktop')\n"
                    )
                    + "else:\n    print('29.4.0')"
                )
                self.assertEqual(
                    host.docker_preflight(timeout=1),
                    {"server_version": "29.4.0", "context": "desktop" if context == "desktop" else "unknown"},
                )

    def test_missing_image_names_tag_and_rebuild_action(self):
        for diagnostic in ("No such image", "No such object"):
            with self.subTest(diagnostic=diagnostic):
                self.docker(
                    f"import sys\nsys.stderr.write('Error response from daemon: {diagnostic}: absent:tag\\n')\n"
                    "sys.exit(1)"
                )
                with self.assertRaisesRegex(
                    RuntimeError, "^Docker image absent:tag is missing; rebuild without --skip-build$"
                ) as raised:
                    host.image_id("absent:tag")
                self.assertIsInstance(raised.exception.__cause__, subprocess.CalledProcessError)
                self.assertIn(diagnostic, raised.exception.__cause__.stderr)

    def test_other_inspect_failures_keep_type_exit_code_and_diagnostic(self):
        self.docker("import sys\nsys.stderr.write('permission denied\\n')\nsys.exit(7)")
        with (
            contextlib.redirect_stderr(io.StringIO()) as stderr,
            self.assertRaises(subprocess.CalledProcessError) as raised,
        ):
            host.image_id("present:tag")
        self.assertEqual(raised.exception.returncode, 7)
        self.assertEqual(raised.exception.stderr, "permission denied\n")
        self.assertEqual(stderr.getvalue(), "permission denied\n")

    def test_existing_image_returns_its_content_identity(self):
        self.docker("print('sha256:fixed')")
        self.assertEqual(host.image_id("present:tag"), "sha256:fixed")
