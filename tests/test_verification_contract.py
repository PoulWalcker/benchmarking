"""Acceptance tests use fabricated records, never claim to execute n8n."""

import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from sapi_config_lab.profile import read
from tests.support.invoice import DIRECTORY
from tests.support.invoice import fixture as invoice_fixture
from verification.contracts import Recorded, Rejected
from verification.n8n_provenance import check_rejection as selected_rejection
from verification.n8n_provenance import observe_execution as selected_observation
from verification.verify import check_execution as selected_execution
from verification.verify import corruption_checks as selected_corruption
from verification.verify import evaluator_identity

SPEC = "06ddd3333109cea8a2cb3071609070d7a3c0d3ff"


def invoice_record():
    inputs = {"invoices": [{"id": "A", "amount_minor": 7, "currency": "AED"}]}
    output = {"total_minor": 7, "currency": "AED", "invoice_count": 1}
    operations = [
        ("validate", "invoices.validate", {"invoices": inputs["invoices"]}),
        ("total", "invoices.sum", {"amount_minor": 7, "currency": "AED", "count": 1}),
        ("report", "invoices.report", output),
    ]
    envelope = {"inputs": inputs, "steps": {}, "statuses": {}, "events": {}}
    runs = {
        "Demo start": [{"startTime": -20, "executionTime": 1, "data": {"main": [[{"json": {}}]]}}],
        "Fixture": [{"startTime": -10, "executionTime": 1, "data": {"main": [[{"json": copy.deepcopy(envelope)}]]}}],
    }
    mapping = {}
    for index, (sid, operation, value) in enumerate(operations):
        envelope["steps"][sid] = value
        envelope["statuses"][sid] = "completed"
        envelope["events"][sid] = {
            "step_id": sid,
            "operation": operation,
            "status": "completed",
            "implementation": "local_js",
        }
        mapping[sid] = sid
        runs[sid] = [
            {
                "startTime": index * 10,
                "executionTime": 1,
                "source": [
                    {
                        "previousNode": "Fixture" if index == 0 else operations[index - 1][0],
                        "previousNodeOutput": 0,
                        "previousNodeRun": 0,
                    }
                ],
                "data": {"main": [[{"json": copy.deepcopy(envelope)}]]},
            }
        ]
    final = {
        "output": output,
        "trace": list(envelope["events"].values()),
        "steps": envelope["steps"],
        "statuses": envelope["statuses"],
        "workflow_ref": {"id": "invoice-total", "revision": 1},
        "spec_revision": SPEC,
        "llm_mode": "live",
    }
    runs["Result"] = [
        {
            "startTime": 40,
            "executionTime": 1,
            "source": [{"previousNode": "report", "previousNodeOutput": 0, "previousNodeRun": 0}],
            "data": {"main": [[{"json": copy.deepcopy(final)}]]},
        }
    ]
    run = {
        "report_schema": "sapi-lab-execution/v1",
        "status": "success",
        "output": copy.deepcopy(output),
        "result": copy.deepcopy(final),
        "workflow_id": "test-workflow",
        "execution_id": "test-execution",
        "n8n_version": "2.41.5",
        "import_exit_code": 0,
        "execute_exit_code": 0,
        "persisted_status": "success",
        "result_node_present": True,
        "run_data": runs,
        "mapping": mapping,
        "execution": {"status": "success", "succeeded": True, "engine": {"name": "n8n", "version": "2.41.5"}},
        "evidence": {
            "engine": {"kind": "n8n", "source": "engine_execution_records"},
            "workflow_trace": {"source": "workflow_authored", "events": copy.deepcopy(final["trace"])},
        },
        "llm": {"selected_mode": "live", "agency_http_call_count": 0, "provider_call_count": None},
        "acceptance": {"status": "not_evaluated", "passed": None},
    }
    return inputs, run


def submitted(inputs):
    config = read(DIRECTORY / "solution/config.yaml")
    config["workflow"]["inputs"] = inputs
    return config


def observe_execution(scenario, inputs, run, mode):
    return selected_observation(
        scenario, inputs, run, mode, config=submitted(inputs), contract=invoice_fixture().contract
    )


def check_execution(scenario, inputs, run, mode):
    return selected_execution(scenario, inputs, run, mode, config=submitted(inputs), fixture=invoice_fixture())


def corruption_checks(scenario, inputs, run, mode):
    return selected_corruption(scenario, inputs, run, mode, config=submitted(inputs), fixture=invoice_fixture())


def check_business_result(scenario, inputs, observation, mode):
    invoice_fixture().business(inputs, observation, mode)
    return {"output_verified": True}


def check_rejection(run, case, config):
    return selected_rejection(run, case, config, invoice_fixture().contract)


class VerificationContractTests(unittest.TestCase):
    def test_business_result_does_not_require_an_engine(self):
        inputs, run = invoice_record()
        observation, _ = observe_execution("invoice-total", inputs, run, "live")
        self.assertTrue(check_business_result("invoice-total", inputs, observation, "live")["output_verified"])
        observation.final["output"]["total_minor"] += 1
        with self.assertRaisesRegex(Rejected, "Wrong invoice result"):
            check_business_result("invoice-total", inputs, observation, "live")

    def test_engine_success_does_not_imply_acceptance(self):
        inputs, run = invoice_record()
        self.assertTrue(check_execution("invoice-total", inputs, run, "live")["engine_provenance_verified"])
        wrong = {"total_minor": 999, "currency": "AED", "invoice_count": 1}
        run["result"]["output"] = copy.deepcopy(wrong)
        run["output"] = copy.deepcopy(wrong)
        run["run_data"]["Result"][0]["data"]["main"][0][0]["json"]["output"] = copy.deepcopy(wrong)
        with self.assertRaisesRegex(Rejected, "Wrong invoice result|Result differs") as rejected:
            check_execution("invoice-total", inputs, run, "live")
        with tempfile.TemporaryDirectory() as directory:
            evidence = Path(directory) / "evidence/cases/original/case.json"
            evidence.parent.mkdir(parents=True)
            evidence.write_text(json.dumps(run))
            before = evidence.read_bytes()
            identity = evaluator_identity(invoice_fixture())
            files = {"original": {"case.json": hashlib.sha256(before).hexdigest()}}
            recorded = Recorded(Path(directory) / "evidence", Path(directory) / "evaluation", files, identity)
            recorded.accept("original", False, str(rejected.exception))
            decision = json.loads((Path(directory) / "evaluation/cases/original/acceptance.json").read_text())
            # The decision is its own document; the recorded execution is untouched.
            self.assertEqual(evidence.read_bytes(), before)
            self.assertEqual(sorted(p.name for p in evidence.parent.iterdir()), ["case.json"])
        persisted = json.loads(before)
        self.assertTrue(persisted["execution"]["succeeded"])
        self.assertEqual(persisted["acceptance"], {"status": "not_evaluated", "passed": None})
        self.assertEqual(decision["status"], "rejected")
        self.assertFalse(decision["passed"])
        self.assertEqual(decision["evidence"]["case.json"], hashlib.sha256(before).hexdigest())
        self.assertEqual(decision["evaluator_sha256"], identity["sources_sha256"])
        self.assertEqual(persisted["llm"]["selected_mode"], "live")
        self.assertEqual(persisted["llm"]["agency_http_call_count"], 0)

    def test_corruption_probes_still_reject_output_trace_and_missing_native_result(self):
        inputs, run = invoice_record()
        self.assertEqual(
            corruption_checks("invoice-total", inputs, run, "live"),
            [
                "wrong-final-output",
                "output-without-real-result-node",
                "falsified-step-trace",
            ],
        )

    def test_new_reports_require_explicit_matching_native_engine(self):
        for corrupt in [
            lambda run: run["execution"]["engine"].update(name="other"),
            lambda run: run["evidence"].update(engine=None),
            lambda run: run["evidence"]["engine"].update(kind="other"),
            lambda run: run["evidence"]["engine"].update(source="workflow_authored"),
            lambda run: run.update(report_schema="unknown"),
        ]:
            inputs, run = invoice_record()
            corrupt(run)
            with self.subTest(run=run["evidence"]), self.assertRaises(Rejected):
                check_execution("invoice-total", inputs, run, "live")
            with self.assertRaises(Rejected):
                check_rejection(run, {}, {})

    def test_legacy_reports_keep_their_strict_provenance_path(self):
        inputs, run = invoice_record()
        for field in ["report_schema", "execution", "evidence"]:
            run.pop(field)
        self.assertTrue(check_execution("invoice-total", inputs, run, "live")["output_verified"])
        run["persisted_status"] = "error"
        with self.assertRaisesRegex(Rejected, "persisted execution"):
            check_execution("invoice-total", inputs, run, "live")

    def test_native_timing_constraints_survive_verifier_split(self):
        inputs, run = invoice_record()
        run["run_data"]["total"][0]["startTime"] = 0
        with self.assertRaisesRegex(Rejected, "began before"):
            check_execution("invoice-total", inputs, run, "live")
