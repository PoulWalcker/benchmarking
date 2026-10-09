"""Direct Harbor parity controls over checked-in tasks, without staging or model calls."""

import ast
import hashlib
import json
import os
from pathlib import Path
import runpy
import subprocess
import tempfile
import tomllib
import unittest
import uuid

from harbor.models.task.task import Task
import yaml

from sapi_config_lab.contracts import OutputArtifact
from sapi_config_lab.coordinate.provenance import source_manifest
from sapi_config_lab.paths import workspace_root
from tests.support.checkout_evaluation import SCORING
from tests.support.pinned import AVAILABLE, SOURCE

ROOT = workspace_root()
REPORTS = ROOT / "reports/native-phase1"


class NativeTaskTests(unittest.TestCase):
    def test_static_task_assets_preserve_public_and_oracle_bytes(self):
        pinned = {
            "invoice-total": {
                "instruction.md": "eed1f481fd235ddc2e263df1f6212d70ea87a4e8b00287c93abd13b92cc50b7c",
                "solution/config.yaml": "2b4d9b5569cd811053bb2e504de4111b171be188b2deff88b4ca63f187406e99",
            },
            "checkout-recovery": {
                "instruction.md": "4d54d5e80750d7c588687a6a8ed6ca935e1d35aae68ece9f92725a96460b4f47",
                "solution/config.yaml": "c9176e088aa14e7afcde3cfb8a461841061a25cc9d8abb8b70acb8154150b1a9",
            },
        }
        self.assertFalse((ROOT / "benchmarks").exists())
        for name, files in pinned.items():
            task = ROOT / "tasks" / name
            self.assertFalse((task / "config.yaml").exists())
            for relative, expected in files.items():
                self.assertEqual(hashlib.sha256((task / relative).read_bytes()).hexdigest(), expected)
            config = tomllib.loads((task / "task.toml").read_text())
            self.assertEqual(config["agent"]["user"], "1000")
            self.assertEqual(config["environment"]["network_mode"], "no-network")
            self.assertEqual(config["verifier"]["environment"]["network_mode"], "public")
            self.assertEqual(config["verifier"]["environment_mode"], "separate")
            self.assertEqual(config["artifacts"][0]["exclude"], ["*"])
            self.assertNotIn("verifier_compose", config.get("metadata", {}).get("sapi", {}))
            for script in (task / "tests").glob("*.py"):
                imports = {
                    node.module for node in ast.walk(ast.parse(script.read_text())) if isinstance(node, ast.ImportFrom)
                }
                self.assertFalse(imports & {"sapi_config_lab.benchmark", "sapi_config_lab.benchmark_loading"})

    def test_harbor_author_environment_has_no_private_compose_overlay(self):
        for name in ("invoice-total", "checkout-recovery"):
            task = Task(ROOT / "tasks" / name)
            self.assertFalse((task.paths.environment_dir / "docker-compose.yaml").exists())
            self.assertEqual(task.config.verifier.environment_mode, "separate")
        trusted = yaml.safe_load((ROOT / "tasks/checkout-recovery/tests/docker-compose.yaml").read_text())
        self.assertEqual(set(trusted["services"]), {"main", "simulator"})
        self.assertTrue(trusted["networks"]["verification"]["internal"])

    def test_fake_transport_records_calls_without_precreated_directories(self):
        transport_for = runpy.run_path(str(ROOT / "tasks/checkout-recovery/tests/fake_bridge.py"))["transport_for"]
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary) / "evidence/runtime-model"
            transport = transport_for(directory)
            reply = json.loads(transport("fake://control", "OPERATION: incident.plan\n", 10, 10000))
            self.assertTrue(reply["ok"])
            self.assertFalse(json.loads((directory / "call-1.json").read_text())["paid_dispatch"])
            self.assertEqual(json.loads(reply["output"])["new"], "charge_card(amount, currency)")

    @unittest.skipUnless(AVAILABLE, "Requires pinned upstream source")
    def test_saved_calibration_adapter_binds_fresh_evidence_and_rejects_tampering(self):
        adapter = runpy.run_path(str(ROOT / "tasks/checkout-recovery/tests/calibration_transport.py"))
        template = ROOT / "tasks/checkout-recovery/tests/calibration/judge-reply.json"
        original = template.read_bytes()
        run = json.loads((ROOT / "tasks/checkout-recovery/tests/calibration/run-log.json").read_text())
        run["run_id"] = "fresh-native-calibration-attempt"
        run["submission"]["run_id"] = run["run_id"]
        contract = SCORING.freeze_contract(
            SOURCE,
            "production-checkout-recovery",
            judge_mode="demo",
            artifact=OutputArtifact("incident_summary", "incident-summary.md"),
        )
        reply, audit = adapter["adapt"](template, contract, run)
        self.assertEqual(SCORING.evaluate(contract, run, reply)["normalized_reward"], 0.732)
        self.assertEqual(reply["judgement"]["run_id"], run["run_id"])
        self.assertTrue(reply["provenance"]["artifact_id"].startswith("saved-calibration-"))
        self.assertEqual(audit["template_sha256"], adapter["TEMPLATE_SHA256"])
        self.assertEqual(audit["judge_dispatches"], 0)
        self.assertNotEqual(audit["original_provenance"]["run_log_digest"], audit["run_log_digest"])
        self.assertIsNotNone(SCORING.judgement_fault(contract, run, json.loads(original)))
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            with self.assertRaises(ValueError):
                SCORING.evaluate_once(contract, run, output / "stale", judgement=json.loads(original))
            changed = output / "changed-template.json"
            changed.write_bytes(original + b"\n")
            with self.assertRaisesRegex(ValueError, "template identity mismatch"):
                adapter["adapt"](changed, contract, run)
        self.assertEqual(template.read_bytes(), original)
        run["events"] = [event for event in run["events"] if event["source"] != "candidate"]
        missing, audit = adapter["adapt"](template, contract, run)
        self.assertIsNone(missing)
        self.assertEqual(audit["status"], "missing_required_evidence")

    @unittest.skipUnless(AVAILABLE, "Requires pinned upstream source")
    def test_saved_spike_calibration_replays_through_original_scorer(self):
        directory = ROOT / "tasks/checkout-recovery/tests/calibration"
        contract = SCORING.freeze_contract(
            SOURCE,
            "production-checkout-recovery",
            judge_mode="demo",
            artifact=OutputArtifact("incident_summary", "incident-summary.md"),
        )
        result = SCORING.evaluate(
            contract,
            json.loads((directory / "run-log.json").read_text()),
            json.loads((directory / "judge-reply.json").read_text()),
        )
        self.assertEqual(result["normalized_reward"], 0.732)
        self.assertEqual(result["deterministic_points"], 6)
        self.assertTrue(result["execution_pass"])


@unittest.skipUnless(
    os.environ.get("SAPI_RUN_NATIVE_TESTS") == "1", "Set SAPI_RUN_NATIVE_TESTS=1 for unpaid Harbor controls"
)
class NativeHarborTests(unittest.TestCase):
    def run_native(self, task, agent="oracle", *args):
        REPORTS.mkdir(parents=True, exist_ok=True)
        name = task + "-" + uuid.uuid4().hex[:8]
        command = [
            "harbor",
            "run",
            "-p",
            str(ROOT / "tasks" / task),
            "-a",
            agent,
            "--max-retries",
            "0",
            "--force-build",
            "--job-name",
            name,
            "--jobs-dir",
            str(REPORTS / "jobs"),
            *args,
        ]
        identity = {
            "command": command,
            "sources": source_manifest(ROOT),
            "images": {
                role: subprocess.check_output(
                    ["docker", "image", "inspect", "--format", "{{.Id}}", f"sapi-native-{task}-{role}:phase1"],
                    text=True,
                ).strip()
                for role in ("public", "verifier")
            },
        }
        (REPORTS / (name + "-identity.json")).write_text(json.dumps(identity, indent=2) + "\n")
        completed = subprocess.run(
            command,
            cwd=ROOT,
            env={**os.environ, "PYTHONPATH": str(ROOT)},
            capture_output=True,
            text=True,
            timeout=1200,
        )
        (REPORTS / (name + ".log")).write_text(completed.stdout + completed.stderr)
        trials = list((REPORTS / "jobs" / name).glob(task + "__*/result.json"))
        self.assertEqual(len(trials), 1, str(REPORTS / (name + ".log")))
        trial = trials[0].parent
        result = json.loads(trials[0].read_text())
        for suffix in ("__env", "__verifier__trial"):
            label = "label=com.docker.compose.project=" + trial.name.lower() + suffix
            for command in (["ps", "-aq"], ["network", "ls", "-q"], ["volume", "ls", "-q"]):
                self.assertFalse(subprocess.check_output(["docker", *command, "--filter", label], text=True).strip())
        self.assertTrue((trial / "verifier/result.json").is_file(), str(trial))
        return trial, result, json.loads((trial / "verifier/result.json").read_text())

    def test_invoice_oracle_all_original_observations(self):
        trial, harbor, result = self.run_native("invoice-total")
        self.assertEqual(harbor["verifier_result"]["rewards"], {"reward": 1.0})
        self.assertTrue(result["acceptance"])
        observation = json.loads((trial / "verifier/evidence/observation.json").read_text())
        self.assertEqual(len(observation["entries"]), 14)
        cases = list((trial / "verifier/evidence/cases").glob("*/case.json"))
        self.assertEqual(len(cases), 14)
        self.assertTrue(any(json.loads(path.read_text()).get("engine_version") == "2.41.5" for path in cases))

    def test_invoice_nop_and_wrong_yaml(self):
        for agent, args in (
            ("nop", []),
            ("tests.native.control_agent:ControlAgent", ["--ak", "candidate=tests/native/fixtures/invoice-wrong.yaml"]),
            (
                "tests.native.control_agent:ControlAgent",
                ["--ak", "candidate=tests/native/fixtures/invoice-invalid.yaml"],
            ),
        ):
            with self.subTest(agent=agent, args=args):
                _, harbor, result = self.run_native("invoice-total", agent, *args)
                self.assertEqual(harbor["verifier_result"]["rewards"], {"reward": 0.0})
                self.assertFalse(result["acceptance"])

    def test_checkout_reference_state_and_saved_replay(self):
        trial, harbor, result = self.run_native("checkout-recovery")
        self.assertEqual(harbor["verifier_result"]["rewards"], {"reward": 0.732})
        self.assertTrue(result["acceptance"])
        self.assertTrue((trial / "verifier/evidence/environment-evidence.json").is_file())
        self.assertTrue(list((trial / "verifier/world").glob("*-snapshot.json")))
        contract = SCORING.freeze_contract(
            SOURCE,
            "production-checkout-recovery",
            judge_mode="demo",
            artifact=OutputArtifact("incident_summary", "incident-summary.md"),
        )
        directory = trial / "verifier/evaluation"
        audit = json.loads((trial / "verifier/calibration-transport.json").read_text())
        self.assertEqual(audit["status"], "adapted")
        self.assertEqual(audit["judge_dispatches"], 0)
        self.assertEqual(audit["template_sha256"], "0af5a1b5ffd8cc704cb67a0c4a15afbbebf95b4a3389216f735699c15cbc178b")
        self.assertNotEqual(audit["original_provenance"]["run_log_digest"], audit["run_log_digest"])
        self.assertFalse((directory / "judge-dispatch.json").exists())
        replay = SCORING.evaluate(
            contract,
            json.loads((directory / "run-log.json").read_text()),
            json.loads((directory / "judge-reply.json").read_text()),
        )
        self.assertEqual(replay["normalized_reward"], 0.732)

    def test_checkout_nop_and_missing_judge_keep_null_quality(self):
        for agent, args in (("nop", []), ("oracle", ["--ve", "SAPI_NATIVE_JUDGE_MODE=none"])):
            with self.subTest(agent=agent):
                trial, harbor, result = self.run_native("checkout-recovery", agent, *args)
                self.assertEqual(harbor["exception_info"]["exception_type"], "RewardFileNotFoundError")
                self.assertIsNone(result["harbor_reward"])
                self.assertFalse((trial / "verifier/reward.txt").exists())

    def test_checkout_real_runtime_bridge_and_failures(self):
        for mode in ("success", "timeout", "failure", "wrong-model", "malformed"):
            with self.subTest(mode=mode):
                trial, _, result = self.run_native(
                    "checkout-recovery",
                    "tests.native.control_agent:ControlAgent",
                    "--ak",
                    "candidate=tests/native/fixtures/checkout-llm.yaml",
                    "--ve",
                    "SAPI_NATIVE_FAKE_MODE=" + mode,
                )
                self.assertEqual(result["acceptance"], mode == "success")
                record = json.loads((trial / "verifier/evidence/cases/workflow/case.json").read_text())
                self.assertEqual(record["engine_version"], "2.41.5")
                self.assertTrue((trial / "verifier/evidence/runtime-dispatch.jsonl").is_file())
                calls = list((trial / "verifier/evidence/runtime-model").glob("call-*.json"))
                self.assertEqual(len(calls), 2 if mode == "success" else 1)
                self.assertTrue(all(json.loads(path.read_text())["paid_dispatch"] is False for path in calls))

    def test_checkout_runtime_budget_rejects_before_dispatch(self):
        REPORTS.mkdir(parents=True, exist_ok=True)
        candidate = yaml.safe_load((ROOT / "tests/native/fixtures/checkout-llm.yaml").read_text())
        llm = next(step for step in candidate["workflow"]["steps"] if step["kind"] == "LLM")
        candidate["workflow"]["steps"].extend({**llm, "id": "extra" + str(index)} for index in range(3))
        path = REPORTS / ("over-budget-" + uuid.uuid4().hex + ".yaml")
        path.write_text(yaml.safe_dump(candidate))
        trial, _, result = self.run_native(
            "checkout-recovery", "tests.native.control_agent:ControlAgent", "--ak", "candidate=" + str(path)
        )
        self.assertFalse(result["acceptance"])
        self.assertIsNone(result["execution"])
        self.assertIsNone(result["harbor_reward"])
        self.assertFalse((trial / "verifier/evidence/window.json").exists())
        self.assertFalse((trial / "verifier/evidence/runtime-dispatch.jsonl").exists())

    def test_candidate_isolation_and_hostile_transfer(self):
        for task in ("invoice-total", "checkout-recovery"):
            with self.subTest(task=task):
                trial, _, result = self.run_native(
                    task,
                    "tests.native.control_agent:ProbeAgent",
                    "--ak",
                    "candidate=tasks/" + task + "/solution/config.yaml",
                )
                self.assertTrue(result["acceptance"])
                self.assertTrue(json.loads((trial / "agent/isolation.json").read_text())["private_paths_absent"])
        for attack in ("symlink", "fifo", "extra", "artifact"):
            with self.subTest(attack=attack):
                args = ["--ak", "attack=" + attack]
                if attack in ("extra", "artifact"):
                    args += ["--ak", "candidate=tasks/invoice-total/solution/config.yaml"]
                trial, _, result = self.run_native("invoice-total", "tests.native.control_agent:ControlAgent", *args)
                self.assertEqual(result["acceptance"], attack == "artifact")
                self.assertFalse((trial / "artifacts/discarded-convention/reward.txt").exists())
