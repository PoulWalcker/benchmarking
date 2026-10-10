"""`sapi-lab check` runs the required local checks and names each one that failed."""

import contextlib
import io
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from sapi_config_lab.coordinate.cli import CHECKS, main


def run_check(failing: set[str]) -> tuple[int, list[tuple[list[str], Path, dict]], str, Path]:
    ran: list[tuple[list[str], Path, dict]] = []

    def fake(argv, log, **options):
        ran.append((argv, log, options))
        name = next(name for name, arguments in CHECKS.items() if argv[1:] == list(arguments))
        log.write_text(f"{name} output\n" + ("error: reason\n" if name in failing else ""))
        return 2 if name == "mypy" and name in failing else 1 if name in failing else 0

    root = Path(tempfile.mkdtemp())
    stdout, stderr = io.StringIO(), io.StringIO()
    with (
        patch("sapi_config_lab.coordinate.cli.run_logged", side_effect=fake),
        patch("sapi_config_lab.coordinate.cli.workspace_root", return_value=root),
        contextlib.redirect_stdout(stdout),
        contextlib.redirect_stderr(stderr),
    ):
        code = main(["check"])
    assert stdout.getvalue() == ""
    return code, ran, stderr.getvalue(), root


class CheckCommandTests(unittest.TestCase):
    def test_every_required_check_runs_in_this_interpreter_through_one_display(self):
        code, ran, stderr, root = run_check(set())
        self.assertEqual(code, 0)
        self.assertEqual([argv[0] for argv, _, _ in ran], [sys.executable] * 5)
        self.assertEqual(list(CHECKS), ["unittest", "ruff check", "ruff format", "mypy", "distribution"])
        self.assertIn("--check", ran[2][0])
        logs = ran[0][1].parent
        self.assertEqual(logs.parent, root / "reports")
        self.assertEqual([log.name for _, log, _ in ran], [n.replace(" ", "-") + ".log" for n in CHECKS])
        self.assertEqual([options for _, _, options in ran], [{"stage": n, "timeout": None} for n in CHECKS])
        self.assertIn(
            "[sapi-lab check] started · stages: unittest → ruff check → ruff format → mypy → distribution", stderr
        )
        self.assertIn(f"logs: {logs}", stderr)
        self.assertIn("[sapi-lab check] 3/5 ruff format · done", stderr)
        self.assertRegex(stderr, r"\[sapi-lab check\] passed after \dm\d\ds · 5/5 stages done\n$")

    def test_a_failed_stage_is_named_with_its_log_and_later_stages_still_run(self):
        code, ran, stderr, _ = run_check({"ruff format", "mypy"})
        self.assertEqual(code, 1)
        self.assertEqual(len(ran), 5)
        log = ran[2][1]
        self.assertIn("[sapi-lab check] 3/5 ruff format · failed", stderr)
        self.assertIn(
            f"failed at ruff format: exit 1\n  log: {log}\n  last lines:\n    ruff format output\n    error: reason\n"
            f"  inspect: tail -n 200 {log}\n",
            stderr,
        )
        self.assertIn("failed at mypy: exit 2\n", stderr)
        self.assertIn("[sapi-lab check] 5/5 distribution · done", stderr)
        self.assertRegex(stderr, r"failed after \dm\d\ds · 3/5 stages done · failed: ruff format, mypy\n$")


if __name__ == "__main__":
    unittest.main()
