"""Contract tests; upstream is mocked, never invokes a model."""

from io import BytesIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from sapi_config_lab.execute.agency import ContractError, DispatchAudit, check_schema, execute, strict_json


class BridgeContractTests(unittest.TestCase):
    def setUp(self):
        self.catalog = {
            "example.classify": {
                "kind": "LLM",
                "prompt": "Classify input.",
                "input_schema": {
                    "type": "object",
                    "properties": {"text": {"type": "string"}},
                    "required": ["text"],
                    "additionalProperties": False,
                },
                "output_schema": {
                    "type": "object",
                    "properties": {"priority": {"type": "string", "enum": ["high", "normal"]}},
                    "required": ["priority"],
                    "additionalProperties": False,
                },
            }
        }
        self.request = {
            "invocation_id": "trial:1:step",
            "operation": "example.classify",
            "inputs": {"text": "synthetic"},
        }

    def call(self, wrapper):
        with patch(
            "sapi_config_lab.execute.agency.urlopen", return_value=BytesIO(json.dumps(wrapper).encode())
        ) as mocked:
            result = execute(self.request, self.catalog, "http://127.0.0.1:8765/run", 1)
        return result, mocked

    def test_success_preserves_id_and_only_exposes_model_id(self):
        result, mocked = self.call(
            {
                "ok": True,
                "exit_code": 0,
                "output": '{"priority":"high"}',
                "stderr": "sensitive diagnostics\nmodel: configured-model\n",
            }
        )
        self.assertEqual(result["output"], {"priority": "high"})
        self.assertEqual(result["invocation_id"], self.request["invocation_id"])
        self.assertEqual(result["model"], "configured-model")
        self.assertNotIn("sensitive", json.dumps(result))
        sent = json.loads(mocked.call_args.args[0].data)
        self.assertEqual(set(sent), {"prompt"})

    def test_invalid_results_fail_closed(self):
        for output in (
            '{"priority":"urgent"}',
            "{}",
            '{"priority":"high","extra":1}',
            "```json\n{}\n```",
            '{"priority":"high","priority":"normal"}',
        ):
            with self.subTest(output=output), self.assertRaises(ContractError):
                self.call({"ok": True, "exit_code": 0, "output": output})

    def test_failed_wrapper_is_not_success(self):
        with self.assertRaises(ContractError):
            self.call({"ok": False, "exit_code": 1, "output": '{"priority":"high"}'})

    def test_no_upstream_for_bad_input_or_arbitrary_prompt(self):
        for change in ({"operation": "missing"}, {"inputs": {"text": 123}}, {"prompt": "arbitrary"}):
            with self.subTest(change=change), patch("sapi_config_lab.execute.agency.urlopen") as mocked:
                with self.assertRaises(ContractError):
                    execute({**self.request, **change}, self.catalog, "http://127.0.0.1:8765/run", 1)
                mocked.assert_not_called()

    def test_no_nonfinite_numbers_or_bool_as_integer(self):
        for text in ('{"x":NaN}', '{"x":Infinity}'):
            with self.assertRaises(ContractError):
                strict_json(text)
        with self.assertRaises(ContractError):
            check_schema(True, {"type": "integer"})

    def test_outgoing_audit_is_written_before_dispatch_and_budget_latches(self):
        with tempfile.TemporaryDirectory() as directory:
            audit = DispatchAudit(
                Path(directory) / "audit.jsonl",
                {"max_attempts": 1, "operations": {"example.classify": 1}, "model": "configured-model"},
            )

            def upstream(*args, **kwargs):
                self.assertEqual(audit.records()[0]["event"], "dispatch_attempt")
                return BytesIO(
                    json.dumps(
                        {
                            "ok": True,
                            "exit_code": 0,
                            "output": '{"priority":"high"}',
                            "stderr": "model: configured-model\ntokens used\n1,234\nsecret",
                        }
                    ).encode()
                )

            with patch("sapi_config_lab.execute.agency.urlopen", side_effect=upstream) as called:
                execute(self.request, self.catalog, "http://unused", 1, audit=audit)
                with self.assertRaises(ContractError):
                    execute({**self.request, "invocation_id": "second"}, self.catalog, "http://unused", 1, audit=audit)
                with self.assertRaises(ContractError):
                    execute({**self.request, "invocation_id": "third"}, self.catalog, "http://unused", 1, audit=audit)
                self.assertEqual(called.call_count, 1)
            self.assertTrue(audit.failed)
            self.assertEqual(audit.records()[1]["wrapper"]["cli_reported_tokens"], 1234)
            self.assertNotIn("secret", json.dumps(audit.records()))

    def test_failure_disarms_later_dispatch_and_timeout_outcome_stays_unknown(self):
        failures = [
            TimeoutError(),
            BytesIO(b'{"ok":false,"exit_code":1}'),
            BytesIO(b'{"ok":true,"exit_code":0,"output":"bad"}'),
        ]
        for failure in failures:
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as directory:
                audit = DispatchAudit(
                    Path(directory) / "audit.jsonl",
                    {"max_attempts": 2, "operations": {"example.classify": 2}, "model": "configured-model"},
                )
                with patch(
                    "sapi_config_lab.execute.agency.urlopen",
                    side_effect=failure if isinstance(failure, Exception) else None,
                    return_value=failure,
                ) as called:
                    with self.assertRaises((ContractError, TimeoutError)):
                        execute(self.request, self.catalog, "http://unused", 1, audit=audit)
                    with self.assertRaises(ContractError):
                        execute(
                            {**self.request, "invocation_id": "later"}, self.catalog, "http://unused", 1, audit=audit
                        )
                    self.assertEqual(called.call_count, 1)
                if isinstance(failure, TimeoutError):
                    self.assertEqual(audit.records()[1]["category"], "timeout_unknown_outcome")
                    self.assertEqual(audit.records()[1]["model_outcome"], "unknown")

    def test_duplicate_and_invalid_request_never_reach_upstream_again(self):
        with tempfile.TemporaryDirectory() as directory:
            for kind in ("duplicate", "invalid"):
                audit = DispatchAudit(
                    Path(directory) / (kind + ".jsonl"),
                    {"max_attempts": 2, "operations": {"example.classify": 2}, "model": "configured-model"},
                )
                with patch(
                    "sapi_config_lab.execute.agency.urlopen",
                    return_value=BytesIO(
                        b'{"ok":true,"exit_code":0,"output":"{\\"priority\\":\\"high\\"}","stderr":"model: configured-model"}'
                    ),
                ) as called:
                    if kind == "duplicate":
                        execute(self.request, self.catalog, "http://unused", 1, audit=audit)
                    bad = self.request if kind == "duplicate" else {**self.request, "inputs": {"text": 12}}
                    with self.assertRaises(ContractError):
                        execute(bad, self.catalog, "http://unused", 1, audit=audit)
                    with self.assertRaises(ContractError):
                        execute(
                            {**self.request, "invocation_id": "later"}, self.catalog, "http://unused", 1, audit=audit
                        )
                    self.assertEqual(called.call_count, 1 if kind == "duplicate" else 0)

    def test_budget_must_be_explicit_bounded_and_cannot_resume_existing_audit(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "audit.jsonl"
            for budget in (
                {},
                {"max_attempts": 9, "operations": {"example.classify": 9}, "model": "configured-model"},
                {"max_attempts": 8, "operations": {"example.classify": 2}, "model": "configured-model"},
            ):
                with self.assertRaises(ValueError):
                    DispatchAudit(path, budget)
            budget = {"max_attempts": 1, "operations": {"example.classify": 1}, "model": "configured-model"}
            DispatchAudit(path, budget)
            with self.assertRaises(FileExistsError):
                DispatchAudit(path, budget)


if __name__ == "__main__":
    unittest.main()
