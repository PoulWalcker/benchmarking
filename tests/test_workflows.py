"""Behavioral checks of the profile and generated JS; these do not execute n8n."""

import copy
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

from sapi_config_lab import profile
from sapi_config_lab.compile import n8n as compiler
from sapi_config_lab.paths import workspace_root
from tests.support.invoice import CATALOG, OPERATION_SOURCE

ROOT = workspace_root()


class LabTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        support = ROOT / "tests/support"
        cls.bindings = profile.read_bindings(CATALOG)
        cls.bindings.update(profile.read_bindings(support / "refinement/bindings.yaml"))
        cls.bindings.update(profile.read_bindings(support / "admission/bindings.yaml"))
        conditional = profile.read(support / "graphs/guarded.yaml")
        conditional["workflow"]["steps"] = conditional["workflow"]["steps"][:4]
        conditional["workflow"]["dependencies"] = conditional["workflow"]["dependencies"][:4]
        conditional["workflow"]["output"] = {"ref": "steps.select"}
        cls.configs = {
            "invoice": profile.read(ROOT / "tasks/invoice-total/solution/config.yaml"),
            "conditional": conditional,
            "join": profile.read(support / "graphs/branch.yaml"),
            "repeated": profile.read(support / "graphs/repeated.yaml"),
            "guarded": profile.read(support / "graphs/guarded.yaml"),
            "refinement": profile.read(support / "refinement/config.yaml"),
            "lifecycle": profile.read(support / "admission/config.yaml"),
        }

    def run_config(self, name, inputs=None, mutate_export=None):
        artifact, _ = compiler.compile_n8n(
            copy.deepcopy(self.configs[name]), self.bindings, operation_source=OPERATION_SOURCE
        )
        if mutate_export:
            mutate_export(artifact)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "workflow.json"
            path.write_text(json.dumps(artifact))
            cmd = ["node", str(ROOT / "tests/support/run-export.mjs"), str(path)]
            if inputs is not None:
                path_input = Path(tmp) / "input.json"
                path_input.write_text(json.dumps(inputs))
                cmd.append(str(path_input))
            run = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        if run.returncode:
            raise RuntimeError(run.stderr)
        return json.loads(run.stdout)

    def test_all_configs_pass_profile_checks(self):
        for cfg in self.configs.values():
            with self.subTest(workflow=cfg["workflow"]["id"]):
                profile.validate(cfg, self.bindings)

    def test_invoice_fixture_counts_each_invoice_once(self):
        result = self.run_config("invoice")
        self.assertEqual(result["output"], {"total_minor": 38000, "currency": "AED", "invoice_count": 3})
        self.assertTrue(result["simulation"])
        self.assertEqual([e["step_id"] for e in result["trace"]], ["validate", "total", "report"])

    def test_invoice_new_input_and_zero_amount(self):
        result = self.run_config(
            "invoice",
            {
                "invoices": [
                    {"id": "A", "amount_minor": 0, "currency": "USD"},
                    {"id": "B", "amount_minor": 101, "currency": "USD"},
                ]
            },
        )
        self.assertEqual(result["output"], {"total_minor": 101, "currency": "USD", "invoice_count": 2})

    def test_invoice_rejects_invalid_batches(self):
        valid = {"id": "A", "amount_minor": 100, "currency": "AED"}
        cases = [
            ([], "nonempty"),
            ([valid, valid], "Duplicate"),
            ([valid, {**valid, "id": "B", "currency": "USD"}], "Mixed"),
            ([{**valid, "amount_minor": -1}], "Invalid amount"),
            ([{**valid, "amount_minor": 0.5}], "Invalid amount"),
            ([{**valid, "amount_minor": 9007199254740991}, {**valid, "id": "B"}], "safe integer"),
        ]
        for invoices, message in cases:
            with self.subTest(message=message), self.assertRaisesRegex(RuntimeError, message):
                self.run_config("invoice", {"invoices": invoices})

    def test_conditional_guards_complete_one_branch_and_skip_the_other(self):
        for high, days in ((False, 0), (True, 3)):
            with self.subTest(high=high):
                result = self.run_config(
                    "conditional", {"ticket": {"id": "T-new", "text": "sample", "days_overdue": days}}
                )
                states = {event["step_id"]: event["status"] for event in result["trace"]}
                selected = "escalate" if high else "normal"
                skipped = "normal" if high else "escalate"
                self.assertEqual(states[selected], "completed")
                self.assertEqual(states[skipped], "skipped")
                self.assertEqual(states["select"], "completed")
                self.assertEqual(result["output"], result["steps"][selected])
                self.assertNotIn(skipped, result["steps"])

    def test_live_input_schema_rejects_invalid_value_before_dispatch(self):
        step = copy.deepcopy(self.configs["join"]["workflow"]["steps"][0])
        step["with"]["material"] = 7
        binding = self.bindings[step["uses"]]
        ctx = {"inputs": {}, "steps": {}, "statuses": {}, "events": {}}
        body = f"const step = {json.dumps(step)}, ctx = {json.dumps(ctx)}, binding = {json.dumps(binding)};\n"
        body += "prepareAgency(step, ctx, binding, 'test/1'); console.log('null');"
        with self.assertRaisesRegex(RuntimeError, "Schema violation at inputs.material"):
            self.runtime_js(body)

    def test_research_join_preserves_both_sources(self):
        result = self.run_config(
            "join", {"product_material": "PRODUCT_SENTINEL", "marketing_material": "MARKETING_SENTINEL"}
        )
        self.assertEqual(result["output"]["evidence"], ["PRODUCT_SENTINEL", "MARKETING_SENTINEL"])
        self.assertEqual(result["output"]["report"], "Product: PRODUCT_SENTINEL\n\nMarketing: MARKETING_SENTINEL")
        self.assertEqual(
            {e["actor"] for e in result["trace"] if e["implementation"] == "stub"},
            {"product-sapi", "marketing-sapi", "writer-sapi"},
        )

    def test_research_output_independent_of_root_order(self):
        def reverse(artifact):
            artifact["nodes"].reverse()
            artifact["connections"]["Fixture"]["main"][0].reverse()

        self.assertEqual(self.run_config("join")["output"], self.run_config("join", mutate_export=reverse)["output"])

    def test_missing_join_input_is_not_an_optional_skip(self):
        def drop_branch(artifact):
            artifact["connections"]["marketing [LLM STUB]"]["main"][0] = []

        with self.assertRaisesRegex(RuntimeError, "Unavailable reference: steps.marketing"):
            self.run_config("join", mutate_export=drop_branch)

    def test_missing_input_fails_instead_of_producing_result(self):
        with self.assertRaisesRegex(RuntimeError, "Unavailable reference: inputs.invoices"):
            self.run_config("invoice", {})

    def test_missing_conditional_branch_token_is_an_error(self):
        def drop_branch(artifact):
            artifact["connections"]["normal"]["main"][0] = []

        with self.assertRaisesRegex(RuntimeError, "Unavailable reference: steps.normal"):
            self.run_config("conditional", mutate_export=drop_branch)

    def test_pipeline_forbids_llm(self):
        cfg = copy.deepcopy(self.configs["conditional"])
        cfg["workflow"]["kind"] = "Pipeline"
        with self.assertRaisesRegex(profile.Invalid, "Pipeline cannot include LLM"):
            profile.validate(cfg, self.bindings)

    def test_cycle_is_rejected(self):
        cfg = copy.deepcopy(self.configs["invoice"])
        cfg["workflow"]["dependencies"].append(["report", "validate"])
        with self.assertRaisesRegex(profile.Invalid, "Cyclic"):
            profile.validate(cfg, self.bindings)

    def test_references_require_existing_ancestor_and_field(self):
        for ref, message in [
            ("steps.unknown", "Missing producer"),
            ("steps.report", "not an upstream"),
            ("steps.validate.nonexistent", "Unknown output"),
        ]:
            cfg = copy.deepcopy(self.configs["invoice"])
            cfg["workflow"]["steps"][1]["with"]["invoices"] = {"ref": ref}
            with self.subTest(ref=ref), self.assertRaisesRegex(profile.Invalid, message):
                profile.validate(cfg, self.bindings)

    def test_conditional_output_needs_explicit_optional_reference(self):
        cfg = copy.deepcopy(self.configs["conditional"])
        cfg["workflow"]["steps"][-1]["with"]["normal"] = {"ref": "steps.normal"}
        with self.assertRaisesRegex(profile.Invalid, "conditional output requires optional_ref"):
            profile.validate(cfg, self.bindings)

    def test_actor_operation_restriction(self):
        cfg = copy.deepcopy(self.configs["join"])
        cfg["workflow"]["steps"][0]["actor"] = "writer-sapi"
        with self.assertRaisesRegex(profile.Invalid, "unauthorized actor"):
            profile.validate(cfg, self.bindings)

    def test_duplicate_ids_and_keys_are_rejected(self):
        cfg = copy.deepcopy(self.configs["invoice"])
        cfg["workflow"]["steps"].append(copy.deepcopy(cfg["workflow"]["steps"][0]))
        with self.assertRaisesRegex(profile.Invalid, "duplicate step"):
            profile.validate(cfg, self.bindings)
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "duplicate.yaml"
            p.write_text("schema: first\nschema: second\n")
            with self.assertRaisesRegex(profile.Invalid, "Duplicate YAML key"):
                profile.read(p)

    def test_unknown_operation_is_rejected(self):
        cfg = copy.deepcopy(self.configs["invoice"])
        cfg["workflow"]["steps"][0]["uses"] = "invented.automatic_magic"
        with self.assertRaisesRegex(profile.Invalid, "Unknown operation"):
            profile.validate(cfg, self.bindings)

    def test_activation_requires_exact_revision(self):
        cfg = copy.deepcopy(self.configs["invoice"])
        cfg["activation"]["workflow_ref"]["revision"] = 2
        with self.assertRaisesRegex(profile.Invalid, "another revision"):
            profile.validate(cfg, self.bindings)

    def test_refinement_must_be_bounded(self):
        cfg = copy.deepcopy(self.configs["refinement"])
        cfg["execution"]["refinement"]["max_attempts"] = 0
        with self.assertRaisesRegex(profile.Invalid, "Invalid refinement limit"):
            profile.validate(cfg, self.bindings)

    def test_unsupported_semantics_rejected_by_backend(self):
        with self.assertRaisesRegex(profile.Unsupported, "E_LIFECYCLE"):
            compiler.compile_n8n(
                self.configs["lifecycle"],
                self.bindings,
                operation_source=(ROOT / "tests/support/admission/operations.js").read_text(),
            )
        cfg = copy.deepcopy(self.configs["join"])
        cfg["execution"]["concurrency"] = "required_parallel"
        with self.assertRaisesRegex(profile.Unsupported, "E_PARALLEL"):
            compiler.compile_n8n(cfg, self.bindings, operation_source=OPERATION_SOURCE)

    def runtime_js(self, body):
        """Exercise envelope contracts locally, without claiming n8n execution."""
        source = OPERATION_SOURCE + "\n" + (compiler.RESOURCES / "runtime-fragment.js").read_text() + "\n" + body
        run = subprocess.run(["node", "-e", source], capture_output=True, text=True, timeout=10)
        if run.returncode:
            raise RuntimeError(run.stderr)
        return json.loads(run.stdout)

    def test_live_guard_skips_without_resolving_inputs_or_call_request(self):
        step = {
            "id": "conditional",
            "kind": "LLM",
            "uses": "research.product",
            "when": {"ref": "inputs.enabled", "eq": True},
            "with": {"material": {"ref": "inputs.missing"}},
        }
        ctx = {"inputs": {"enabled": False}, "steps": {}, "statuses": {}, "events": {}}
        binding = self.bindings["research.product"]
        body = f"const step = {json.dumps(step)}, ctx = {json.dumps(ctx)}, binding = {json.dumps(binding)};\n"
        body += "const prepared = prepareAgency(step, ctx, binding, 'test/1'); const restored = restoreAgency(step, prepared, null, binding); console.log(JSON.stringify({prepared, restored}));"
        result = self.runtime_js(body)
        self.assertFalse(result["prepared"]["should_run"])
        self.assertIsNone(result["prepared"]["request"])
        self.assertEqual(result["restored"]["statuses"], {"conditional": "skipped"})
        self.assertEqual(result["restored"]["steps"], {})

    def test_live_response_restores_envelope_and_checks_contract(self):
        step = self.configs["conditional"]["workflow"]["steps"][0]
        binding = self.bindings["ticket.classify"]
        ctx = {
            "inputs": self.configs["conditional"]["workflow"]["inputs"],
            "steps": {"earlier": {"value": 7}},
            "statuses": {"earlier": "completed"},
            "events": {"earlier": {"step_id": "earlier"}},
        }
        valid = {
            "invocation_id": "test/1",
            "status": "completed",
            "output": {"category": "delivery", "priority": "high"},
        }
        preamble = f'const step = {json.dumps(step)}, binding = {json.dumps(binding)}, ctx = {json.dumps(ctx)}; const prepared = prepareAgency(step, ctx, binding, "test/1");\n'
        result = self.runtime_js(
            preamble + f"console.log(JSON.stringify(restoreAgency(step, prepared, {json.dumps(valid)}, binding)));"
        )
        self.assertEqual(result["inputs"], ctx["inputs"])
        self.assertEqual(result["steps"]["earlier"], ctx["steps"]["earlier"])
        self.assertEqual(result["steps"]["classify"], valid["output"])
        self.assertEqual(result["events"]["classify"]["invocation_id"], "test/1")
        bad_responses = [
            ({**valid, "invocation_id": "wrong"}, "invocation ID mismatch"),
            ({**valid, "status": "failed"}, "did not complete"),
            ({**valid, "output": {"category": "delivery", "priority": "urgent"}}, "Schema violation"),
            ({**valid, "output": {"category": "delivery", "priority": 1}}, "Schema violation"),
            ({**valid, "output": {"category": "delivery"}}, "Output fields differ"),
            ({**valid, "steps": {"injected": {}}}, "Unexpected Agency response fields"),
        ]
        for response, message in bad_responses:
            with self.subTest(response=response), self.assertRaisesRegex(RuntimeError, message):
                self.runtime_js(
                    preamble
                    + f"console.log(JSON.stringify(restoreAgency(step, prepared, {json.dumps(response)}, binding)));"
                )

    def test_nested_live_output_contract_rejects_invalid_evidence(self):
        schema = self.bindings["research.write"]["output_schema"]
        for evidence in ([], ["only one"], ["valid", 7], ["first", "second", "third"]):
            with self.subTest(evidence=evidence), self.assertRaisesRegex(RuntimeError, "Schema violation"):
                self.runtime_js(
                    f'validateSchema({json.dumps({"report": "report", "evidence": evidence})}, {json.dumps(schema)}); console.log("null");'
                )

    def test_live_compiler_emits_exclusive_guard_and_fail_closed_transport(self):
        cfg = copy.deepcopy(self.configs["join"])
        cfg["workflow"]["inputs"]["enabled"] = False
        cfg["workflow"]["steps"][0]["when"] = {"ref": "inputs.enabled", "eq": True}
        cfg["workflow"]["steps"][2]["with"]["product"] = {"optional_ref": "steps.product"}
        artifact, mapping = compiler.compile_n8n(
            cfg, self.bindings, llm_mode="live", bridge_url="http://bridge:18765", operation_source=OPERATION_SOURCE
        )
        nodes = {n["name"]: n for n in artifact["nodes"]}
        self.assertEqual(mapping["product"], "product [LLM LIVE]")
        self.assertEqual(artifact["connections"]["Guard product"]["main"][1][0]["node"], mapping["product"])
        self.assertEqual(artifact["connections"]["Guard product"]["main"][0][0]["node"], "Agency product")
        transport = nodes["Agency product"]
        self.assertEqual(transport["type"], "n8n-nodes-base.httpRequest")
        self.assertEqual(transport["parameters"]["url"], "http://bridge:18765/v1/agency/execute")
        self.assertEqual(transport["parameters"]["options"]["timeout"], 120000)
        self.assertFalse(transport["parameters"]["options"]["response"]["response"]["neverError"])
        self.assertFalse(transport.get("continueOnFail", False))
        self.assertFalse(transport.get("retryOnFail", False))
        self.assertIn("exactly one response item", nodes[mapping["product"]]["parameters"]["jsCode"])

    def test_live_requires_explicit_deployment_and_supported_catalog_schema(self):
        for bridge_url in (None, "file:///tmp/bridge", "http://user:secret@bridge", "http://bridge?token=secret"):
            with self.subTest(url=bridge_url), self.assertRaises(profile.Invalid):
                compiler.compile_n8n(
                    self.configs["conditional"],
                    self.bindings,
                    llm_mode="live",
                    bridge_url=bridge_url,
                    operation_source=OPERATION_SOURCE,
                )
        bindings = copy.deepcopy(self.bindings)
        bindings["ticket.classify"]["output_schema"]["patternProperties"] = {}
        with self.assertRaisesRegex(profile.Invalid, "unsupported schema keywords"):
            compiler.compile_n8n(self.configs["conditional"], bindings, operation_source=OPERATION_SOURCE)
        bindings = copy.deepcopy(self.bindings)
        bindings["ticket.classify"].pop("output_schema")
        with self.assertRaisesRegex(profile.Invalid, "output_schema required"):
            compiler.compile_n8n(self.configs["conditional"], bindings, operation_source=OPERATION_SOURCE)


if __name__ == "__main__":
    unittest.main(verbosity=2)
