"""Evidence-based attribution of incomplete live fixture traces."""

import copy
from pathlib import Path
import unittest

from sapi_config_lab.paths import workspace_root

ROOT = workspace_root()
SOURCES = (
    (ROOT / "src/sapi_config_lab/compile/runtime-fragment.js").read_text(),
    (ROOT / "tasks/invoice-total/operations.js").read_text(),
)


class LiveAttributionTests(unittest.TestCase):
    def record(self, node="total", message="Invalid amount [line 42]", error="WrappedExecutionError"):
        return {
            "status": "error",
            "persisted_status": "error",
            "result_node_present": False,
            "acceptance": False,
            "mapping": {"total": "total"},
            "run_data": {
                "Fixture": [{"startTime": 0}],
                node: [{"startTime": 10, "error": {"name": error, "message": message}}],
            },
        }

    def graph(self, node):
        return {"nodes": [{"name": node, "type": "n8n-nodes-base.code"}]}

    def test_trusted_last_operation_error_is_a_proven_rejection(self):
        from sapi_config_lab.coordinate.live_evidence import attribute_incomplete

        outcome, reason = attribute_incomplete(self.record(), self.graph("total"), SOURCES)
        self.assertEqual(outcome, "rejected")
        self.assertEqual(reason, "trusted_deterministic_check")
        outcome, _ = attribute_incomplete(
            self.record("Prepare total", "Schema violation at inputs.amount: minimum [line 8]"),
            self.graph("Prepare total"),
            SOURCES,
        )
        self.assertEqual(outcome, "rejected")

    def test_infrastructure_faults_are_never_candidate_rejections(self):
        from sapi_config_lab.coordinate.live_evidence import attribute_incomplete

        for node, error in (
            ("Agency total", "NodeApiError"),
            ("total [LLM LIVE]", "WrappedExecutionError"),
            ("Fixture", "WrappedExecutionError"),
            ("Demo start", "WrappedExecutionError"),
            ("Guard total", "WrappedExecutionError"),
            ("total", "NodeApiError"),
        ):
            with self.subTest(node=node, error=error):
                outcome, reason = attribute_incomplete(self.record(node, error=error), self.graph(node), SOURCES)
                self.assertEqual(outcome, "infrastructure")
                self.assertTrue(reason)
        for status in ("engine_error", "import_error", "compile_error"):
            record = {**self.record(), "status": status}
            with self.subTest(status=status):
                self.assertEqual(attribute_incomplete(record, self.graph("total"), SOURCES)[0], "infrastructure")
        for missing in ("persisted_status", "run_data"):
            record = self.record()
            record.pop(missing)
            with self.subTest(missing=missing):
                self.assertEqual(attribute_incomplete(record, self.graph("total"), SOURCES)[0], "infrastructure")

    def test_unproven_errors_stop_with_truthful_reasons(self):
        from sapi_config_lab.coordinate.live_evidence import attribute_incomplete

        variants = [
            (self.record(message="Workflow deadline exceeded [line 76]"), "deadline"),
            (self.record(message="Cannot read properties of undefined", error="TypeError"), "untrusted_node_error"),
            (self.record(error="Error"), "untrusted_node_error"),
            ({**self.record(), "acceptance": True}, "incomplete_accepted_verdict"),
            (
                {**self.record(), "persisted_status": "canceled", "run_data": {"Fixture": [{"startTime": 0}]}},
                "incomplete_execution",
            ),
        ]
        multiple = self.record()
        multiple["run_data"]["Prepare total"] = copy.deepcopy(multiple["run_data"]["total"])
        variants.append((multiple, "ambiguous_node_errors"))
        earlier = self.record()
        earlier["run_data"]["Join final"] = [{"startTime": 20}]
        variants.append((earlier, "error_not_last_execution"))
        for record, reason in variants:
            with self.subTest(reason=reason):
                self.assertEqual(attribute_incomplete(record, self.graph("total"), SOURCES), ("unverified", reason))

    def test_prefix_trust_is_limited_to_runtime_check_message_prefixes(self):
        from sapi_config_lab.coordinate.live_evidence import attribute_incomplete

        for message in (
            "[unexplained runtime failure",
            ".unexplained runtime failure",
            "Product: arbitrary runtime failure",
        ):
            with self.subTest(message=message):
                record = self.record(message=message)
                sources = (SOURCES[0], (ROOT / "tasks/research-report/operations.js").read_text())
                self.assertEqual(attribute_incomplete(record, self.graph("total"), sources)[0], "unverified")


class NativeCollectionTests(unittest.TestCase):
    def setUp(self):
        import tempfile

        from sapi_config_lab.contracts import CompileOptions
        from sapi_config_lab.coordinate.backend import N8nBackend
        from sapi_config_lab.coordinate.native_tasks import invoke
        from sapi_config_lab.coordinate.provenance import source_manifest
        from sapi_config_lab.evidence import digest, sha256, write_json
        from sapi_config_lab.profile import read_bindings
        from verification.verify import inventory

        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.verifier = Path(temporary.name) / "verifier"
        self.task = ROOT / "tasks/ticket-routing"
        self.submission = self.task / "solution/config.yaml"
        self.sources = source_manifest()
        self.options = {"mode": "live", "selected_case": "high-priority", "judge_model": None}
        bundle = invoke(
            self.task, {"action": "plan", "submission": str(self.submission), "options": self.options}, self.sources
        )
        self.plan = bundle["plan"]
        self.bridge = "http://host:18765"
        compiled = N8nBackend(operation_source=(self.task / "operations.js").read_text()).compile(
            self.plan["entries"][0]["config"],
            read_bindings(self.task / "bindings.yaml"),
            CompileOptions("live", self.bridge),
        )
        self.graph = {**compiled.document, "id": "wf", "active": False}
        self.run = {
            "status": "error",
            "persisted_status": "error",
            "result_node_present": False,
            "workflow_id": "wf",
            "execution_id": "1",
            "mapping": compiled.mapping,
            "run_data": {
                "Prepare classify": [
                    {
                        "startTime": 1,
                        "error": {
                            "name": "WrappedExecutionError",
                            "message": "Schema violation at inputs.ticket: missing id [line 65]",
                        },
                    }
                ]
            },
        }
        self.artifact = self.verifier / "evidence/cases/high-priority"
        self.artifact.mkdir(parents=True)
        write_json(self.artifact / "workflow.json", self.graph)
        self.run["workflow_sha256"] = sha256(self.artifact / "workflow.json")
        write_json(self.artifact / "case.json", self.run)
        write_json(self.artifact / "mapping.json", compiled.mapping)
        for name in ("import.log", "execution.stdout.log", "execution.stderr.log"):
            (self.artifact / name).write_text("")
        (self.verifier / "evidence/submission.yaml").write_bytes(self.submission.read_bytes())
        write_json(
            self.verifier / "native-task.json",
            {
                "schema": "sapi-lab-native-task/v1",
                "name": self.task.name,
                "sources": self.sources,
                "options": bundle["options"],
                "options_sha256": digest(bundle["options"]),
                "submission_sha256": sha256(self.submission),
            },
        )
        write_json(
            self.verifier / "evidence/observation.json",
            {
                "plan_sha256": digest(self.plan),
                "entries": [{"name": "high-priority", "files": inventory(self.artifact)}],
            },
        )

    def collect(self):
        from sapi_config_lab.coordinate.live_evidence import collect_native
        from sapi_config_lab.evidence import sha256

        return collect_native(
            [{"task_name": self.task.name, "result_path": str(self.verifier.parent / "result.json")}],
            {self.task.name: {"path": self.submission, "sha256": sha256(self.submission)}},
            {self.task.name: {"high-priority"}},
            self.bridge,
            {self.task.name: self.task},
        )

    def test_incomplete_native_trace_preserves_integrity_and_returns_case_attribution_inputs(self):
        collected = self.collect()
        self.assertEqual(len(collected), 1)
        self.assertEqual(
            (collected[0]["trace"], collected[0]["calls"], collected[0]["status"]), ("incomplete", [], "error")
        )
        self.assertEqual(collected[0]["failed_node"][0]["node"], "Prepare classify")
        (self.artifact / "import.log").write_text("tampered")
        with self.assertRaisesRegex(ValueError, "files changed"):
            self.collect()

    def test_partial_native_call_keeps_identity_timing_and_compiled_graph_proof(self):
        from sapi_config_lab.evidence import digest, write_json
        from tests.test_n8n_evidence import NativeLiveEvidenceTests
        from verification.verify import inventory

        live = NativeLiveEvidenceTests().live_record()
        final = live["run_data"].pop("Result")[0]["data"]["main"][0][0]["json"]
        restore = live["run_data"]["classify [LLM LIVE]"][0]["data"]["main"][0][0]["json"]
        restore["workflow_ref"] = final["workflow_ref"]
        self.run["run_data"] = live["run_data"]
        self.run["run_data"]["select"] = [
            {
                "startTime": 100,
                "error": {
                    "name": "WrappedExecutionError",
                    "message": "Exactly one routing branch must complete [line 13]",
                },
            }
        ]
        write_json(self.artifact / "case.json", self.run)
        write_json(
            self.verifier / "evidence/observation.json",
            {
                "plan_sha256": digest(self.plan),
                "entries": [{"name": "high-priority", "files": inventory(self.artifact)}],
            },
        )
        case = self.collect()[0]
        self.assertEqual((case["trace"], len(case["calls"])), ("incomplete", 1))
        self.assertEqual(case["calls"][0]["request"]["invocation_id"], "ticket-routing/r1/classify/wf/1")
        self.run["run_data"]["Agency classify"][0]["source"][0]["previousNodeOutput"] = 1
        write_json(self.artifact / "case.json", self.run)
        write_json(
            self.verifier / "evidence/observation.json",
            {
                "plan_sha256": digest(self.plan),
                "entries": [{"name": "high-priority", "files": inventory(self.artifact)}],
            },
        )
        with self.assertRaisesRegex(AssertionError, "true Guard branch"):
            self.collect()


class TrustedLiteralPinTests(unittest.TestCase):
    def test_each_need_message_is_a_literal_or_a_runtime_literal_prefix(self):
        import re

        paths = [ROOT / "src/sapi_config_lab/compile/runtime-fragment.js"]
        paths += [
            ROOT / "tasks" / name / "operations.js" for name in ("invoice-total", "ticket-routing", "research-report")
        ]
        for path in paths:
            for number, line in enumerate(path.read_text().splitlines(), 1):
                if "need(" not in line or line.startswith("function need("):
                    continue
                with self.subTest(source=path.relative_to(ROOT), line=number):
                    message = re.search(r"""need\(.+,\s*(['"])(.*?)\1\s*(\+.*)?\);?""", line)
                    self.assertIsNotNone(message, "A nonliteral need message requires an explicit attribution decision")
                    assert message is not None
                    if message[3]:
                        self.assertEqual(path, paths[0], "Only runtime fragment prefixes are trusted")
