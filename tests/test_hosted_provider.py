"""A new hosted provider is provider code, one table entry each and a scenario directory: no core edit."""

from __future__ import annotations

import copy
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import secrets
import shutil
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.request import Request, urlopen

from sapi_config_lab.coordinate import evaluation
from sapi_config_lab.coordinate.evaluation import HOSTED_REPORT, control_passed, hosted_evaluation, trial_accepted
from sapi_config_lab.coordinate.hosted_worker import HTTP_TIMEOUT_SECONDS, harbor_reward
from sapi_config_lab.coordinate.packages import HOSTED_TEST, stage_tasks, verifier_bounds
from sapi_config_lab.coordinate.providers import ENVIRONMENTS, EVALUATORS, HostedEnvironment, HostedEvaluator
from sapi_config_lab.coordinate.runs import Hosting, Run
from sapi_config_lab.coordinate.scenarios import SCENARIOS, load_scenario
from sapi_config_lab.execute.host import HostConfig
from sapi_config_lab.paths import workspace_root

LIMIT_SECONDS = 60
REFERENCE = workspace_root() / "benchmarks/10-checkout-recovery"


class FakeWorld:
    """A provider's fresh world: bearer-authenticated `POST /tools` receipts, frozen once by `finalize`."""

    def __init__(self, host: HostConfig):
        self.token = secrets.token_urlsafe(16)
        self.leads: dict[str, dict] = {}
        self.events: list[dict] = []
        self.evidence: dict | None = None
        self.closed = False
        self.lock = threading.Lock()
        self.server = ThreadingHTTPServer((host.listen_host, 0), _world_handler(self))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = f"http://{host.container_host}:{self.server.server_port}"

    @property
    def connection(self) -> dict:
        return {"base_url": self.base_url, "access_token": self.token}

    @property
    def limit_seconds(self) -> int:
        return LIMIT_SECONDS

    @property
    def identity(self) -> dict:
        return {"world": "fake-crm"}

    def call(self, operation: str, arguments: dict) -> dict:
        with self.lock:
            if self.evidence is not None or operation != "create_lead":
                return {"ok": False, "error": {"code": "REJECTED", "message": operation, "retryable": False}}
            lead = f"lead-{len(self.leads) + 1}"
            self.leads[lead] = copy.deepcopy(arguments)
            self.events.append({"operation": operation, "id": lead})
            return {"ok": True, "value": {"id": lead}}

    def finalize(self) -> dict:
        with self.lock:
            if self.evidence is None:
                self.evidence = {"state": {"leads": copy.deepcopy(self.leads)}, "events": copy.deepcopy(self.events)}
            return copy.deepcopy(self.evidence)

    def transport_evidence(self) -> dict:
        return {"policy": "fake-direct", "calls": len(self.events)}

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)


def _world_handler(world: FakeWorld) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):
            if self.path != "/tools":
                status, body = 404, {"error": "Not found"}
            elif not hmac.compare_digest(self.headers.get("Authorization", ""), "Bearer " + world.token):
                status, body = 401, {"error": "Unauthorized"}
            else:
                request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                status, body = 200, world.call(request["operation"], request["arguments"])
            raw = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

    return Handler


def fake_result(record: Path) -> dict:
    """Deterministic acceptance, and no score: the trial completed and the world holds exactly one lead."""
    world = json.loads((record / "evidence/environment-evidence.json").read_text())
    trial = json.loads((record / "evidence/trial.json").read_text())
    return {
        "execution": trial["termination_reason"] == "completed",
        "acceptance": len(world["state"]["leads"]) == 1,
        "quality": None,
    }


def post(url: str, token: str, body: dict) -> dict:
    request = Request(
        url, json.dumps(body).encode(), {"Content-Type": "application/json", "Authorization": "Bearer " + token}
    )
    with urlopen(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
        return json.load(response)


def write_scenario(directory: Path, **overrides) -> Path:
    directory.mkdir(parents=True)
    meta = {
        "environment": "fake",
        "evaluator": "fake-state",
        "workflow_id": "fake-crm-workflow",
        "bindings": "bindings.yaml",
        "budgets": {"authoring_attempts": 1, "runtime_model_calls": 0},
    } | overrides
    (directory / "scenario.json").write_text(json.dumps(meta))
    for name in ("config.yaml", "bindings.yaml"):
        shutil.copyfile(REFERENCE / name, directory / name)
    (directory / "instruction.md").write_text("Execute the frozen YAML benchmark submission.\n")
    (directory / "authoring-notes.md").write_text("Create one lead.\n")
    return directory


class FakeProviderTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.root)
        self.worlds: list[FakeWorld] = []
        self.reevaluated: list[tuple] = []

        def start(scenario, seed, host):
            world = FakeWorld(host)
            self.worlds.append(world)
            return world

        def reevaluate(scenario, record, output, args):
            self.reevaluated.append((scenario.name, record, output))
            return fake_result(record)

        environment = HostedEnvironment(
            evaluators=frozenset({"fake-state"}),
            limit_seconds=lambda scenario: LIMIT_SECONDS,
            task=lambda scenario: "Create one lead in the CRM.",
            start=start,
            modules=(),
        )
        evaluator = HostedEvaluator(
            judge_calls=0, prepare=lambda scenario, judge_model: fake_result, reevaluate=reevaluate, modules=()
        )
        for table, entry in ((ENVIRONMENTS, {"fake": environment}), (EVALUATORS, {"fake-state": evaluator})):
            patcher = patch.dict(table, entry)
            patcher.start()
            self.addCleanup(patcher.stop)
        # Loaded inside the ENVIRONMENTS patch: the table entry is what makes `environment: fake` valid.
        self.scenario = load_scenario(write_scenario(self.root / "benchmarks/90-fake-crm"))
        # runs.py, packages.py and evaluation.py bind this same dict by name, so one patch reaches them all.
        patcher = patch.dict(SCENARIOS, {"fake-crm": self.scenario})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_the_scenario_loads_hosted_and_only_with_its_own_evaluator(self):
        self.assertTrue(self.scenario.hosted)
        self.assertEqual((self.scenario.environment, self.scenario.evaluator), ("fake", "fake-state"))
        with self.assertRaisesRegex(ValueError, "Invalid benchmark definition"):
            load_scenario(write_scenario(self.root / "other/90-fake-crm", evaluator="autowfbench"))
        with patch.dict(ENVIRONMENTS, clear=True), self.assertRaisesRegex(ValueError, "Invalid benchmark definition"):
            load_scenario(self.scenario.directory)

    def test_staging_builds_a_hosted_package_from_the_table_entry(self):
        stage_tasks(self.root / "oracle", mode="oracle", scenarios=("fake-crm",))
        tests = self.root / "oracle/fake-crm/tests"
        self.assertEqual(json.loads((tests / "environment.json").read_text()), {"scenario": "fake-crm", "seed": 0})
        self.assertEqual((tests / "test.sh").read_text(), HOSTED_TEST.format(action="run"))
        self.assertEqual((tests / "bindings.yaml").read_bytes(), (REFERENCE / "bindings.yaml").read_bytes())
        self.assertFalse((tests / "cases.json").exists())

        stage_tasks(self.root / "generation", mode="generation", scenarios=("fake-crm",))
        task = self.root / "generation/fake-crm"
        self.assertEqual(
            json.loads((task / "tests/admission.json").read_text()),
            {"scenario": "fake-crm", "runtime_model_calls": 0, "deadline_seconds": LIMIT_SECONDS},
        )
        self.assertEqual((task / "tests/test.sh").read_text(), HOSTED_TEST.format(action="admit"))
        self.assertIn("Create one lead in the CRM.", (task / "instruction.md").read_text())

    def run_trial(self, agent: str = "oracle") -> tuple[Run, int, list[dict], dict]:
        """One control job through Run.harbor and a real TrialHost; a stand-in plays Harbor's container."""
        host = HostConfig(container_host="127.0.0.1", listen_host="127.0.0.1")
        run = Run(self.root / "run", {}, {}, "t", host=host, staging=self.root / "staging")
        run.output.mkdir()
        stage_tasks(run.tasks, mode="oracle", scenarios=("fake-crm",))
        run.bounds = verifier_bounds(("fake-crm",), "oracle")
        seen: dict = {}

        def container(argv, log, **_):
            tasks = Path(argv[argv.index("--path") + 1])
            connection = json.loads((tasks / "fake-crm/tests/connection.json").read_text())
            begun = post(connection["url"] + "/begin", connection["token"], {})
            if agent == "nop":
                record = {"status": "missing_submission", "output": None}
            else:
                call = {"operation": "create_lead", "arguments": {"name": "Ada"}}
                seen["receipt"] = post(begun["operation_url"], begun["operation_token"], call)
                record = {"status": "success", "output": {"final_answer": "done"}}
            report = post(connection["url"] + "/finish", connection["token"], {"record": record})
            trial = Path(argv[argv.index("--jobs-dir") + 1]) / argv[argv.index("--job-name") + 1] / "fake-crm__1"
            trial.mkdir(parents=True)
            # The real container's reward rule, so Harbor's reward here is the one it would record.
            seen["reward"] = reward = harbor_reward(report["result"])
            rewards = None if reward is None else {"reward": float(reward)}
            (trial / "result.json").write_text(
                json.dumps({"task_name": "fake-crm", "verifier_result": {"rewards": rewards}})
            )
            return 0

        hosting = Hosting("stub", hosted_evaluation(("fake-crm",)))
        with patch("sapi_config_lab.coordinate.runs.run_logged", side_effect=container):
            exit_code, trials = run.harbor(agent, run.tasks, agent, hosting=hosting)
        return run, exit_code, trials, seen

    def test_a_trial_runs_end_to_end_through_the_real_host_and_evaluator_seam(self):
        run, exit_code, trials, seen = self.run_trial()
        self.assertEqual(exit_code, 0)
        self.assertEqual(seen["receipt"], {"ok": True, "value": {"id": "lead-1"}})
        (world,) = self.worlds
        self.assertTrue(world.closed)
        self.assertEqual(world.leads, {"lead-1": {"name": "Ada"}})

        record = run.output / "environments/oracle/fake-crm"
        evidence = record / "evidence"
        self.assertEqual(
            json.loads((evidence / "environment-evidence.json").read_text()),
            {"state": {"leads": {"lead-1": {"name": "Ada"}}}, "events": [{"operation": "create_lead", "id": "lead-1"}]},
        )
        self.assertEqual(json.loads((evidence / "transport-evidence.json").read_text())["calls"], 1)
        self.assertEqual(json.loads((evidence / "native-record.json").read_text())["status"], "success")
        trial_record = json.loads((evidence / "trial.json").read_text())
        self.assertEqual(
            (trial_record["scenario"], trial_record["world"], trial_record["termination_reason"]),
            ("fake-crm", "fake-crm", "completed"),
        )

        expected = fake_result(record)
        report = json.loads((record / "evaluation/report.json").read_text())
        self.assertEqual(
            report,
            {
                "schema": HOSTED_REPORT,
                "scenario": "fake-crm",
                "mode": "stub",
                "submission_sha256": None,
                "passed": True,
                "result": expected,
            },
        )
        self.assertEqual(expected, {"execution": True, "acceptance": True, "quality": None})
        (trial,) = trials
        self.assertEqual(seen["reward"], "1")
        self.assertEqual(trial["rewards"], {"reward": 1.0})
        self.assertEqual(trial["result"], expected)
        self.assertTrue(trial_accepted(trial))
        self.assertIsNone(self.scenario.reference_reward)
        self.assertTrue(control_passed("oracle", trial))

    def test_a_nop_trial_is_rejected_unscored_and_passes_its_control(self):
        run, exit_code, trials, seen = self.run_trial("nop")
        self.assertEqual(exit_code, 0)
        self.assertNotIn("receipt", seen)
        record = run.output / "environments/nop/fake-crm"
        trial_record = json.loads((record / "evidence/trial.json").read_text())
        self.assertEqual(trial_record["termination_reason"], "solution_failed")
        (trial,) = trials
        self.assertEqual(trial["result"], {"execution": False, "acceptance": False, "quality": None})
        self.assertEqual((seen["reward"], trial["rewards"]), ("0", {"reward": 0.0}))
        self.assertFalse(trial_accepted(trial))
        self.assertTrue(control_passed("nop", trial))
        self.assertFalse(control_passed("oracle", trial))

    def test_a_recorded_trial_is_reevaluated_by_its_provider(self):
        run, *_ = self.run_trial()
        record = run.output / "environments/oracle/fake-crm"
        output = self.root / "again"
        with patch("sys.stdout"):
            self.assertEqual(evaluation.main(["--record", str(record), "--output", str(output)]), 0)
        self.assertEqual(self.reevaluated, [("fake-crm", record, output)])
        self.assertEqual(
            json.loads((output / "result.json").read_text()), {"execution": True, "acceptance": True, "quality": None}
        )


if __name__ == "__main__":
    unittest.main()
