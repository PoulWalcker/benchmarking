"""Occurrence and topology controls; fabricated evidence does not execute n8n."""

import copy
import unittest

from sapi_config_lab.paths import workspace_root
from sapi_config_lab.profile import Invalid, read, read_bindings, validate
from verification.contracts import Rejected
from verification.verify import invalid_configs

ROOT = workspace_root()


class OccurrenceTests(unittest.TestCase):
    def test_cycle_control_uses_graph_edges_when_steps_are_reordered(self):
        config = read(ROOT / "benchmarks/03-competitor-report/config.yaml")
        config["workflow"]["steps"].reverse()
        bad = dict(invalid_configs(config))["cycle"]
        with self.assertRaisesRegex(Invalid, "Cyclic|cycle|Cycle"):
            validate(bad, read_bindings(ROOT / "src/sapi_config_lab/bindings.yaml"))

    def test_role_binding_accepts_renamed_ids_but_rejects_wrong_input_origin(self):
        from verification.roles import bind_roles

        config = read(ROOT / "benchmarks/01-invoice-total/config.yaml")
        config["workflow"]["steps"].reverse()
        config = _rename(config, {"validate": "alpha", "total": "beta", "report": "gamma"})
        self.assertEqual(bind_roles("invoice-total", config), {"validate": "alpha", "total": "beta", "report": "gamma"})
        config["workflow"]["steps"][1]["with"] = {"invoices": {"ref": "inputs.invoices"}}
        with self.assertRaises(Rejected):
            bind_roles("invoice-total", config)


def _rename(value, names):
    if isinstance(value, dict):
        return {key: _rename(item, names) for key, item in value.items()}
    if isinstance(value, list):
        return [_rename(item, names) for item in value]
    if isinstance(value, str):
        if value in names:
            return names[value]
        if value.startswith("steps."):
            parts = value.split(".")
            parts[1] = names.get(parts[1], parts[1])
            return ".".join(parts)
    return copy.deepcopy(value)


class OccurrenceObservationTests(unittest.TestCase):
    def test_observation_keeps_step_identity_and_requires_each_native_occurrence(self):
        from tests.test_verification_contract import invoice_record
        from verification.n8n_provenance import observe_execution

        inputs, run = invoice_record()
        observation, records = observe_execution("invoice-total", inputs, run, "live")
        self.assertEqual(set(observation.events), {"validate", "total", "report"})
        self.assertEqual(observation.roles["total"], "total")
        self.assertEqual(set(records), {"validate", "total", "report"})

    def test_stub_record_with_unexpected_agency_call_is_rejected(self):
        from tests.test_verification_contract import invoice_record
        from verification.verify import check_execution

        inputs, run = invoice_record()
        for final in (run["result"], run["run_data"]["Result"][0]["data"]["main"][0][0]["json"]):
            final["llm_mode"] = "stub"
        run["run_data"]["Agency hidden"] = [copy.deepcopy(run["run_data"]["report"][0])]
        with self.assertRaisesRegex(Rejected, "Agency"):
            check_execution("invoice-total", inputs, run, "stub")


def ledger_record():
    """Small worked ledger example encoded as fabricated native envelopes."""
    from tests.test_verification_contract import invoice_record

    config = read(ROOT / "benchmarks/06-dual-ledger-closeout/config.yaml")
    inputs = {
        "domestic_invoices": [{"id": "D", "amount_minor": 7, "currency": "AED"}],
        "export_invoices": [{"id": "X", "amount_minor": 13, "currency": "EUR"}],
    }
    config["workflow"]["inputs"] = inputs
    values = {
        "domestic_validate": {"invoices": inputs["domestic_invoices"]},
        "domestic_sum": {"amount_minor": 7, "currency": "AED", "count": 1},
        "domestic_report": {"total_minor": 7, "currency": "AED", "invoice_count": 1},
        "export_validate": {"invoices": inputs["export_invoices"]},
        "export_sum": {"amount_minor": 13, "currency": "EUR", "count": 1},
        "export_report": {"total_minor": 13, "currency": "EUR", "invoice_count": 1},
    }
    _, run = invoice_record()
    data = {}
    envelopes = {}

    def native(name, rows, parents):
        data[name] = [
            {
                "startTime": len(data) * 10,
                "executionTime": 1,
                "source": [
                    {"previousNode": parent, "previousNodeRun": 0, "previousNodeOutput": 0} for parent in parents
                ],
                "data": {"main": [[{"json": copy.deepcopy(row)} for row in rows]]},
            }
        ]

    native("Demo start", [{}], [])
    blank = {"inputs": inputs, "events": {}, "statuses": {}, "steps": {}}
    native("Fixture", [blank], ["Demo start"])
    final = copy.deepcopy(blank)
    for step in config["workflow"]["steps"]:
        sid = step["id"]
        parent = next((first for first, second in config["workflow"]["dependencies"] if second == sid), None)
        envelope = copy.deepcopy(envelopes[parent] if parent else blank)
        event = {
            "step_id": sid,
            "operation": step["uses"],
            "actor": None,
            "implementation": "script",
            "status": "completed",
        }
        envelope["events"][sid] = event
        envelope["statuses"][sid] = "completed"
        envelope["steps"][sid] = values[sid]
        envelopes[sid] = envelope
        for key in ("events", "statuses", "steps"):
            final[key].update(envelope[key])
        native(sid, [envelope], [parent or "Fixture"])
    native(
        "Join final", [envelopes["domestic_report"], envelopes["export_report"]], ["domestic_report", "export_report"]
    )
    final.update(
        output={"domestic": values["domestic_report"], "export": values["export_report"]},
        trace=list(final.pop("events").values()),
        workflow_ref={"id": "dual-ledger-closeout", "revision": 1},
        spec_revision=config["spec_revision"],
        llm_mode="stub",
    )
    native("Result", [final], ["Join final"])
    run.update(
        result=copy.deepcopy(final),
        output=copy.deepcopy(final["output"]),
        run_data=data,
        mapping={step["id"]: step["id"] for step in config["workflow"]["steps"]},
    )
    return config, run


class RepeatedOperationTests(unittest.TestCase):
    def test_both_chains_are_observed_and_both_terminal_envelopes_required(self):
        from verification.verify import check_execution

        config, run = ledger_record()
        inputs = config["workflow"]["inputs"]
        result = check_execution("dual-ledger-closeout", inputs, run, config=config)
        self.assertEqual(result["operation_count"], 6)
        run["run_data"]["Join final"][0]["data"]["main"][0].pop()
        with self.assertRaisesRegex(Rejected, "input envelope"):
            check_execution("dual-ledger-closeout", inputs, run, config=config)

    def test_wrong_origin_swapped_outputs_and_duplicate_trace_fail_closed(self):
        from verification.verify import check_execution

        for change in ("origin", "outputs", "duplicate", "extra-data", "early-result", "lost-occurrence"):
            config, run = ledger_record()
            if change == "origin":
                config["workflow"]["steps"][3]["with"] = {"invoices": {"ref": "inputs.domestic_invoices"}}
            elif change == "outputs":
                config["workflow"]["output"]["domestic"], config["workflow"]["output"]["export"] = (
                    config["workflow"]["output"]["export"],
                    config["workflow"]["output"]["domestic"],
                )
            elif change == "duplicate":
                for final in (run["result"], run["run_data"]["Result"][0]["data"]["main"][0][0]["json"]):
                    final["trace"][-1] = copy.deepcopy(final["trace"][0])
            elif change == "extra-data":
                node = run["run_data"]["domestic_report"][0]["data"]["main"][0][0]["json"]
                node["steps"]["export_report"] = copy.deepcopy(run["result"]["steps"]["export_report"])
            elif change == "early-result":
                run["run_data"]["Result"][0]["startTime"] = 0
            else:
                run["run_data"].pop("export_validate")
            with self.subTest(change=change), self.assertRaises(Rejected):
                check_execution("dual-ledger-closeout", config["workflow"]["inputs"], run, config=config)

    def test_input_rejection_identifies_the_intended_repeated_operation(self):
        from verification.n8n_provenance import check_rejection

        config, run = ledger_record()
        run.update(status="error", error={"message": "duplicate invoice IDs"}, result_node_present=False)
        run["run_data"].pop("Result")
        case = {"role": "export_validate", "operation": "invoices.validate", "error": "duplicate invoice IDs"}
        run["run_data"]["domestic_validate"][0]["error"] = {"message": case["error"]}
        with self.assertRaisesRegex(Rejected, "Expected operation"):
            check_rejection(run, case, config)
        run["run_data"]["export_validate"][0]["error"] = run["run_data"]["domestic_validate"][0].pop("error")
        check_rejection(run, case, config)
