"""Role lineage: the public format's equivalent output forms bind, and failures keep their precise reason."""

import copy
import hashlib
import json
from typing import get_args
import unittest

import yaml

from sapi_config_lab import profile
from sapi_config_lab.paths import CATALOG, workspace_root
from sapi_config_lab.profile import read, read_bindings, validate
from verification import roles
from verification.contracts import Rejected
from verification.extensions import reply_roles
from verification.roles import OUTPUTS, resolve
from verification.roles import bind_roles as bind_selected_roles

ROOT = workspace_root()
# Generated attempt 2 of reports/20261006T185618Z-generation, rejected before this fix although the
# workflow was correct; attempt 3 differed only in step IDs and wording.
PROJECTED = ROOT / "tests/support/competitor-report-projected-output.yaml"
PROJECTED_SHA256 = "9f0802c34c543954b1fb9a5d975c2bad3d38692e03ae392f74767d602c6f3279"


def reference(name: str) -> dict:
    if name == "invoice-total":
        return read(ROOT / "benchmarks/01-invoice-total/config.yaml")
    filename = {
        "competitor-report": "branch",
        "dual-ledger-closeout": "repeated",
        "revise-answer": "refinement",
        "priority-support-brief": "guarded",
    }[name]
    return read(ROOT / "tests/support/graphs" / (filename + ".yaml"))


def bind_roles(scenario, config):
    if scenario == "invoice-total":
        return bind_selected_roles(scenario, config)
    filename = {"competitor-report": "branch", "dual-ledger-closeout": "repeated", "priority-support-brief": "guarded"}[
        scenario
    ]
    contract = json.loads((ROOT / "tests/support/graphs" / (filename + "-contract.json")).read_text())
    return bind_selected_roles(scenario, config, contract)


def fields(step: str, operation: str, ref: str = "ref") -> dict:
    return {field: {ref: f"steps.{step}.{field}"} for field in OUTPUTS[operation]}


def step(config: dict, operation: str) -> dict:
    return next(item for item in config["workflow"]["steps"] if item["uses"] == operation)


class EquivalentFormsTests(unittest.TestCase):
    def test_the_recorded_rejected_submission_now_binds(self):
        raw = PROJECTED.read_bytes()
        self.assertEqual(hashlib.sha256(raw).hexdigest(), PROJECTED_SHA256)
        config = yaml.safe_load(raw)
        validate(config, read_bindings(CATALOG))
        self.assertEqual(
            config["workflow"]["output"],
            {"report": {"ref": "steps.write_report.report"}, "evidence": {"ref": "steps.write_report.evidence"}},
        )
        self.assertEqual(
            bind_roles("competitor-report", config),
            {
                "product": "product_analysis",
                "marketing": "marketing_analysis",
                "combine": "combine",
                "write": "write_report",
            },
        )

    def test_projection_resolves_to_the_whole_closed_result(self):
        result = {"report": "R", "evidence": ["a", "b"]}
        steps, statuses = {"w": result}, {"w": "completed"}
        projected = resolve(fields("w", "research.write"), {}, steps, statuses)
        self.assertEqual(projected, resolve({"ref": "steps.w"}, {}, steps, statuses))

    def test_projected_step_argument_and_nested_output_bind(self):
        config = reference("competitor-report")
        combine = step(config, "research.combine")["id"]
        step(config, "research.write")["with"]["brief"] = fields(combine, "research.combine")
        validate(config, read_bindings(CATALOG))
        self.assertIn("write", bind_roles("competitor-report", config))
        packet = reference("dual-ledger-closeout")
        domestic = packet["workflow"]["output"]["domestic"]["ref"].split(".")[1]
        packet["workflow"]["output"]["domestic"] = fields(domestic, "invoices.report")
        self.assertIn("domestic_report", bind_roles("dual-ledger-closeout", packet))

    def test_refinement_accepts_the_projected_draft(self):
        config = reference("revise-answer")
        draft = step(config, "reply.generate")["id"]
        config["workflow"]["output"] = fields(draft, "reply.generate")
        step(config, "reply.check")["with"]["draft"] = fields(draft, "reply.generate")
        self.assertEqual(reply_roles(config)[0], draft)


class NonEquivalentFormsTests(unittest.TestCase):
    def assert_code(self, code: str, scenario: str, config: dict) -> Rejected:
        with self.assertRaises(Rejected) as raised:
            bind_roles(scenario, config)
        self.assertEqual(raised.exception.code, code, str(raised.exception))
        return raised.exception

    def test_partial_renamed_or_mixed_projections_stay_rejected(self):
        config = yaml.safe_load(PROJECTED.read_bytes())
        partial = copy.deepcopy(config)
        del partial["workflow"]["output"]["evidence"]
        renamed = copy.deepcopy(config)
        renamed["workflow"]["output"]["text"] = renamed["workflow"]["output"].pop("report")
        mixed = copy.deepcopy(config)
        mixed["workflow"]["output"]["evidence"] = {"ref": "steps.combine.product"}
        for broken in (partial, renamed, mixed):
            error = self.assert_code("wrong_role_output_lineage", "competitor-report", broken)
            self.assertIn("contract requires", str(error))

    def test_optional_projection_is_not_a_skippable_whole_result(self):
        # A skipped writer makes {optional_ref: steps.W} null but this projection {report: null, evidence: null}.
        config = reference("priority-support-brief")
        writer = step(config, "research.write")["id"]
        config["workflow"]["output"]["brief"] = fields(writer, "research.write", "optional_ref")
        self.assert_code("wrong_role_output_lineage", "priority-support-brief", config)


class DiagnosticTests(unittest.TestCase):
    def rejected(self, scenario: str, config: dict) -> Rejected:
        with self.assertRaises(Rejected) as raised:
            bind_roles(scenario, config)
        return raised.exception

    def test_wrong_actor_names_the_step_and_both_actors(self):
        config = reference("competitor-report")
        config["actors"]["other-sapi"] = {"context_scope": "x", "allowed_operations": ["research.write"]}
        writer = step(config, "research.write")
        writer["actor"] = "other-sapi"
        error = self.rejected("competitor-report", config)
        self.assertEqual(error.code, "wrong_role_actor")
        self.assertIn(f"step {writer['id']}", str(error))
        self.assertIn("'other-sapi'", str(error))
        self.assertIn("'writer-sapi'", str(error))

    def test_missing_operation_dependencies_and_inputs_are_named(self):
        config = reference("invoice-total")
        step(config, "invoices.sum")["kind"] = "LLM"
        self.assertEqual(self.rejected("invoice-total", config).code, "missing_role_operation")
        config = reference("invoice-total")
        config["workflow"]["dependencies"].pop()
        error = self.rejected("invoice-total", config)
        self.assertEqual(error.code, "wrong_role_dependencies")
        self.assertIn("('total', 'report')", str(error))
        config = reference("invoice-total")
        step(config, "invoices.sum")["with"] = {"invoices": {"ref": "inputs.invoices"}}
        error = self.rejected("invoice-total", config)
        self.assertEqual(error.code, "wrong_role_input_lineage")
        self.assertIn("inputs.invoices", str(error))

    def test_the_furthest_failing_assignment_explains_a_repeated_operation(self):
        # Six interchangeable ledger steps: only the output lineage is wrong, whichever assignment is tried.
        config = reference("dual-ledger-closeout")
        output = config["workflow"]["output"]
        output["domestic"], output["export"] = output["export"], output["domestic"]
        self.assertEqual(self.rejected("dual-ledger-closeout", config).code, "wrong_role_output_lineage")

    def test_the_restated_step_kinds_match_the_profile(self):
        self.assertEqual(get_args(roles.StepKind), get_args(profile.StepKind))

    def test_the_operation_table_matches_the_catalog(self):
        catalog = read_bindings(CATALOG)
        for operation, declared in OUTPUTS.items():
            self.assertEqual(list(declared), catalog[operation]["outputs"], operation)


if __name__ == "__main__":
    unittest.main()
