"""Exported bounded refinement behavior; the JS probe is not native n8n evidence."""

import copy
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

from sapi_config_lab import profile
from sapi_config_lab.compile.n8n import compile_n8n
from tests.support.refinement import ROOT, check_refinement_history, definition


class RefinementTests(unittest.TestCase):
    def setUp(self):
        self.config = definition()
        self.bindings = profile.read_bindings(ROOT / "bindings.yaml")

    def run_export(self, config=None, mutate=None):
        document, mapping = compile_n8n(
            config or self.config, self.bindings, operation_source=(ROOT / "operations.js").read_text()
        )
        if mutate:
            mutate(document)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "workflow.json"
            path.write_text(json.dumps(document))
            run = subprocess.run(
                ["node", str(ROOT.parent / "run-refinement-export.mjs"), str(path)],
                capture_output=True,
                text=True,
                timeout=10,
            )
        self.assertEqual(run.returncode, 0, run.stderr)
        return json.loads(run.stdout), mapping

    def test_rejected_answer_and_actual_feedback_feed_next_native_attempt(self):
        original = copy.deepcopy(self.config)
        probe, mapping = self.run_export()
        self.assertIsNone(probe["error"])
        result = probe["result"]
        self.assertEqual(result["output"], {"value": 2})
        attempts = result["refinement"]["attempts"]
        self.assertEqual([a["accepted"] for a in attempts], [False, True])
        self.assertEqual(
            attempts[1]["runtime"],
            {"previous": {"value": 1}, "feedback": ["Increase value"]},
        )
        self.assertNotIn("Attempt 3 / draft [LLM STUB]", probe["executed"])
        self.assertEqual(mapping["attempt2:draft"], "Attempt 2 / draft [LLM STUB]")
        self.assertEqual(self.config, original)

    def test_first_acceptance_stops_before_any_later_attempt(self):
        self.config["workflow"]["inputs"]["target"] = 1
        probe, _ = self.run_export()
        self.assertEqual(probe["result"]["refinement"]["accepted_attempt"], 1)
        self.assertNotIn("Attempt 2 / Fixture", probe["executed"])

    def test_exhaustion_preserves_three_rejections_but_no_accepted_result(self):
        self.config["workflow"]["inputs"]["ceiling"] = 1
        probe, _ = self.run_export()
        self.assertIn("exhausted", probe["error"])
        self.assertIsNone(probe["result"])
        attempts = probe["checkpoints"][-1]["value"]["refinement"]["attempts"]
        self.assertEqual(len(attempts), 3)
        self.assertEqual([a["accepted"] for a in attempts], [False, False, False])
        self.assertEqual(attempts[2]["runtime"]["feedback"], ["Value exceeds ceiling"])

    def test_generic_lowering_also_handles_a_script_only_workflow(self):
        config = definition()
        config["workflow"]["steps"][0]["kind"] = "Script"
        self.bindings["probe.advance"]["kind"] = "Script"
        self.bindings["probe.advance"]["implementation"] = "local_js"
        probe, _ = self.run_export(config)
        self.assertIsNone(probe["error"])
        self.assertEqual(probe["result"]["output"], {"value": 2})
        self.assertEqual(probe["result"]["refinement"]["accepted_attempt"], 2)

    def test_reversed_declarations_preserve_dependency_order_in_recursive_compilation(self):
        self.config["workflow"]["steps"].reverse()
        probe, _ = self.run_export()
        self.assertIsNone(probe["error"])
        self.assertEqual(probe["result"]["refinement"]["accepted_attempt"], 2)
        self.assertEqual(probe["result"]["output"], {"value": 2})

    def test_boolean_until_does_not_accept_numeric_one_in_runtime_or_verification(self):
        policy = self.config["execution"]["refinement"]
        policy.update(max_attempts=1, until={"ref": "steps.draft.value", "eq": True})
        probe, _ = self.run_export()
        self.assertIn("exhausted", probe["error"])
        attempts = probe["checkpoints"][-1]["value"]["refinement"]["attempts"]
        self.assertFalse(attempts[0]["accepted"])
        self.assertTrue(check_refinement_history(self.config, attempts)["exhausted"])

    def test_total_deadline_prevents_first_operation_when_already_expired(self):
        def expire(document):
            fixture = next(n for n in document["nodes"] if n["name"] == "Fixture")
            fixture["parameters"]["jsCode"] = fixture["parameters"]["jsCode"].replace("Date.now() + 120000", "0")

        probe, _ = self.run_export(mutate=expire)
        self.assertEqual(probe["error"], "Workflow deadline exceeded")
        self.assertIsNone(probe["result"])
        self.assertEqual(probe["checkpoints"], [])

    def test_live_attempts_have_distinct_admission_ids_and_remaining_timeouts(self):
        graph, mapping = compile_n8n(
            self.config,
            self.bindings,
            llm_mode="live",
            bridge_url="http://127.0.0.1:8766",
            operation_source=(ROOT / "operations.js").read_text(),
        )
        nodes = {node["name"]: node for node in graph["nodes"]}
        for number in (1, 2, 3):
            source = nodes[f"Attempt {number} / Prepare draft"]["parameters"]["jsCode"]
            self.assertIn(f"numeric-refinement/r1/draft/attempt{number}/", source)
            restore = nodes[mapping[f"attempt{number}:draft"]]["parameters"]["jsCode"]
            self.assertIn(f'$("Attempt {number} / Prepare draft")', restore)
            self.assertIn(
                "deadline_at_ms", nodes[f"Attempt {number} / Agency draft"]["parameters"]["options"]["timeout"]
            )


if __name__ == "__main__":
    unittest.main()
