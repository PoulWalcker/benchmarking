"""Harbor settings per scenario: staged verifier timeouts must contain the plan the verifier will run."""

import contextlib
from dataclasses import replace
import hashlib
import io
import json
from pathlib import Path
import tempfile
import tomllib
import unittest
from unittest.mock import patch

from sapi_config_lab.coordinate import scenarios
from sapi_config_lab.coordinate.controls import LOCAL_TESTS_SECONDS, TRANSPORT_SECONDS, suite_seconds
from sapi_config_lab.coordinate.packages import (
    AUTHORED_DEADLINE_SECONDS,
    JOB_OVERHEAD_SECONDS,
    TRIAL_OVERHEAD_SECONDS,
    VERIFIER_OVERHEAD_SECONDS,
    hosted_verifier_seconds,
    job_seconds,
    stage_tasks,
    verifier_bounds,
    verifier_seconds,
)
from sapi_config_lab.coordinate.providers import ENVIRONMENTS, EVALUATORS
from sapi_config_lab.coordinate.runs import Run
from sapi_config_lab.coordinate.scenarios import HARBOR_DEFAULTS, SCENARIOS, load_scenario
from sapi_config_lab.execute.host import BUILD_TIMEOUT_SECONDS
from sapi_config_lab.execute.n8n import execution_ceiling
from sapi_config_lab.paths import workspace_root
from sapi_config_lab.profile import read
from tests.support.pinned import AVAILABLE
from verification.contracts import Rejected
from verification.verify import main as verify_main
from verification.verify import plan

ROOT = workspace_root()
FIXTURES = [name for name, scenario in SCENARIOS.items() if scenario.environment == "fixtures"]


def planned_seconds(issued: dict) -> int:
    """The same bound, summed over the plan the verifier itself issues."""
    total = 0
    for entry in issued["entries"]:
        deadline = entry["config"]["execution"]["deadline_seconds"]
        if entry["procedure"] == "lifecycle":
            total += (2 if entry["tick"] else 1) * execution_ceiling(deadline, bound=True)
        else:
            total += execution_ceiling(deadline, bound=False)
    return total


class VerifierBoundTests(unittest.TestCase):
    def test_the_bound_is_the_verifiers_own_plan_for_every_case_set(self):
        with tempfile.TemporaryDirectory() as directory:
            for name in FIXTURES:
                scenario = SCENARIOS[name]
                cases = scenario.cases()
                authored = read(scenario.config)
                authored["execution"]["deadline_seconds"] = AUTHORED_DEADLINE_SECONDS
                path = Path(directory) / (name + ".yaml")
                path.write_text(json.dumps(authored))
                runs = [planned_seconds(plan(name, scenario.config, cases, "stub"))]
                if "lifecycle" not in authored:
                    runs += [
                        planned_seconds(plan(name, scenario.config, cases, "live", case))
                        for case in cases.get("live_cases") or [case["name"] for case in cases["positive"]]
                    ]
                expected = max(runs) + VERIFIER_OVERHEAD_SECONDS
                self.assertEqual(verifier_seconds(read(scenario.config), cases), expected, name)
                stub = planned_seconds(plan(name, path, cases, "stub"))
                self.assertEqual(verifier_seconds(authored, cases), max([stub, *runs[1:]]) + VERIFIER_OVERHEAD_SECONDS)

    def test_the_format_states_the_authored_deadline(self):
        self.assertIn(f"deadline_seconds: {AUTHORED_DEADLINE_SECONDS}", (ROOT / "generation/FORMAT.md").read_text())

    def test_a_multi_case_plan_counts_every_sequential_execution(self):
        # 3 positive + 7 negative cases, a wrong-result probe and three invalid definitions, each import + execute.
        self.assertEqual(
            verifier_seconds(read(SCENARIOS["invoice-total"].config), SCENARIOS["invoice-total"].cases()),
            14 * (180 + 180) + VERIFIER_OVERHEAD_SECONDS,
        )

    def test_an_unexecutable_deadline_leaves_only_a_live_grant(self):
        cases = SCENARIOS["invoice-total"].cases()
        self.assertEqual(verifier_seconds(None, cases), 180 + 690 + VERIFIER_OVERHEAD_SECONDS)
        self.assertEqual(verifier_seconds({"execution": {"deadline_seconds": True}}, cases), 870 + 120)

    def test_hosted_runs_wait_on_begin_the_workflow_and_finish(self):
        self.assertEqual(
            hosted_verifier_seconds(120, 375, admit=False), 240 + 120 + 240 + 375 + VERIFIER_OVERHEAD_SECONDS
        )
        self.assertEqual(hosted_verifier_seconds(120, 375, admit=True), VERIFIER_OVERHEAD_SECONDS)


def with_timeout(name: str, seconds: int, *, legacy: bool = True):
    scenario = SCENARIOS[name]
    return patch.dict(
        scenarios.SCENARIOS,
        {
            name: replace(
                scenario,
                benchmark=None if legacy else scenario.benchmark,
                harbor={**scenario.harbor, "verifier_timeout_sec": seconds},
            )
        },
    )


class StagingTests(unittest.TestCase):
    def test_every_scenario_stages_in_every_mode(self):
        for mode in ("oracle", "generation"):
            with tempfile.TemporaryDirectory() as directory:
                stage_tasks(Path(directory) / "tasks", mode=mode, scenarios=tuple(FIXTURES))

    def test_a_valid_timeout_is_rendered_into_task_toml(self):
        with tempfile.TemporaryDirectory() as directory, with_timeout("invoice-total", 5160):
            stage_tasks(Path(directory) / "tasks", scenarios=("invoice-total",))
            settings = tomllib.loads((Path(directory) / "tasks/invoice-total/task.toml").read_text())
        self.assertEqual(settings["metadata"]["name"], "invoice-total")
        self.assertEqual(settings["verifier"]["timeout_sec"], 5160)
        self.assertEqual(settings["agent"]["timeout_sec"], HARBOR_DEFAULTS["agent_timeout_sec"])
        self.assertEqual(settings["environment"]["memory_mb"], HARBOR_DEFAULTS["memory_mb"])

    def test_an_insufficient_timeout_refuses_staging_before_writing(self):
        with tempfile.TemporaryDirectory() as directory, with_timeout("invoice-total", 5159):
            with self.assertRaisesRegex(ValueError, "invoice-total: its verifier may run 5160s"):
                stage_tasks(Path(directory) / "tasks", scenarios=("invoice-total",))
            self.assertFalse((Path(directory) / "tasks").exists())

    def test_a_replayed_long_deadline_is_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            config = read(SCENARIOS["invoice-total"].config)
            config["execution"]["deadline_seconds"] = 3600
            path = Path(directory) / "slow.yaml"
            path.write_text(json.dumps(config))
            submission = {"path": path, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
            with self.assertRaisesRegex(ValueError, "harbor.verifier_timeout_sec"):
                stage_tasks(
                    Path(directory) / "tasks",
                    mode="replay",
                    scenarios=("invoice-total",),
                    submissions={"invoice-total": submission},
                )

    @unittest.skipUnless(AVAILABLE, "Requires the pinned upstream source and benchmark extra")
    def test_hosted_scenarios_need_room_for_a_run(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            with_timeout("checkout-recovery", 719),
            patch.dict(SCENARIOS, {"checkout-recovery": replace(SCENARIOS["checkout-recovery"], benchmark=None)}),
        ):
            with self.assertRaisesRegex(ValueError, "checkout-recovery: its verifier may run 1095s"):
                stage_tasks(Path(directory) / "tasks", scenarios=("checkout-recovery",))
            stage_tasks(Path(directory) / "admit", mode="generation", scenarios=("checkout-recovery",))

    def test_native_admission_uses_the_resolved_verifier_phase(self):
        from sapi_config_lab.harbor_integration.tasks import validate_config

        scenario = SCENARIOS["invoice-total"]
        config = validate_config((scenario.directory / scenario.benchmark.harbor_task).read_text())
        required = verifier_bounds(("invoice-total",))["invoice-total"]
        with tempfile.TemporaryDirectory() as directory, with_timeout("invoice-total", 1, legacy=False):
            stage_tasks(Path(directory) / "valid", scenarios=("invoice-total",))
            config.verifier.timeout_sec = required - 1
            with patch("sapi_config_lab.harbor_integration.tasks.validate_config", return_value=config):
                with self.assertRaisesRegex(ValueError, "invoice-total: its verifier may run"):
                    stage_tasks(Path(directory) / "invalid", scenarios=("invoice-total",))
            self.assertFalse((Path(directory) / "invalid").exists())


class OuterTimeoutTests(unittest.TestCase):
    def test_a_job_contains_every_trial_at_its_own_limits_and_plan_estimate(self):
        names = ("invoice-total", "checkout-recovery")
        bounds = verifier_bounds(names)
        expected = (
            2
            * sum(
                SCENARIOS[name].harbor["build_timeout_sec"]
                + SCENARIOS[name].harbor["agent_timeout_sec"]
                + TRIAL_OVERHEAD_SECONDS
                + seconds
                for name, seconds in bounds.items()
            )
            + JOB_OVERHEAD_SECONDS
        )
        self.assertEqual(job_seconds(bounds, attempts=2), expected)
        self.assertEqual(
            suite_seconds(names),
            LOCAL_TESTS_SECONDS + BUILD_TIMEOUT_SECONDS + TRANSPORT_SECONDS + 2 * job_seconds(bounds),
        )

    def test_run_harbor_derives_its_timeout_from_the_staged_bounds(self):
        staging = Path(tempfile.mkdtemp())
        task = staging / "tasks" / "invoice-total"
        task.mkdir(parents=True)
        (task / "task.toml").write_text("")
        run = Run(Path(tempfile.mkdtemp()), {}, {}, "t", staging=staging, harbor_argv=["harbor"])
        run.bounds = {"invoice-total": 3240}
        timeouts = []
        with (
            patch(
                "sapi_config_lab.coordinate.runs.run_logged",
                side_effect=lambda *a, timeout: timeouts.append(timeout) or 0,
            ),
            patch("sapi_config_lab.coordinate.runs.collect_jobs"),
        ):
            run.harbor("oracle", run.tasks, "oracle")
            run.harbor("one", task, "oracle", attempts="3")
        self.assertEqual(timeouts, [job_seconds(run.bounds), job_seconds(run.bounds, attempts=3)])
        self.assertEqual(run.report["harbor_timeouts"], {"oracle": timeouts[0], "one": timeouts[1]})


class DeadlineBudgetTests(unittest.TestCase):
    def budget(self, mode: str, name: str) -> object:
        with tempfile.TemporaryDirectory() as directory:
            stage_tasks(Path(directory) / "tasks", mode=mode, scenarios=(name,))
            tests = Path(directory) / "tasks" / name / "tests"
            if (tests / "benchmark.json").exists():
                return {
                    "deadline_seconds": json.loads((tests / "benchmark.json").read_text())["options"][
                        "deadline_seconds"
                    ]
                }
            return json.loads((tests / "budget.json").read_text())

    def test_each_package_states_the_deadline_it_was_sized_for(self):
        self.assertEqual(self.budget("oracle", "invoice-total"), {"deadline_seconds": 30})
        self.assertEqual(self.budget("generation", "invoice-total"), {"deadline_seconds": AUTHORED_DEADLINE_SECONDS})

    def test_the_verifier_plans_nothing_for_a_longer_deadline(self):
        scenario = SCENARIOS["invoice-total"]
        with tempfile.TemporaryDirectory() as directory:
            for deadline, accepted in ((120, True), (60, True), (121, False), (True, False), ("120", False)):
                config = read(scenario.config)
                config["execution"]["deadline_seconds"] = deadline
                path = Path(directory) / "config.yaml"
                path.write_text(json.dumps(config))
                if accepted:
                    self.assertTrue(plan("invoice-total", path, scenario.cases(), deadline_budget=120)["entries"])
                    continue
                with self.assertRaises(Rejected) as raised:
                    plan("invoice-total", path, scenario.cases(), deadline_budget=120)
                self.assertEqual(raised.exception.code, "deadline_exceeds_budget")

    def test_the_packaged_command_reads_the_budget_beside_its_cases(self):
        scenario = SCENARIOS["invoice-total"]
        with tempfile.TemporaryDirectory() as directory:
            tests = Path(directory)
            (tests / "cases.json").write_text(json.dumps({"invoice-total": scenario.cases()}))
            (tests / "budget.json").write_text(json.dumps({"deadline_seconds": 29}))
            argv = ["verify.py", "plan", "--scenario", "invoice-total", "--config", str(scenario.config)]
            argv += ["--cases", str(tests / "cases.json"), "--output", str(tests / "plan.json")]
            with patch("sys.argv", argv), contextlib.redirect_stdout(io.StringIO()) as stdout:
                self.assertEqual(verify_main(), 1)
        self.assertIn("exceeds the 29s", stdout.getvalue())


class ScenarioSettingsTests(unittest.TestCase):
    def scenario(self, meta: dict, name: str = "12-example") -> scenarios.Scenario:
        directory = Path(tempfile.mkdtemp()) / name
        directory.mkdir()
        (directory / "scenario.json").write_text(
            json.dumps({"environment": "fixtures", "evaluator": "verifier", "default": False, **meta})
        )
        return load_scenario(directory)

    def test_a_section_overrides_only_its_own_keys(self):
        loaded = self.scenario({"harbor": {"cpus": 2, "verifier_timeout_sec": 4000}})
        self.assertEqual(loaded.harbor, HARBOR_DEFAULTS | {"cpus": 2, "verifier_timeout_sec": 4000})
        self.assertEqual(self.scenario({}).harbor, HARBOR_DEFAULTS)

    def test_default_must_be_an_explicit_boolean(self):
        self.assertTrue(self.scenario({"default": True}).default)
        self.assertFalse(self.scenario({"default": False}).default)
        for value in (None, "false", 0, 1):
            with self.assertRaisesRegex(ValueError, "Invalid benchmark definition"):
                self.scenario({"default": value})
        with tempfile.TemporaryDirectory() as root:
            directory = Path(root) / "12-no-default"
            directory.mkdir()
            (directory / "scenario.json").write_text(json.dumps({"environment": "fixtures", "evaluator": "verifier"}))
            with self.assertRaisesRegex(ValueError, "Invalid benchmark definition"):
                load_scenario(directory)

    def test_values_are_bounded_integers_with_known_keys(self):
        for section in (
            {"cpus": True},
            {"cpus": 1.5},
            {"memory_mb": "2048\\n[agent]"},
            {"cpus": 0},
            {"verifier_timeout_sec": 10**9},
            {"environment_mode": "shared"},
            [],
        ):
            with self.assertRaises(ValueError, msg=section):
                self.scenario({"harbor": section})

    def test_a_name_must_be_safe_to_render(self):
        for name in ('12-bad"name', "12-Upper", "example"):
            with self.assertRaises(ValueError, msg=name):
                self.scenario({}, name)

    def test_an_environment_must_be_known_and_judged_by_one_of_its_evaluators(self):
        hosted = {"workflow_id": "example", "budgets": {"runtime_model_calls": 1}}
        self.assertTrue(self.scenario({**hosted, "environment": "autowfbench", "evaluator": "autowfbench"}).hosted)
        for meta in (
            {"environment": "autowfbench", "evaluator": "verifier"},
            {"environment": "unknown", "evaluator": "autowfbench"},
            {"environment": "fixtures", "evaluator": "autowfbench"},
        ):
            with self.assertRaises(ValueError, msg=meta):
                self.scenario({**hosted, **meta})

    def test_every_environment_names_only_registered_evaluators(self):
        for name, environment in ENVIRONMENTS.items():
            self.assertLessEqual(environment.evaluators, set(EVALUATORS), name)


class AgentTests(unittest.TestCase):
    def test_an_agent_that_runs_commands_needs_a_separate_environment(self):
        run = Run(Path(tempfile.mkdtemp()), {}, {}, "t", staging=Path(tempfile.mkdtemp()))
        with self.assertRaisesRegex(RuntimeError, "shared verifier environment"):
            run.harbor("job", run.tasks, "terminus-2")


if __name__ == "__main__":
    unittest.main()
