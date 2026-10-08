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
from sapi_config_lab.coordinate.benchmark_discovery import select_benchmarks
from sapi_config_lab.coordinate.packages import stage_tasks
from sapi_config_lab.paths import workspace_root


class CliCompositionTests(unittest.TestCase):
    def test_native_cli_packages_match_existing_control_packages(self):
        with tempfile.TemporaryDirectory() as temporary, contextlib.redirect_stdout(io.StringIO()):
            base = Path(temporary)
            for scenario, mode, catalog in (
                ("invoice-total", "oracle", "full"),
                ("checkout-recovery", "oracle", "full"),
                ("invoice-total", "generation", "full"),
                ("invoice-total", "generation", "scenario"),
                ("checkout-recovery", "generation", "full"),
            ):
                suffix = f"{scenario}-{mode}-{catalog}"
                existing, composed = base / (suffix + "-old"), base / suffix
                stage_tasks(
                    existing,
                    mode=mode,
                    root=workspace_root(),
                    benchmarks=select_benchmarks(workspace_root() / "benchmarks", (scenario,)),
                    catalog=catalog,
                )
                cli.main(["package-tasks", str(composed), "--scenario", scenario, "--mode", mode, "--catalog", catalog])

                def inventory(directory):
                    return {
                        path.relative_to(directory).as_posix(): path.read_bytes()
                        for path in directory.rglob("*")
                        if path.is_file()
                    }

                self.assertEqual(inventory(composed), inventory(existing), suffix)

    def test_installed_console_can_plan_and_stage_independent_verification(self):
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run(
                [
                    str(Path(sys.executable).parent / "sapi-lab"),
                    "package-tasks",
                    directory + "/tasks",
                    "--scenario",
                    "invoice-total",
                ],
                cwd=workspace_root(),
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue((Path(directory) / "tasks/invoice-total/tests/benchmark.json").is_file())

    def test_selected_commands_do_not_import_legacy_scenario_registry(self):
        script = """
import contextlib, io, sys, tempfile
from pathlib import Path
from unittest.mock import patch
from sapi_config_lab.coordinate import cli, ui
from sapi_config_lab.coordinate.evaluation import recorded_benchmark
from sapi_config_lab.paths import workspace_root
original = __import__
def guarded(name, *args, **kwargs):
    if name == "sapi_config_lab.coordinate.scenarios":
        raise AssertionError("legacy registry imported")
    return original(name, *args, **kwargs)
with patch("builtins.__import__", side_effect=guarded), tempfile.TemporaryDirectory() as tmp, contextlib.redirect_stdout(io.StringIO()):
    root = workspace_root()
    config = root / "benchmarks/01-invoice-total/config.yaml"
    cli.main(["compile", str(config), "--output", tmp + "/invoice.json"])
    cli.main(["package-tasks", tmp + "/tasks", "--scenario", "invoice-total", "--scenario", "checkout-recovery"])
    ui.prepare(config, Path(tmp) / "ui")
    assert recorded_benchmark("checkout-recovery").name == "checkout-recovery"
assert "sapi_config_lab.coordinate.scenarios" not in sys.modules
"""
        result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_oracle_rejects_generation_catalog_arm_before_staging(self):
        with tempfile.TemporaryDirectory() as temporary, contextlib.redirect_stderr(io.StringIO()) as errors:
            for scenario in ("invoice-total", "checkout-recovery"):
                destination = Path(temporary) / scenario
                self.assertEqual(
                    cli.main(["package-tasks", str(destination), "--scenario", scenario, "--catalog", "scenario"]), 2
                )
                self.assertFalse(destination.exists())
            self.assertIn("A catalog variant changes generation prompts only", errors.getvalue())

    def test_historical_detached_compile_needs_no_experiment_workspace(self):
        with tempfile.TemporaryDirectory() as temporary, contextlib.redirect_stdout(io.StringIO()):
            source = Path(temporary) / "candidate.yaml"
            source.write_bytes((workspace_root() / "benchmarks/01-invoice-total/config.yaml").read_bytes())
            with patch(
                "sapi_config_lab.coordinate.compilation.workspace_root", side_effect=RuntimeError("no workspace")
            ):
                self.assertEqual(
                    cli.main(
                        [
                            "compile",
                            str(source),
                            "--bindings",
                            str(workspace_root() / "benchmarks/01-invoice-total/bindings.yaml"),
                            "--operations",
                            str(workspace_root() / "benchmarks/01-invoice-total/operations.js"),
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

    def test_detached_yaml_can_select_descriptor_but_not_executable_paths(self):
        with tempfile.TemporaryDirectory() as temporary, contextlib.redirect_stdout(io.StringIO()):
            base = Path(temporary)
            source = base / "candidate.yaml"
            source.write_bytes((workspace_root() / "benchmarks/01-invoice-total/config.yaml").read_bytes())
            with patch(
                "sapi_config_lab.coordinate.compilation.discover_benchmarks",
                wraps=__import__("sapi_config_lab.benchmark", fromlist=["discover_benchmarks"]).discover_benchmarks,
            ):
                self.assertEqual(
                    cli.main(
                        ["compile", str(source), "--scenario", "invoice-total", "--output", str(base / "out.json")]
                    ),
                    0,
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
