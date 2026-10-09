"""Entrypoint composition selects trusted material without loading legacy execution."""

import contextlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from sapi_config_lab.coordinate import cli
from sapi_config_lab.paths import workspace_root


class CliCompositionTests(unittest.TestCase):
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
