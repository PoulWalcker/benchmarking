"""Hosted model operations need live admission, never invented deterministic task answers."""

import copy
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import yaml

from sapi_config_lab.compile.n8n import RESOURCES, compile_n8n
from sapi_config_lab.coordinate import hosted_worker
from sapi_config_lab.coordinate.live import check_trials
from sapi_config_lab.coordinate.runs import Run
from sapi_config_lab.coordinate.scenarios import SCENARIOS
from sapi_config_lab.profile import Unsupported, read, read_bindings


def model_config(scenario, operation):
    config = copy.deepcopy(read(scenario.config))
    binding = read_bindings(scenario.bindings)[operation]
    config["workflow"]["kind"] = "Gantt"
    config["workflow"]["steps"] = [
        {
            "id": "plan",
            "kind": "LLM",
            "uses": operation,
            "with": {key: [] if key == "evidence" else "task" for key in binding["inputs"]},
        }
    ]
    config["workflow"]["dependencies"] = []
    config["workflow"]["output"] = {"ref": "steps.plan"}
    return config


class HostedPreflightTests(unittest.TestCase):
    def test_advertised_model_operations_fail_clearly_in_stub_mode_and_compile_live(self):
        for name, operation in [
            ("crm-lead-qualification", "crm.plan"),
            ("checkout-recovery", "incident.plan"),
            ("checkout-recovery", "incident.summarize"),
        ]:
            scenario = SCENARIOS[name]
            config = model_config(scenario, operation)
            with self.subTest(operation=operation):
                with self.assertRaisesRegex(Unsupported, "No local implementation.*" + operation):
                    compile_n8n(config, read_bindings(scenario.bindings))
                document, _ = compile_n8n(
                    config, read_bindings(scenario.bindings), llm_mode="live", bridge_url="http://localhost:1"
                )
                self.assertTrue(any(node["type"].endswith(".httpRequest") for node in document["nodes"]))

    def test_crm_model_candidate_admits_without_running_a_stub_or_environment(self):
        scenario = SCENARIOS["crm-lead-qualification"]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            submission = root / "config.yaml"
            submission.write_text(yaml.safe_dump(model_config(scenario, "crm.plan")))
            (root / "bindings.yaml").write_bytes(scenario.bindings.read_bytes())
            with patch.object(hosted_worker, "SUBMISSION", submission), patch.object(hosted_worker, "TESTS", root):
                report = hosted_worker.admit(
                    {"scenario": scenario.name, "runtime_model_calls": 1, "deadline_seconds": 120}
                )
            self.assertTrue(report["passed"], report)
            trial = {"task_name": scenario.name, "acceptance": report, "exception": None, "rewards": {"reward": 1.0}}
            selected = {scenario.name: {"sha256": report["submission_sha256"]}}
            check_trials([trial], selected, mode="stub", admission=True)
            with self.assertRaises(ValueError):
                check_trials([trial], selected, mode="live")
            trial["acceptance"]["submission_sha256"] = "changed"
            with self.assertRaises(ValueError):
                check_trials([trial], selected, mode="stub", admission=True)


class PreflightExecutionTests(unittest.TestCase):
    def test_hosted_admission_never_starts_a_world(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run = Run(root / "run", {}, {}, "test", staging=root / "staging")
            run.bounds = {"crm-lead-qualification": 720}
            task = run.tasks / "crm-lead-qualification"
            (task / "tests").mkdir(parents=True)
            (task / "task.toml").write_text("")
            (task / "tests/environment.json").write_text('{"scenario":"crm-lead-qualification","seed":0}')
            with (
                patch("sapi_config_lab.coordinate.runs.TrialHost") as host,
                patch("sapi_config_lab.coordinate.runs.run_logged", return_value=0) as command,
                patch("sapi_config_lab.coordinate.runs.collect_jobs"),
            ):
                run.harbor("preflight", run.tasks, "oracle", admission=True)
            host.assert_not_called()
            self.assertIn("SAPI_HOSTED_ADMISSION=1", command.call_args.args[0])

    def test_local_capability_discovery_matches_the_executable_table(self):
        import re

        source = (RESOURCES / "operations.js").read_text()
        process = subprocess.run(
            ["node", "-e", source + "\nconsole.log(JSON.stringify(Object.keys(operations)))"],
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        )
        self.assertEqual(set(re.findall(r"^  '([^']+)':", source, re.MULTILINE)), set(json.loads(process.stdout)))
