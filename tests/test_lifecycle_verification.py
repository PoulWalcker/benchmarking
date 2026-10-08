"""Independent synthetic evidence controls, not proof of real controller/n8n runs."""

import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
import uuid

from sapi_config_lab.paths import workspace_root
from tests.support.lifecycle import config as fixture_config
from tests.support.lifecycle import native_acceptance
from verification.contracts import Recorded, Rejected
from verification.lifecycle import verify_lifecycle as audit_lifecycle
from verification.lifecycle_submission import evaluate_lifecycle, plan_lifecycle


def hash_json(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()


def native_fixture(config, admission, *, execution="1"):
    text = config["workflow"]["steps"][0]["with"]["text"]
    text = config["workflow"]["inputs"]["text"] if isinstance(text, dict) else text
    values = {"decode": {"value": json.loads(text)}}
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
        "mapping": {"decode": "decode"},
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
    for sid in ("decode",):
        state["steps"][sid] = values[sid]
        state["statuses"][sid] = "completed"
        state["events"][sid] = {
            "step_id": sid,
            "operation": "fixture." + sid,
            "actor": None,
            "status": "completed",
            "implementation": "script",
        }
        name = run["mapping"][sid]
        add(name, state, parent)
        parent = name
    final = {
        "output": (
            values["decode"]["value"]
            if config["workflow"]["output"]["ref"] == "steps.decode.value"
            else values["decode"]
        )
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
    config = fixture_config()
    if rejected:
        config["workflow"]["output"] = {"ref": "steps.decode.value"}
    config["workflow"]["revision"] = revision
    config["activation"]["workflow_ref"]["revision"] = revision
    ref = config["activation"]["workflow_ref"]
    admission = {
        "kind": "Callback",
        "purpose": "test",
        "rule_id": "fixture-test-requested",
        "event_id": f"test-{revision}",
        "workflow_ref": ref,
    }
    key = hash_json({name: admission[name] for name in ("rule_id", "event_id")})
    decision = {
        "verifier": "fixture.acceptance_v1",
        "passed": not rejected,
        "findings": ["Output must reference the decoded result"] if rejected else [],
    }
    definition = f"lifecycle-fixture@{revision}"
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
            "lifecycle-fixture": {
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
                "record": native_fixture(config, admission, execution=str(revision)),
                "decision": decision,
            }
        },
        "transitions": transitions,
        "rebuilds": {},
        "adhoc": [],
    }


def rebuilt_snapshot():
    first, second = snapshot(rejected=True), snapshot(revision=2)
    source, target = "lifecycle-fixture@1", "lifecycle-fixture@2"
    failed_key = next(iter(first["events"]))
    target_ref = second["definitions"][target]["workflow_ref"]
    key = hash_json({"event": failed_key, "target": target_ref})
    first["definitions"][source]["status"] = "archived"
    second["definitions"][target]["forked_from"] = source
    first["definitions"].update(second["definitions"])
    first["events"].update(second["events"])
    first["families"] = second["families"]
    first["families"]["lifecycle-fixture"]["rebuild_count"] = 1
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


@unittest.skipUnless(os.environ.get("SAPI_RUN_DOCKER_TESTS") == "1", "Native Docker lifecycle proof is opt-in")
class NativeLifecycleTests(unittest.TestCase):
    def test_generic_callback_cron_revision_and_rebuild_evidence(self):
        from sapi_config_lab.execute.host import LAB_IMAGE

        root = workspace_root()
        evidence = root / "reports/migration-12" / ("native-" + uuid.uuid4().hex[:10])
        evidence.mkdir(parents=True)
        command = [
            "docker",
            "run",
            "--rm",
            "--network",
            "none",
            "--entrypoint",
            "python",
            "-e",
            "PYTHONPATH=/app/lab/src:/app/lab",
            "-v",
            f"{root}:/app/lab:ro",
            "-v",
            f"{evidence}:/evidence",
            LAB_IMAGE,
            "/app/lab/tests/support/lifecycle/native_control.py",
        ]
        (evidence / "command.json").write_text(json.dumps(command, indent=2))
        with (evidence / "docker.log").open("w") as log:
            result = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, timeout=900)
        self.assertEqual(result.returncode, 0, f"See {evidence / 'docker.log'}")
        verdict = json.loads((evidence / "verification.json").read_text())
        self.assertEqual(len(verdict["native_executions"]), 4)
        self.assertEqual(verdict["calls"], [])


class LifecycleVerificationTests(unittest.TestCase):
    def test_generic_submission_plan_uses_explicit_tick_and_mutation(self):
        config = fixture_config()
        entries = plan_lifecycle(
            config,
            {"positive": [{"name": "fixture", "inputs": {"text": '{"count": 11}'}}]},
            tick="2026-10-05T05:00:00+00:00",
            wrong_output={"value": {"count": -1}},
        )
        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[0]["config"]["workflow"]["id"], "lifecycle-fixture")
        self.assertEqual(entries[0]["tick"], "2026-10-05T05:00:00+00:00")
        self.assertEqual(entries[1]["config"]["workflow"]["output"], {"value": {"count": -1}})
        self.assertEqual(config, fixture_config())

    def test_same_value_literal_output_does_not_prove_declared_preview_origin(self):
        state = snapshot()
        event = next(iter(state["events"].values()))
        config = state["definitions"]["lifecycle-fixture@1"]["config"]
        config["workflow"]["output"] = copy.deepcopy(event["record"]["output"])
        run = native_fixture(config, event["admission"])
        self.assertEqual(run["output"], event["record"]["output"])
        self.assertFalse(native_acceptance(config, run, event["admission"], mode="stub")["passed"])

    def test_real_execution_of_wrong_literal_input_is_a_business_rejection(self):
        state = snapshot()
        event = next(iter(state["events"].values()))
        config = state["definitions"]["lifecycle-fixture@1"]["config"]
        config["workflow"]["steps"][0]["with"]["text"] = '{"count": -1}'
        run = native_fixture(config, event["admission"])
        result = native_acceptance(config, run, event["admission"], mode="stub")
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
        check = native_acceptance(
            state["definitions"]["lifecycle-fixture@1"]["config"], event["record"], event["admission"], mode="stub"
        )
        self.assertFalse(check["passed"])
        event["decision"].update(passed=True, findings=[])
        event["state"] = "passed"
        state["transitions"][-1]["data"]["decision"] = event["decision"]
        with self.assertRaises(Rejected):
            verify_lifecycle(state, mode="stub")

    def test_injected_negative_decision_cannot_accept_forged_native_evidence(self):
        state = snapshot(rejected=True)
        event = next(iter(state["events"].values()))
        entry = {
            "name": "negative",
            "kind": "mutated-generated-workflow",
            "config": state["definitions"]["lifecycle-fixture@1"]["config"],
        }
        event["record"] = {"status": "success"}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            case = root / "evidence/cases/negative"
            case.mkdir(parents=True)
            (case / "snapshot.json").write_text(json.dumps(state))
            (case / "event.json").write_text(json.dumps(event))
            recorded = Recorded(
                root / "evidence",
                root / "evaluation",
                {"negative": {}},
                {"name": "fixture", "sources_sha256": "fixture"},
            )
            rows = []
            with self.assertRaises(Rejected):
                evaluate_lifecycle([entry], recorded, rows, acceptance=lambda *args, **kwargs: {"passed": False})
            self.assertFalse(rows[0]["passed"])
            self.assertNotIn("business_rejected", rows[0])

    def test_injected_acceptance_cannot_bypass_native_provenance(self):
        state = snapshot()
        next(iter(state["events"].values()))["record"] = {"status": "success"}
        with self.assertRaises(Rejected):
            audit_lifecycle(state, acceptance=lambda *args, **kwargs: {"passed": True}, mode="stub")

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
            "changed-definition": lambda s: s["definitions"]["lifecycle-fixture@1"]["config"]["workflow"][
                "inputs"
            ].update(text="{}"),
            "release-before-test": lambda s: s["transitions"][-1].update(sequence=1),
            "invented-release": lambda s: s["families"]["lifecycle-fixture"].update(active="lifecycle-fixture@2"),
            "fake-admission": lambda s: next(iter(s["events"].values()))["record"]["result"].update(admission={}),
            "missing-native-operation": lambda s: next(iter(s["events"].values()))["record"]["run_data"].pop("decode"),
        }
        for name, mutate in mutations.items():
            with self.subTest(name=name):
                state = snapshot()
                mutate(state)
                with self.assertRaises(Rejected):
                    verify_lifecycle(state, mode="stub")


def verify_lifecycle(snapshot, **options):
    return audit_lifecycle(snapshot, acceptance=native_acceptance, **options)


if __name__ == "__main__":
    unittest.main()
