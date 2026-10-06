"""Exercise the common task seam without paid models or Docker."""

from pathlib import Path
from types import SimpleNamespace
import unittest
from sapi_config_lab.coordinate.benchmark_tasks import TASKS, task_definition
from sapi_config_lab.coordinate.benchmark import oracle_config, terminal_submission
from sapi_config_lab.contracts import CompileOptions
from sapi_config_lab.coordinate.backend import N8nBackend
from sapi_config_lab.profile import read_bindings

ROOT = Path(__file__).resolve().parents[1]


class CommonTaskTests(unittest.TestCase):
    def test_both_oracles_compile_using_the_same_backend(self):
        for task in TASKS.values():
            contract = SimpleNamespace(package={"definition": {"id": task.challenge_id}})
            compiled = N8nBackend().compile(
                oracle_config(contract),
                read_bindings(ROOT / task.catalog),
                CompileOptions(operation_url="http://tools/tools"),
            )
            self.assertEqual(compiled.engine, "n8n")
            self.assertNotIn("not-persisted", str(compiled.document))

    def test_crm_result_does_not_require_checkout_artifact(self):
        record = {"status": "success", "output": {"final_answer": "actual receipts"}}
        reason, submission = terminal_submission(record, 1, "case", task=task_definition("crm"))
        self.assertEqual(reason, "completed")
        self.assertEqual(submission["artifacts"], [])
        self.assertEqual(terminal_submission(record, 1, "case")[0], "protocol_error")

    def test_retry_is_explicit_only_for_transient_update(self):
        bindings = read_bindings(ROOT / task_definition("crm").catalog)
        self.assertEqual(bindings["crm.update"]["max_attempts"], 2)
        for name in ["followup.create", "customer.send"]:
            self.assertEqual(bindings[name].get("max_attempts", 1), 1)

    def test_model_caps_are_task_owned_and_small(self):
        task = task_definition("crm-lead-qualification")
        self.assertEqual((task.authoring_attempts, task.runtime_model_cap, task.live_reference), (1, 1, False))


if __name__ == "__main__":
    unittest.main()
