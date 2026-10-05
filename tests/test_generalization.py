"""Semantic acceptance over actual compiled JS probes; these are not n8n runs."""

import copy
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

from sapi_config_lab.paths import CATALOG, workspace_root
from sapi_config_lab.runtime.n8n.compiler import compile_demo
from sapi_config_lab.workflow import profile
from verification.contracts import Rejected
from verification.generalization import analyze, facts

ROOT = workspace_root()
DATA = ROOT / "generation/generalization"


class GeneralizationTests(unittest.TestCase):
    def setUp(self):
        self.cases = json.loads((DATA / "cases.json").read_text())

    def probe(self, config, case):
        candidate = copy.deepcopy(config)
        candidate["workflow"]["inputs"] = case["inputs"]
        graph, _ = compile_demo(candidate, profile.read_bindings(CATALOG))
        with tempfile.TemporaryDirectory() as temporary:
            artifact = Path(temporary) / "graph.json"
            artifact.write_text(json.dumps(graph))
            result = subprocess.run(
                ["node", str(ROOT / "tests/support/run-export.mjs"), str(artifact)],
                capture_output=True,
                text=True,
                timeout=10,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_distinct_graphs_inline_objects_and_redundant_edges_are_accepted(self):
        for path in sorted((DATA / "controls").glob("*.yaml")):
            config = profile.read(path)
            for case in self.cases[config["workflow"]["id"]]:
                with self.subTest(graph=path.name, case=case["name"]):
                    final = self.probe(config, case)
                    self.assertTrue(analyze(config, case, final["steps"])["semantic_origins_verified"])
        shared = profile.read(DATA / "controls/two-audience-briefs-shared.yaml")
        separate = profile.read(DATA / "controls/two-audience-briefs-separate.yaml")
        self.assertEqual(len(analyze(shared, self.cases["two-audience-briefs"][0])["model_occurrences"]), 5)
        self.assertEqual(len(analyze(separate, self.cases["two-audience-briefs"][0])["model_occurrences"]), 6)

    def test_correct_value_literals_cannot_replace_origin_links(self):
        config = profile.read(DATA / "controls/billing-bulletin-packet-inline.yaml")
        case = self.cases["billing-bulletin-packet"][0]
        final = self.probe(config, case)
        config["workflow"]["output"] = final["output"]
        with self.assertRaisesRegex(Rejected, "origins"):
            analyze(config, case, final["steps"])

    def test_sum_of_identical_literal_invoices_still_bypasses_validation(self):
        config = profile.read(DATA / "controls/billing-bulletin-packet-branches.yaml")
        case = self.cases["billing-bulletin-packet"][0]
        step = next(s for s in config["workflow"]["steps"] if s["uses"] == "invoices.sum")
        step["with"] = {"invoices": case["inputs"]["invoices"]}
        final = self.probe(config, case)
        self.assertEqual(final["output"]["billing"]["total_minor"], 1600)
        with self.assertRaisesRegex(Rejected, "validation"):
            analyze(config, case, final["steps"])

    def test_swapped_audiences_and_mixed_source_objects_are_rejected(self):
        config = profile.read(DATA / "controls/two-audience-briefs-shared.yaml")
        case = self.cases["two-audience-briefs"][0]
        config["workflow"]["output"] = {"cooperatives": {"ref": "steps.wk"}, "clinics": {"ref": "steps.wc"}}
        final = self.probe(config, case)
        with self.assertRaisesRegex(Rejected, "origins"):
            analyze(config, case, final["steps"])
        config = profile.read(DATA / "controls/two-audience-briefs-separate.yaml")
        next(s for s in config["workflow"]["steps"] if s["id"] == "mc")["with"] = {
            "material": {"ref": "inputs.product_material"}
        }
        with self.assertRaisesRegex(Rejected, "source role"):
            analyze(config, case)

    def test_unsupported_evidence_missing_fact_and_wrong_arithmetic_are_rejected(self):
        for task, file, occurrence, replacement in [
            ("billing-bulletin-packet", "branches", "s", {"amount_minor": 1601, "currency": "AED", "count": 2}),
            (
                "two-audience-briefs",
                "shared",
                "p",
                {"summary": "Offline inventory and CSV export.", "evidence": "Fabricated quote"},
            ),
            (
                "two-audience-briefs",
                "shared",
                "wc",
                {
                    "report": "Product supports offline inventory and CSV export.",
                    "evidence": [
                        "The product supports offline inventory and CSV export.",
                        "The service targets rural cooperatives through printed catalogues.",
                    ],
                },
            ),
        ]:
            config = profile.read(DATA / "controls" / f"{task}-{file}.yaml")
            case = self.cases[task][0]
            final = self.probe(config, case)
            final["steps"][occurrence] = replacement
            with self.subTest(task=task, occurrence=occurrence), self.assertRaises(Rejected):
                analyze(config, case, final["steps"])

    def test_extra_actor_permissions_reject_but_unused_model_work_is_reported(self):
        config = profile.read(DATA / "controls/two-audience-briefs-shared.yaml")
        case = self.cases["two-audience-briefs"][0]
        config["actors"]["product"]["allowed_operations"].append("research.write")
        with self.assertRaisesRegex(Rejected, "permissions"):
            analyze(config, case)
        config = profile.read(DATA / "controls/two-audience-briefs-shared.yaml")
        duplicate = copy.deepcopy(config["workflow"]["steps"][0])
        duplicate["id"] = "unused_product"
        config["workflow"]["steps"].append(duplicate)
        checked = analyze(config, case)
        self.assertEqual(checked["unused_model_occurrences"], ["unused_product"])

    def test_safe_guard_and_skipped_unused_model_preserve_business_values(self):
        config = profile.read(DATA / "controls/billing-bulletin-packet-branches.yaml")
        report = next(s for s in config["workflow"]["steps"] if s["id"] == "r")
        report["when"] = {"ref": "steps.p.mode", "eq": "preview"}
        report["join"] = "all_terminal"
        config["workflow"]["output"]["billing"] = {"optional_ref": "steps.r"}
        config["workflow"]["dependencies"].append(["p", "r"])
        case = self.cases["billing-bulletin-packet"][0]
        final = self.probe(config, case)
        self.assertEqual(analyze(config, case, final["steps"])["expected_output"], final["output"])
        config = profile.read(DATA / "controls/two-audience-briefs-shared.yaml")
        extra = copy.deepcopy(config["workflow"]["steps"][0])
        extra["id"] = "unused_product"
        extra["when"] = {"ref": "inputs.product_material", "eq": "never-matches-fixture"}
        config["workflow"]["steps"].insert(0, extra)
        config["workflow"]["dependencies"].append(["unused_product", "p"])
        case = self.cases["two-audience-briefs"][0]
        final = self.probe(config, case)
        self.assertEqual(final["statuses"]["unused_product"], "skipped")
        self.assertEqual(analyze(config, case, final["steps"])["expected_output"], final["output"])

    def test_common_paraphrase_passes_and_explicit_source_contradiction_rejects(self):
        anchors = self.cases["two-audience-briefs"][0]["anchors"]["product_material"]
        facts("Manage inventory offline and export CSV files.", anchors)
        with self.assertRaisesRegex(Rejected, "contradicted"):
            facts("The product does not support offline inventory or CSV export.", anchors)


if __name__ == "__main__":
    unittest.main()
