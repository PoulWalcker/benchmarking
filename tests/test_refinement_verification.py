"""Synthetic native-record tests; these tests do not claim actual n8n execution."""

import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from tests.support.refinement import check_refinement_history, definition, review, verify_refinement
from verification.contracts import Rejected
from verification.refinement import json_value


def evidence(values, *, mode="live", maximum=3, limit=10):
    """Construct evidence independently, without compiler/controller/operation code."""
    config = definition()
    config["execution"]["refinement"]["max_attempts"] = maximum
    config["workflow"]["inputs"]["ceiling"] = limit
    run = {
        "workflow_id": "native-workflow",
        "execution_id": "73",
        "n8n_version": "2.41.5",
        "import_exit_code": 0,
        "execute_exit_code": 0,
        "status": "success",
        "persisted_status": "success",
        "result_node_present": True,
        "run_data": {},
        "mapping": {},
    }
    for number in range(1, maximum + 1):
        run["mapping"][f"attempt{number}:draft"] = f"Attempt {number} / draft [LLM {mode.upper()}]"
        run["mapping"][f"attempt{number}:check"] = f"Attempt {number} / check"

    def add(name, value, parent=None, channel=0, branch=None, error=None):
        channels = [[{"json": copy.deepcopy(value)}]]
        if branch is not None:
            channels = [[], []]
            channels[0 if branch else 1] = [{"json": copy.deepcopy(value)}]
        record = {"startTime": len(run["run_data"]) * 10, "executionTime": 1, "data": {"main": channels}}
        if parent:
            record["source"] = [{"previousNode": parent, "previousNodeOutput": channel, "previousNodeRun": 0}]
        if error:
            record["error"] = {"message": error}
            record["data"] = {}
        run["run_data"][name] = [record]

    add("Demo start", {})
    history = {"max_attempts": maximum, "attempts": [], "accepted_attempt": None}
    runtime = {"previous": None, "feedback": []}
    for number, value in enumerate(values, 1):
        prefix = f"Attempt {number} / "
        fixture = "Fixture" if number == 1 else prefix + "Fixture"
        state = {
            "inputs": config["workflow"]["inputs"],
            "runtime": copy.deepcopy(runtime),
            "steps": {},
            "statuses": {},
            "events": {},
            "llm_mode": mode,
            "deadline_at_ms": 90000,
            "refinement": copy.deepcopy(history),
        }
        add(fixture, state, "Demo start" if number == 1 else f"Continue {number - 1}")
        draft = {"value": value}
        check = review(value, config["workflow"]["inputs"]["target"], limit)
        errors = check["errors"]
        event = {
            "step_id": "draft",
            "operation": "probe.advance",
            "actor": None,
            "status": "completed",
            "implementation": mode,
        }
        draft_node = run["mapping"][f"attempt{number}:draft"]
        if mode == "live":
            invocation = f"numeric-refinement/r1/draft/attempt{number}/native-workflow/73"
            event["invocation_id"] = invocation
            request = {
                "invocation_id": invocation,
                "operation": "probe.advance",
                "actor": None,
                "inputs": copy.deepcopy(runtime),
            }
            prepared = {**copy.deepcopy(state), "request": request, "should_run": True}
            add(prefix + "Prepare draft", prepared, fixture)
            add(prefix + "Guard draft", prepared, prefix + "Prepare draft", branch=True)
            add(
                prefix + "Agency draft",
                {"status": "completed", "invocation_id": invocation, "output": draft},
                prefix + "Guard draft",
            )
            parent = prefix + "Agency draft"
        else:
            parent = fixture
        state["steps"]["draft"] = draft
        state["statuses"]["draft"] = "completed"
        state["events"]["draft"] = event
        add(draft_node, state, parent)
        state["steps"]["check"] = check
        state["statuses"]["check"] = "completed"
        state["events"]["check"] = {
            "step_id": "check",
            "operation": "probe.check",
            "actor": None,
            "status": "completed",
            "implementation": "script",
        }
        check_node = run["mapping"][f"attempt{number}:check"]
        add(check_node, state, draft_node)
        attempt = {
            "number": number,
            "runtime": copy.deepcopy(runtime),
            "output": draft,
            "steps": copy.deepcopy(state["steps"]),
            "statuses": copy.deepcopy(state["statuses"]),
            "trace": list(copy.deepcopy(state["events"]).values()),
            "accepted": not errors,
        }
        history["attempts"].append(attempt)
        if not errors:
            history["accepted_attempt"] = number
        continuing = bool(errors) and number < maximum
        if continuing:
            runtime = {"previous": draft, "feedback": errors}
        state.update(refinement=copy.deepcopy(history), runtime=copy.deepcopy(runtime), continue_refinement=continuing)
        add(f"Checkpoint {number}", state, check_node)
        if number < maximum:
            add(f"Continue {number}", state, f"Checkpoint {number}", branch=continuing)
    run["refinement"] = copy.deepcopy(history)
    parent = f"Continue {number}" if number < maximum else f"Checkpoint {number}"
    channel = 1 if number < maximum else 0
    if history["accepted_attempt"]:
        final = {
            "workflow_ref": {"id": "numeric-refinement", "revision": 1},
            "llm_mode": mode,
            "spec_revision": "06ddd3333109cea8a2cb3071609070d7a3c0d3ff",
            "refinement": copy.deepcopy(history),
            **{key: copy.deepcopy(attempt[key]) for key in ("output", "steps", "statuses", "trace")},
        }
        add("Result", final, parent, channel)
        run.update(result=copy.deepcopy(final), output=copy.deepcopy(final["output"]))
    else:
        add("Result", None, parent, channel, error="Refinement exhausted without an accepted result")
        run.update(status="error", persisted_status="error", execute_exit_code=1, result_node_present=True, output=None)
    return config, run


class ExtensionVerificationTests(unittest.TestCase):
    def test_native_steps_follow_dependencies_when_declarations_are_reversed(self):
        config, run = evidence([1, 2])
        config["workflow"]["steps"].reverse()
        result = verify_refinement(config, run)
        self.assertEqual(result["accepted_attempt"], 2)
        self.assertEqual(len(result["calls"]), 2)

    def test_numeric_until_does_not_equal_boolean_even_inside_nested_json(self):
        for value, expected in ((1, True), (0, False)):
            with self.subTest(value=value, expected=expected):
                config, run = evidence([value], maximum=1)
                config["execution"]["refinement"]["until"] = {"ref": "steps.draft.value", "eq": expected}
                self.assertTrue(verify_refinement(config, run)["exhausted"])
        config, run = evidence([1], maximum=1)
        config["execution"]["refinement"]["until"] = {
            "ref": "steps.check",
            "eq": {"pass": 0, "errors": ["Increase value"]},
        }
        self.assertTrue(verify_refinement(config, run)["exhausted"])
        config, run = evidence([2], maximum=1)
        config["execution"]["refinement"]["until"] = {"ref": "steps.draft.value", "eq": 2.0}
        self.assertEqual(verify_refinement(config, run)["accepted_attempt"], 1)

    def test_nested_predicate_comparison_matches_javascript_stringify(self):
        pairs = [
            [1, True],
            ["😀", "\ud83d\ude00"],
            [0, False],
            [1, 1.0],
            [-0.0, 0],
            [{"values": [True, {"pass": False}]}, {"values": [1, {"pass": 0}]}],
            [{"values": [1, {"pass": True}]}, {"values": [1.0, {"pass": True}]}],
            [{"a": 1, "b": 2}, {"b": 2, "a": 1}],
            [{"10": 1, "2": 2, "x": 3}, {"2": 2, "10": 1, "x": 3}],
            [{"01": 1, "x": 2}, {"x": 2, "01": 1}],
        ]
        result = subprocess.run(
            [
                "node",
                "-e",
                "console.log(JSON.stringify(JSON.parse(process.argv[1]).map(([a,b]) => JSON.stringify(a) === JSON.stringify(b))))",
                json.dumps(pairs),
            ],
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        )
        self.assertEqual([json_value(a) == json_value(b) for a, b in pairs], json.loads(result.stdout))

    def test_failed_result_node_presence_does_not_mean_a_successful_output(self):
        config, run = evidence([1] * 3)
        self.assertTrue(run["result_node_present"])
        self.assertEqual(run["run_data"]["Result"][0]["data"], {})
        self.assertTrue(verify_refinement(config, run)["exhausted"])
        run["result"] = {"output": {"value": 99}}
        with self.assertRaises(Rejected):
            verify_refinement(config, run)

    def test_first_or_later_acceptance_and_native_exhaustion(self):
        for values, expected in (([2], 1), ([1, 2], 2), ([1] * 3, None)):
            with self.subTest(values=values):
                config, run = evidence(values)
                result = verify_refinement(config, run)
                self.assertEqual(result["accepted_attempt"], expected)
                self.assertEqual(len(result["calls"]), len(values))

    def test_stub_evidence_does_not_claim_live_calls(self):
        config, run = evidence([1, 2], mode="stub")
        self.assertEqual(verify_refinement(config, run, mode="stub")["calls"], [])

    def test_coherent_but_false_review_is_rejected_independently(self):
        config, run = evidence([1, 2])
        attempts = copy.deepcopy(run["refinement"]["attempts"])
        attempts[0]["steps"]["check"] = {"pass": True, "errors": []}
        attempts[0]["accepted"] = True
        with self.assertRaises(Rejected):
            check_refinement_history(config, attempts[:1])

    def test_history_without_native_attempt_is_rejected(self):
        config, run = evidence([1, 2])
        run["run_data"].pop("Attempt 2 / draft [LLM LIVE]")
        with self.assertRaises(Rejected):
            verify_refinement(config, run)

    def test_native_evidence_corruptions_fail(self):
        def value(run, name):
            return run["run_data"][name][0]["data"]["main"][0][0]["json"]

        mutations = {
            "wrong-carry": lambda r: value(r, "Attempt 2 / Fixture")["runtime"].update(feedback=[]),
            "wrong-request": lambda r: value(r, "Attempt 2 / Prepare draft")["request"]["inputs"].update(previous=None),
            "wrong-identity": lambda r: value(r, "Attempt 2 / Agency draft").update(invocation_id="different"),
            "reset-deadline": lambda r: value(r, "Attempt 2 / Fixture").update(deadline_at_ms=100000),
            "wrong-output": lambda r: value(r, "Result").update(output={"value": 99}),
            "extra-native-call": lambda r: r["run_data"].update(
                {"Attempt 3 / Agency draft": r["run_data"]["Attempt 2 / Agency draft"]}
            ),
            "wrong-edge": lambda r: r["run_data"]["Attempt 2 / Fixture"][0]["source"][0].update(previousNodeOutput=1),
            "adapter-only-pass": lambda r: r.update(output={"value": 99}),
        }
        for name, mutate in mutations.items():
            with self.subTest(name=name):
                config, run = evidence([1, 2])
                mutate(run)
                with self.assertRaises(Rejected):
                    verify_refinement(config, run)

    def test_execution_after_acceptance_and_premature_stop_rejected(self):
        for values in ([2, 2], [1]):
            with self.subTest(values=values):
                config, run = evidence(values)
                with self.assertRaises(Rejected):
                    verify_refinement(config, run)


@unittest.skipUnless(os.environ.get("SAPI_RUN_DOCKER_TESTS") == "1", "native Docker proof is opt-in")
class NativeRefinementDockerTests(unittest.TestCase):
    def test_native_attempts_stop_and_exhaust_without_infrastructure_retries(self):
        from sapi_config_lab.coordinate.provenance import source_manifest
        from sapi_config_lab.execute.host import checked_harbor, running_containers
        from sapi_config_lab.paths import workspace_root

        root = workspace_root()
        reports = root / "reports/migration-11"
        reports.mkdir(parents=True, exist_ok=True)
        output = Path(tempfile.mkdtemp(prefix="native-refinement-", dir=reports))
        frozen = source_manifest(root)
        (output / "source-manifest.json").write_text(json.dumps(frozen, indent=2))
        task = output / "task"
        environment = task / "environment"
        payload = task / "tests/payload"
        environment.mkdir(parents=True)
        payload.mkdir(parents=True)
        (task / "instruction.md").write_text("Run the unpaid generic refinement instrument control.\n")
        (task / "task.toml").write_text(
            'schema_version = "1.4"\n[agent]\ntimeout_sec = 30\n'
            "[environment]\nbuild_timeout_sec = 1200\ncpus = 1\nmemory_mb = 2048\n"
            "[verifier]\ntimeout_sec = 1800\n"
        )
        dockerfile = (root / "infra/Dockerfile").read_text().replace("COPY tasks /app/lab/tasks/\n", "")
        (environment / "Dockerfile").write_text(dockerfile)
        shutil.copyfile(root / ".dockerignore", environment / ".dockerignore")
        for name in ("src", "generation"):
            shutil.copytree(root / name, environment / name, ignore=shutil.ignore_patterns("__pycache__"))
        shutil.copytree(root / "verification", payload / "verification", ignore=shutil.ignore_patterns("__pycache__"))
        fixture = payload / "tests/support/refinement"
        fixture.parent.mkdir(parents=True)
        (payload / "tests/__init__.py").write_text("")
        shutil.copytree(root / "tests/support/refinement", fixture, ignore=shutil.ignore_patterns("__pycache__"))
        (task / "tests/test.sh").write_text(
            "#!/bin/sh\nset -eu\nexport PYTHONPATH=/tests/payload:/app/lab/src\n"
            "n8n --version > /logs/verifier/runtime-version.txt\n"
            "python3 -m tests.support.refinement.native\n"
        )
        harbor, version = checked_harbor()
        before = set(running_containers().splitlines())
        command = [
            *harbor,
            "run",
            "--path",
            str(task),
            "--agent",
            "nop",
            "--jobs-dir",
            str(output / "jobs"),
            "--job-name",
            "refinement",
            "--n-concurrent",
            "1",
            "--max-retries",
            "0",
            "--force-build",
        ]
        (output / "command.json").write_text(json.dumps({"argv": command, "harbor_version": version}, indent=2))
        with (output / "harbor.log").open("w") as log:
            completed = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, check=False)
        self.assertEqual(completed.returncode, 0, (output / "harbor.log").read_text())
        results = list((output / "jobs/refinement").glob("*/result.json"))
        self.assertEqual(len(results), 1)
        native = json.loads(results[0].read_text())
        self.assertIsNone(native["exception_info"], native)
        self.assertEqual(native["verifier_result"]["rewards"], {"reward": 1.0})
        observations = json.loads((results[0].parent / "verifier/refinement/summary.json").read_text())
        self.assertTrue(observations["passed"])
        self.assertEqual([row["attempts"] for row in observations["cases"]], [1, 2, 3, 2])
        self.assertEqual([row["native_calls"] for row in observations["cases"]], [1, 2, 3, 0])
        self.assertEqual(set(running_containers().splitlines()) - before, set())
        self.assertEqual(source_manifest(root), frozen, "Sources changed during native controls")


if __name__ == "__main__":
    unittest.main()
