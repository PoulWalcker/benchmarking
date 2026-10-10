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
from sapi_config_lab.coordinate.native_tasks import public_sources, select_tasks
from sapi_config_lab.coordinate.provenance import source_manifest
from sapi_config_lab.paths import workspace_root
from tests.support.checkout_evaluation import SCORING
from tests.support.pinned import AVAILABLE, SOURCE
from tests.test_native_images import image_entries

ROOT = workspace_root()
REPORTS = ROOT / "reports/native-phase1"


class NativeTaskTests(unittest.TestCase):
    def test_static_task_assets_preserve_public_and_oracle_bytes(self):
        pinned = {
            "invoice-total": {
                "instruction.md": "eed1f481fd235ddc2e263df1f6212d70ea87a4e8b00287c93abd13b92cc50b7c",
                "solution/config.yaml": "2b4d9b5569cd811053bb2e504de4111b171be188b2deff88b4ca63f187406e99",
            },
            "research-report": {
                "instruction.md": "9204a247b684cb0613a20afea96aed3f2d231fb22cb0c15fca5edf44b2f2763d",
                "solution/config.yaml": "9c9b59245e67e988f0b259db492a82c840da34afbc79e7b1e99effe56682d81c",
            },
            "checkout-recovery": {
                "instruction.md": "4d54d5e80750d7c588687a6a8ed6ca935e1d35aae68ece9f92725a96460b4f47",
                "solution/config.yaml": "c9176e088aa14e7afcde3cfb8a461841061a25cc9d8abb8b70acb8154150b1a9",
            },
        }
        self.assertFalse((ROOT / "benchmarks").exists())
        for name, files in pinned.items():
            task = ROOT / "tasks" / name
            for relative, expected in files.items():
                self.assertEqual(hashlib.sha256((task / relative).read_bytes()).hexdigest(), expected)

    def test_discovered_task_config_and_evaluator_assets(self):
        root = ROOT / "tasks"
        for task in select_tasks(root, sorted(path.parent.name for path in root.glob("*/task.toml"))):
            with self.subTest(task=task.name):
                self.assertFalse((task / "config.yaml").exists())
                evaluator = task / "evaluation/evaluator.py"
                self.assertTrue(evaluator.is_file(), str(evaluator))
                ast.parse(evaluator.read_text())
                config = tomllib.loads((task / "task.toml").read_text())
                self.assertEqual(config["agent"]["user"], "1000")
                self.assertEqual(config["environment"]["network_mode"], "no-network")
                self.assertEqual(config["verifier"]["environment"]["network_mode"], "public")
                self.assertEqual(config["verifier"]["environment_mode"], "separate")
                self.assertEqual(config["artifacts"][0]["exclude"], ["*"])
                self.assertNotIn("verifier_compose", config.get("metadata", {}).get("sapi", {}))
                for script in (task / "tests").glob("*.py"):
                    imports = {
                        node.module
                        for node in ast.walk(ast.parse(script.read_text()))
                        if isinstance(node, ast.ImportFrom)
                    }
                    self.assertFalse(imports & {"sapi_config_lab.benchmark", "sapi_config_lab.benchmark_loading"})
                for area, role in (("environment", "public"), ("tests", "verifier")):
                    bases = [
                        line.split()[1]
                        for line in (task / area / "Dockerfile").read_text().splitlines()
                        if line.startswith("FROM ")
                    ]
                    self.assertEqual(bases, [f"sapi-native-{task.name}-{role}:phase1"])

    def test_harbor_author_environment_has_no_private_compose_overlay(self):
        root = ROOT / "tasks"
        for directory in select_tasks(root, sorted(path.parent.name for path in root.glob("*/task.toml"))):
            with self.subTest(task=directory.name):
                task = Task(directory)
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
        root = ROOT / "tasks"
        tasks = select_tasks(root, sorted(path.parent.name for path in root.glob("*/task.toml")))
        for directory in tasks:
            task = directory.name
            with self.subTest(task=task):
                trial, _, result = self.run_native(
                    task,
                    "tests.native.control_agent:ProbeAgent",
                    "--ak",
                    "candidate=tasks/" + task + "/solution/config.yaml",
                )
                self.assertTrue(result["acceptance"])
                self.assertTrue(json.loads((trial / "agent/isolation.json").read_text())["private_paths_absent"])
        for directory in tasks:
            task = directory.name
            for attack in ("symlink", "fifo", "extra", "artifact"):
                with self.subTest(task=task, attack=attack):
                    args = ["--ak", "attack=" + attack]
                    if attack in ("extra", "artifact"):
                        args += ["--ak", "candidate=tasks/" + task + "/solution/config.yaml"]
                    trial, _, result = self.run_native(task, "tests.native.control_agent:ControlAgent", *args)
                    self.assertEqual(result["acceptance"], attack == "artifact")
                    self.assertFalse((trial / "artifacts/discarded-convention/reward.txt").exists())


def public_image_result(base, files, expected, private):
    """Compare exported contents with declared sources and private task digests."""
    changed = sorted(path for path, entry in files.items() if base.get(path) != entry)
    removed = sorted(base.keys() - files.keys())
    base_digests = {entry["sha256"] for entry in base.values() if entry["sha256"] is not None}
    excluded = {path: digest for path, digest in private.items() if digest in base_digests}
    forbidden = set(private.values()) - base_digests
    leaks = {path: entry["sha256"] for path, entry in files.items() if entry["sha256"] in forbidden}
    return {
        "expected": expected,
        "changed": changed,
        "removed": removed,
        "files": {path: files.get(path) for path in expected},
        "private_sources": private,
        "excluded_base_digests": sorted(set(excluded.values())),
        "excluded_private_sources": excluded,
        "leaks": leaks,
    }


def assert_public_image(result):
    """Require an exact regular-file public diff and no non-base private bytes."""
    case = unittest.TestCase()
    case.assertEqual(set(result["changed"]), set(result["expected"]), "D1: unexpected filesystem diff")
    case.assertEqual(result["removed"], [], "D1: base entries removed")
    for path, digest in result["expected"].items():
        entry = result["files"][path]
        case.assertIsNotNone(entry, f"D1: missing public asset {path}")
        case.assertEqual(entry["type"], "0", f"D1: non-regular public asset {path}")
        case.assertEqual(entry["sha256"], digest, f"D1: differing public asset {path}")
    case.assertEqual(result["leaks"], {}, "D2: private task bytes entered the public image")


class PublicImageProofTests(unittest.TestCase):
    def test_exported_filesystem_proof_rejects_content_and_type_mutations(self):
        entry = {"type": "0", "mode": 0o644, "uid": 0, "gid": 0, "link": "", "sha256": "base"}
        base = {"/base": entry, "/empty": {**entry, "sha256": "empty"}}
        path = "/app/public/instruction.md"
        files = {**base, path: {**entry, "sha256": "public"}}
        expected = {path: "public"}
        private = {"solution/config.yaml": "private", "evaluation/__init__.py": "empty"}
        valid = public_image_result(base, files, expected, private)
        assert_public_image(valid)
        self.assertEqual(valid["excluded_base_digests"], ["empty"])
        self.assertEqual(valid["excluded_private_sources"], {"evaluation/__init__.py": "empty"})
        for label, changed in (
            ("extra", {**files, "/renamed-secret": {**entry, "sha256": "private"}}),
            ("extra directory", {**files, "/extra": {**entry, "type": "5", "sha256": None}}),
            ("missing", base),
            ("differing", {**files, path: {**entry, "sha256": "wrong"}}),
            ("removed base", {path: files[path]}),
            ("changed base permissions", {**files, "/base": {**entry, "mode": 0o777}}),
            ("symlink", {**files, path: {**entry, "type": "2", "link": "/base", "sha256": None}}),
            ("fifo", {**files, path: {**entry, "type": "6", "sha256": None}}),
            ("hardlink", {**files, path: {**entry, "type": "1", "link": "base", "sha256": "public"}}),
        ):
            with self.subTest(mutation=label), self.assertRaisesRegex(AssertionError, "D1:"):
                assert_public_image(public_image_result(base, changed, expected, private))
        # An allowlisted file can still disclose private bytes; D1 alone cannot catch that.
        with self.assertRaisesRegex(AssertionError, "D2:"):
            assert_public_image(public_image_result(base, files, expected, {"solution/copy": "public"}))
        self.assertEqual(
            public_image_result(base, {**files, "/renamed": {**entry, "sha256": "private"}}, expected, private)[
                "leaks"
            ],
            {"/renamed": "private"},
        )


@unittest.skipUnless(
    os.environ.get("SAPI_RUN_NATIVE_TESTS") == "1", "Set SAPI_RUN_NATIVE_TESTS=1 for unpaid Docker image checks"
)
class NativePublicImageTests(unittest.TestCase):
    def test_public_images_contain_exact_declared_bytes_and_no_private_bytes(self):
        sources = source_manifest(ROOT)
        record = json.loads((ROOT / "reports/native-image-build.json").read_text())
        self.assertEqual(record["sources"], sources, "Run ./run.sh with all discovered scenarios first")
        base_tag = "sapi-native-public-base:phase1"
        base_id = subprocess.check_output(
            ["docker", "image", "inspect", "--format", "{{.Id}}", base_tag], text=True
        ).strip()
        base = image_entries(base_id)
        identities = {base_tag: base_id}
        root = ROOT / "tasks"
        tasks = select_tasks(root, sorted(path.parent.name for path in root.glob("*/task.toml")))
        REPORTS.mkdir(parents=True, exist_ok=True)
        for task in tasks:
            with self.subTest(task=task.name):
                allowed = public_sources(task)
                prefix = f"tasks/{task.name}/"
                expected = {f"/app/public/{Path(path).name}": sources[prefix + path] for path in allowed}
                private = {
                    path: digest
                    for path, digest in sources.items()
                    if path.startswith(prefix) and path.removeprefix(prefix) not in allowed
                }
                tag = f"sapi-native-{task.name}-public:phase1"
                image_id = subprocess.check_output(
                    ["docker", "image", "inspect", "--format", "{{.Id}}", tag], text=True
                ).strip()
                self.assertEqual(image_id, record["images"][tag], "Public image differs from verified build")
                identities[tag] = image_id
                result = public_image_result(base, image_entries(image_id), expected, private)
                result.update(image=tag, image_id=image_id, base_image=base_tag, base_image_id=base_id)
                (REPORTS / f"{task.name}-public-image.json").write_text(json.dumps(result, indent=2) + "\n")
                assert_public_image(result)
        self.assertEqual(source_manifest(ROOT), sources, "Sources changed during public-image proof")
        for tag, identity in identities.items():
            self.assertEqual(
                subprocess.check_output(["docker", "image", "inspect", "--format", "{{.Id}}", tag], text=True).strip(),
                identity,
                "Image tag changed during public-image proof",
            )
