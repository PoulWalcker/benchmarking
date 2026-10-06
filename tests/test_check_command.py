"""`sapi-lab check` runs the required local checks and names each one that failed."""

import contextlib
import io
import subprocess
import sys
import unittest
from unittest.mock import patch

from sapi_config_lab.coordinate.cli import CHECKS, main


def run_check(failing: set[str]) -> tuple[int, list[list[str]], str]:
    ran: list[list[str]] = []

    def fake(argv, **options):
        ran.append(argv)
        name = next(name for name, arguments in CHECKS.items() if argv[1:] == list(arguments))
        return subprocess.CompletedProcess(argv, 1 if name in failing else 0)

    stdout, stderr = io.StringIO(), io.StringIO()
    with patch("sapi_config_lab.coordinate.cli.subprocess.run", side_effect=fake):
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = main(["check"])
    assert stdout.getvalue() == ""
    return code, ran, stderr.getvalue()


class CheckCommandTests(unittest.TestCase):
    def test_every_required_check_runs_in_this_interpreter(self):
        code, ran, stderr = run_check(set())
        self.assertEqual(code, 0)
        self.assertEqual([argv[0] for argv in ran], [sys.executable] * 5)
        self.assertEqual(list(CHECKS), ["unittest", "ruff check", "ruff format", "mypy", "distribution"])
        self.assertIn("--check", ran[2])
        self.assertTrue(stderr.endswith("check passed\n"))

    def test_a_failed_stage_is_named_and_later_stages_still_run(self):
        code, ran, stderr = run_check({"ruff format", "mypy"})
        self.assertEqual(code, 1)
        self.assertEqual(len(ran), 5)
        self.assertIn("[3/5] ruff format: FAILED", stderr)
        self.assertIn("check failed: ruff format, mypy", stderr)


if __name__ == "__main__":
    unittest.main()
