"""Independent synthetic evidence controls, not proof of real controller/n8n runs."""

import copy
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import yaml

from sapi_config_lab import profile
from sapi_config_lab.coordinate.packages import stage_tasks
from sapi_config_lab.coordinate.scenarios import all_cases, select_scenarios
from sapi_config_lab.paths import workspace_root
from verification.contracts import Rejected
from verification.lifecycle import digest_native, verify_lifecycle
from verification.lifecycle_submission import validate_task
from verification.verify import evaluate


def hash_json(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()


def native_digest(config, admission, *, execution="1"):
    articles = config["workflow"]["inputs"]["articles"]
    summary = {
        "text": "A warehouse opened in Dubai. The shop added a payment option.",
        "article_ids": [a["id"] for a in articles],
    }
    values = {"prepare": {"articles": articles}, "summarize": summary, "preview": {"mode": "preview", **summary}}
    preview_input = next(step for step in config["workflow"]["steps"] if step["uses"] == "digest.preview")["with"][
        "summary"
    ]
    if "ref" not in preview_input:
        values["preview"] = {"mode": "preview", **preview_input}
    state = {
        "inputs": copy.deepcopy(config["workflow"]["inputs"]),
        "steps": {},
        "statuses": {},
        "events": {},
        "admission": admission,
        "deadline_at_ms": 120000,
    }
    run = {
        "import_exit_code": 0,
        "execute_exit_code": 0,
        "n8n_version": "2.41.5",
        "workflow_id": "native",
        "execution_id": execution,
        "status": "success",
        "persisted_status": "success",
        "input": {"activation": admission},
        "mapping": {"prepare": "prepare", "summarize": "summarize [LLM STUB]", "preview": "preview"},
        "run_data": {},
    }

    def add(name, value, parent):
        record = {
            "startTime": len(run["run_data"]) * 10,
            "executionTime": 1,
            "data": {"main": [[{"json": copy.deepcopy(value)}]]},
        }
        if parent:
            record["source"] = [{"previousNode": parent, "previousNodeOutput": 0, "previousNodeRun": 0}]
        run["run_data"][name] = [record]

    add("Demo start", {}, None)
    add("Fixture", state, "Demo start")
    parent = "Fixture"
    for sid in ("prepare", "summarize", "preview"):
        state["steps"][sid] = values[sid]
        state["statuses"][sid] = "completed"
        state["events"][sid] = {
            "step_id": sid,
            "operation": "digest." + sid,
            "actor": None,
            "status": "completed",
            "implementation": "stub" if sid == "summarize" else "script",
        }
        name = run["mapping"][sid]
        add(name, state, parent)
        parent = name
    final = {
        "output": values[config["workflow"]["output"]["ref"].split(".")[1]]
        if "ref" in config["workflow"]["output"]
        else copy.deepcopy(config["workflow"]["output"]),
        "steps": copy.deepcopy(state["steps"]),
        "statuses": state["statuses"],
        "trace": list(state["events"].values()),
        "workflow_ref": config["activation"]["workflow_ref"],
        "spec_revision": config["spec_revision"],
        "llm_mode": "stub",
        "input_source": "event",
        "simulation": False,
        "admission": admission,
    }
    add("Result", final, parent)
    run.update(output=copy.deepcopy(final["output"]), result=copy.deepcopy(final))
    return run


def snapshot(*, rejected=False, revision=1):
    config = yaml.safe_load((Path(__file__).parents[1] / "benchmarks/05-daily-digest/config.yaml").read_text())
    if rejected:
        config["workflow"]["output"] = {"ref": "steps.summarize"}
    config["workflow"]["revision"] = revision
    config["activation"]["workflow_ref"]["revision"] = revision
    ref = config["activation"]["workflow_ref"]
    admission = {
        "kind": "Callback",
        "purpose": "test",
        "rule_id": "digest-test-requested",
        "event_id": f"test-{revision}",
        "workflow_ref": ref,
    }
    key = hash_json({name: admission[name] for name in ("rule_id", "event_id")})
    decision = {
        "verifier": "digest.acceptance_v1",
        "passed": not rejected,
        "findings": ["Digest must be a preview"] if rejected else [],
    }
    definition = f"daily-digest@{revision}"
    transitions = []
    for kind, data in (
        ("registered", {"definition": definition, "sha256": hash_json(config)}),
        ("event_admitted", {"event": key, "admission": admission, "state": "queued"}),
        ("execution_reserved", {"event": key, "definition": definition, "deadline_at": 120}),
        ("native_complete", {"event": key, "status": "success"}),
        ("test_result", {"event": key, "definition": definition, "decision": decision}),
    ):
        transitions.append({"sequence": len(transitions) + 1, "at": len(transitions), "kind": kind, "data": data})
    if not rejected:
        transitions.append(
            {"sequence": 6, "at": 5, "kind": "released", "data": {"definition": definition, "test_event": key}}
        )
    return {
        "schema": "sapi-lab-lifecycle/v1",
        "definitions": {
            definition: {
                "workflow_ref": ref,
                "config": config,
                "sha256": hash_json(config),
                "forked_from": None,
                "status": "rejected" if rejected else "released",
            }
        },
        "families": {
            "daily-digest": {
                "current": definition,
                "active": None if rejected else definition,
                "pending": None,
                "cron_event": None,
                "suspended": False,
                "rebuild_count": 0,
                "schedule": {"schedule": "0 9 * * *", "timezone": "Asia/Dubai"},
                "schedule_overlay": None,
            }
        },
        "events": {
            key: {
                "admission": admission,
                "definition": definition,
                "condition": True,
                "state": "failed" if rejected else "passed",
                "deadline_at": 120,
                "record": native_digest(config, admission, execution=str(revision)),
                "decision": decision,
            }
        },
        "transitions": transitions,
        "rebuilds": {},
        "adhoc": [],
    }


def rebuilt_snapshot():
    first, second = snapshot(rejected=True), snapshot(revision=2)
    source, target = "daily-digest@1", "daily-digest@2"
    failed_key = next(iter(first["events"]))
    target_ref = second["definitions"][target]["workflow_ref"]
    key = hash_json({"event": failed_key, "target": target_ref})
    first["definitions"][source]["status"] = "archived"
    second["definitions"][target]["forked_from"] = source
    first["definitions"].update(second["definitions"])
    first["events"].update(second["events"])
    first["families"] = second["families"]
    first["families"]["daily-digest"]["rebuild_count"] = 1
    first["rebuilds"][key] = {
        "state": "completed",
        "number": 1,
        "source": source,
        "target": target_ref,
        "test_event": failed_key,
        "findings": first["events"][failed_key]["decision"],
        "candidate_sha256": second["definitions"][target]["sha256"],
    }
    additions = [
        ("rebuild_queued", {"rebuild": key, "source": source, "target": target_ref}),
        ("rebuild_reserved", {"rebuild": key, "number": 1, "target": target_ref}),
        (
            "fork_persisted",
            {
                "rebuild": key,
                "definition": target,
                "sha256": second["definitions"][target]["sha256"],
                "forked_from": source,
            },
        ),
        ("archived", {"definition": source, "replacement": target}),
    ]
    additions += [(row["kind"], row["data"]) for row in second["transitions"][1:]]
    for kind, data in additions:
        first["transitions"].append(
            {"kind": kind, "data": data, "sequence": len(first["transitions"]) + 1, "at": len(first["transitions"])}
        )
    return first


class LifecycleVerificationTests(unittest.TestCase):
    def test_same_value_literal_output_does_not_prove_declared_preview_origin(self):
        state = snapshot()
        event = next(iter(state["events"].values()))
        config = state["definitions"]["daily-digest@1"]["config"]
        config["workflow"]["output"] = copy.deepcopy(event["record"]["output"])
        run = native_digest(config, event["admission"])
        self.assertEqual(run["output"], event["record"]["output"])
        self.assertFalse(digest_native(config, run, event["admission"], mode="stub")["passed"])

    def test_real_execution_of_wrong_literal_input_is_a_business_rejection(self):
        state = snapshot()
        event = next(iter(state["events"].values()))
        config = state["definitions"]["daily-digest@1"]["config"]
        config["workflow"]["steps"][2]["with"]["summary"] = {
            "text": "Incorrect prepared content",
            "article_ids": ["wrong"],
        }
        run = native_digest(config, event["admission"])
        result = digest_native(config, run, event["admission"], mode="stub")
        self.assertFalse(result["passed"])

    def test_genuine_rejected_output_requires_persisted_fork_before_archive_and_new_test(self):
        state = rebuilt_snapshot()
        result = verify_lifecycle(state, mode="stub")
        self.assertEqual([row["accepted"] for row in result["native_executions"]], [False, True])
        broken = copy.deepcopy(state)
        transitions = broken["transitions"]
        persist = next(row for row in transitions if row["kind"] == "fork_persisted")
        archive = next(row for row in transitions if row["kind"] == "archived")
        persist["kind"], archive["kind"] = archive["kind"], persist["kind"]
        persist["data"], archive["data"] = archive["data"], persist["data"]
        with self.assertRaises(Rejected):
            verify_lifecycle(broken, mode="stub")
        broken = copy.deepcopy(state)
        next(iter(broken["rebuilds"].values()))["findings"] = {"passed": False, "findings": ["invented"]}
        with self.assertRaises(Rejected):
            verify_lifecycle(broken, mode="stub")

    def test_accepted_native_test_releases_exact_revision(self):
        result = verify_lifecycle(snapshot(), mode="stub")
        self.assertEqual(len(result["native_executions"]), 1)
        self.assertEqual(result["calls"], [])
        self.assertFalse(result["model_authorship_verified"])
        self.assertFalse(result["wall_clock_cron_verified"])

    def test_controller_pass_cannot_override_actual_nonpreview_output(self):
        state = snapshot(rejected=True)
        event = next(iter(state["events"].values()))
        check = digest_native(
            state["definitions"]["daily-digest@1"]["config"], event["record"], event["admission"], mode="stub"
        )
        self.assertFalse(check["passed"])
        event["decision"].update(passed=True, findings=[])
        event["state"] = "passed"
        state["transitions"][-1]["data"]["decision"] = event["decision"]
        with self.assertRaises(Rejected):
            verify_lifecycle(state, mode="stub")

    def test_status_only_or_mock_backend_cannot_prove_native_execution(self):
        state = snapshot()
        next(iter(state["events"].values()))["record"] = {
            "status": "success",
            "output": {"mode": "preview", "text": "hello", "article_ids": ["a1", "a2"]},
        }
        with self.assertRaises(Rejected):
            verify_lifecycle(state, mode="stub")

    def test_revision_hash_order_and_source_corruptions_fail(self):
        mutations = {
            "changed-definition": lambda s: s["definitions"]["daily-digest@1"]["config"]["workflow"]["inputs"].update(
                articles=[]
            ),
            "release-before-test": lambda s: s["transitions"][-1].update(sequence=1),
            "invented-release": lambda s: s["families"]["daily-digest"].update(active="daily-digest@2"),
            "fake-admission": lambda s: next(iter(s["events"].values()))["record"]["result"].update(admission={}),
            "missing-native-operation": lambda s: next(iter(s["events"].values()))["record"]["run_data"].pop("prepare"),
        }
        for name, mutate in mutations.items():
            with self.subTest(name=name):
                state = snapshot()
                mutate(state)
                with self.assertRaises(Rejected):
                    verify_lifecycle(state, mode="stub")


class LifecycleSubmissionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)

    def test_task_extension_is_scoped_and_has_no_reference_solution(self):
        path = Path(self.temporary.name) / "tasks"
        stage_tasks(path, mode="generation", scenarios=("daily-digest", "invoice-total"))
        digest = (path / "daily-digest/instruction.md").read_text()
        invoice = (path / "invoice-total/instruction.md").read_text()
        self.assertIn("TASK-SPECIFIC LIFECYCLE EXTENSION", digest)
        self.assertNotIn("TASK-SPECIFIC LIFECYCLE EXTENSION", invoice)
        self.assertFalse((path / "daily-digest/solution").exists())
        self.assertFalse((path / "daily-digest/environment/base.yaml").exists())
        self.assertEqual(tuple(select_scenarios()), ("invoice-total", "ticket-routing", "competitor-report"))

    def test_task_constraints_are_enforced(self):
        config = profile.read(workspace_root() / "benchmarks/05-daily-digest/config.yaml")
        validate_task(config)
        config["lifecycle"]["on_test_fail"]["max_rebuilds"] = 3
        with self.assertRaises(AssertionError):
            validate_task(config)

    def test_harbor_report_does_not_require_opening_the_shared_log_mount(self):
        report_dir = Path(self.temporary.name) / "shared-verifier-log" / "evaluation"
        original_open = os.open

        def mount_open(path, flags, *args, **kwargs):
            if Path(path) == report_dir:
                raise PermissionError("Harbor log mount cannot be opened for directory fsync")
            return original_open(path, flags, *args, **kwargs)

        with patch("sapi_config_lab.evidence.os.open", side_effect=mount_open):
            report = evaluate(
                "daily-digest",
                Path(self.temporary.name) / "missing.yaml",
                report_dir.parent / "evidence",
                all_cases()["daily-digest"],
            )
        self.assertFalse(report["passed"])
        self.assertEqual(report["error_type"], "FileNotFoundError")
        self.assertEqual(json.loads((report_dir / "report.json").read_text()), report)


if __name__ == "__main__":
    unittest.main()
