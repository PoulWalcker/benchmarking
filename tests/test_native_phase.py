"""Native task admission preserves phase ceilings and zero-model control execution."""

import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from sapi_config_lab.coordinate.live import cohort_grants
from sapi_config_lab.coordinate.native_tasks import invoke
from sapi_config_lab.coordinate.provenance import source_manifest
from sapi_config_lab.paths import workspace_root
from sapi_config_lab.profile import read

ROOT = workspace_root()


class NativePhaseTests(unittest.TestCase):
    def test_actual_task_plans_preserve_sequential_phase_admission(self):
        task = ROOT / "tasks/invoice-total"
        with tempfile.TemporaryDirectory() as directory:
            submission = Path(directory) / "config.yaml"
            for deadline in (120, 300):
                config = read(task / "solution/config.yaml")
                config["execution"]["deadline_seconds"] = deadline
                submission.write_text(json.dumps(config))
                request = {
                    "action": "plan",
                    "submission": str(submission),
                    "options": {"mode": "stub", "deadline_seconds": deadline},
                }
                if deadline == 120:
                    self.assertEqual(len(invoke(task, request, source_manifest())["plan"]["entries"]), 14)
                else:
                    with self.assertRaises(subprocess.CalledProcessError) as error:
                        invoke(task, request, source_manifest())
                    self.assertIn("exceed the native verifier phase", error.exception.stderr)
                    with self.assertRaises(subprocess.CalledProcessError):
                        cohort_grants({task.name: {"path": submission}}, {task.name: task})

    def test_expanded_fixture_cohort_must_fit_before_live_admission(self):
        task = ROOT / "tasks/invoice-total"
        cases = json.loads((task / "cases.json").read_text())
        cases["positive"].extend({**cases["positive"][0], "name": "extra-" + str(index)} for index in range(20))
        with self.assertRaises(subprocess.CalledProcessError) as error:
            cohort_grants({task.name: {"path": task / "solution/config.yaml", "cases": cases}}, {task.name: task})
        self.assertIn("exceed the native verifier phase", error.exception.stderr)

    def test_prompt_phase_refuses_before_authoring_when_native_limit_is_reduced(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            shutil.copytree(ROOT / "generation", root / "generation")
            for name in ("invoice-total", "checkout-recovery"):
                task = root / "tasks" / name
                shutil.copytree(ROOT / "tasks" / name, task, ignore=shutil.ignore_patterns("__pycache__"))
                toml = task / "task.toml"
                text = toml.read_text()
                text = text.replace("timeout_sec = 6000", "timeout_sec = 100").replace(
                    "timeout_sec = 1800", "timeout_sec = 100"
                )
                toml.write_text(text)
                sources = source_manifest(root)
                with (
                    patch("sapi_config_lab.coordinate.native_tasks.resource_root", return_value=root),
                    patch("sapi_config_lab.coordinate.native_tasks.source_manifest", return_value=sources),
                ):
                    with self.assertRaises(subprocess.CalledProcessError) as error:
                        invoke(task, {"action": "prompt", "catalog": "full"}, sources)
                self.assertIn("exceed the native verifier phase", error.exception.stderr)

    def test_actual_checkout_main_compiles_zero_model_control_without_a_bridge(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            shutil.copytree(
                ROOT / "tasks/checkout-recovery", root / "payload", ignore=shutil.ignore_patterns("__pycache__")
            )
            script = r"""
import json, os, runpy, sys
from pathlib import Path
from unittest.mock import patch
sys.path[:0] = [sys.argv[1], sys.argv[2], sys.argv[3]]
from sapi_config_lab.contracts import CompileOptions
from sapi_config_lab.coordinate.backend import N8nBackend
from sapi_config_lab.coordinate import native_record
from sapi_config_lab.profile import read, read_bindings
root = Path(sys.argv[2])
namespace = runpy.run_path(sys.argv[3] + "/main.py")
globals_ = namespace["main"].__globals__
manifest = root / "source-manifest.json"
manifest.write_text(json.dumps({"test": "frozen"}))
def record(*args):
    with patch.object(native_record, "Path", side_effect=lambda value: manifest if value == "/opt/source-manifest.json" else Path(value)):
        return native_record.record(*args)
def execute(task, output, submission, options, functions):
    assert options["native_mode"] == "control"
    assert options["mode"] == "stub"
    assert "SAPI_BRIDGE_URL" not in os.environ
    N8nBackend((task / "operations.js").read_text()).compile(
        read(submission), read_bindings(task / "bindings.yaml"),
        CompileOptions(llm_mode=options["mode"], bridge_url=os.environ.get("SAPI_BRIDGE_URL"), operation_url="http://simulator:8000/tools"),
    )
    return 0
globals_.update(ROOT=root / "payload", OUT=root / "out", SUBMISSION=root / "payload/solution/config.yaml", record=record, run_task=execute)
with patch.dict(os.environ, {"SAPI_NATIVE_MODE": "control", "SAPI_HOSTED_ADMISSION": "0"}):
    os.environ.pop("SAPI_BRIDGE_URL", None)
    assert namespace["main"]() == 0
metadata = json.loads((root / "out/native-task.json").read_text())
assert metadata["options"]["mode"] == "stub"
"""
            completed = subprocess.run(
                [sys.executable, "-I", "-B", "-c", script, str(ROOT / "src"), str(root), str(root / "payload/tests")],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
