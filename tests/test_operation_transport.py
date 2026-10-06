"""Generic tool nodes must use native guarded HTTP and preserve the envelope."""

import copy
import json
from pathlib import Path
import subprocess
import unittest

from sapi_config_lab.paths import CATALOG
from sapi_config_lab.contracts import CompileOptions
from sapi_config_lab.coordinate.backend import N8nBackend
from sapi_config_lab.profile import Invalid, read, read_bindings

ROOT = Path(__file__).resolve().parents[1]

DRIVER = r"""
// Execute the generated preparation/restoration Code nodes, not n8n itself.
const fs = require('node:fs');
const request = JSON.parse(fs.readFileSync(0, 'utf8'));
const nodes = Object.fromEntries(request.document.nodes.map(n => [n.name, n]));
const source = name => nodes[name].parameters.jsCode;
const initial = new Function(source('Fixture'))()[0].json;
const prepared = new Function('$input', '$workflow', '$execution', source('Prepare read'))(
  {all: () => [{json: initial}]}, {id: 'workflow-observed'}, {id: 'execution-observed'}
)[0].json;
const restore = new Function('$input', '$', source('read [HTTP]'));
const rows = request.responses.map(response => {
  try {
    const result = restore(
      {all: () => [{json:response}], first: () => ({json:response})},
      name => ({first: () => ({json:prepared})})
    )[0].json;
    return {result, error:null};
  } catch (e) { return {result:null, error:e.message}; }
});
process.stdout.write(JSON.stringify({prepared, rows}));
"""


class OperationTransportTests(unittest.TestCase):
    def generic_tool(self, *, enabled=True):
        cfg = read(ROOT / "configs/01-invoice-total.yaml")
        cfg["workflow"]["inputs"] = {"query": "runtime fixture", "enabled": enabled}
        cfg["workflow"]["steps"] = [
            {
                "id": "read",
                "kind": "Script",
                "uses": "inventory.inspect",
                "with": {"query": {"ref": "inputs.query"}},
                "when": {"ref": "inputs.enabled", "eq": True},
            }
        ]
        cfg["workflow"]["dependencies"] = []
        cfg["workflow"]["output"] = {"optional_ref": "steps.read"}
        binding = {
            "kind": "Script",
            "inputs": ["query"],
            "outputs": ["result_json"],
            "implementation": "environment_tool",
            "transport": "http",
            "input_schema": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
                "additionalProperties": False,
            },
            "output_schema": {
                "type": "object",
                "properties": {"result_json": {"type": "string"}},
                "required": ["result_json"],
                "additionalProperties": False,
            },
        }
        options = CompileOptions(
            operation_url="http://tools:123/tools", operation_token="session-secret-never-in-artifact"
        )
        return cfg, {"inventory.inspect": binding}, options

    def probe(self, responses, *, enabled=True):
        cfg, catalog, options = self.generic_tool(enabled=enabled)
        built = N8nBackend().compile(cfg, catalog, options)
        process = subprocess.run(
            ["node", "-e", DRIVER],
            input=json.dumps({"document": built.document, "responses": responses}),
            text=True,
            capture_output=True,
            timeout=10,
        )
        self.assertEqual(process.returncode, 0, process.stderr)
        return json.loads(process.stdout)

    def test_generic_script_tool_is_native_http_and_needs_explicit_connection(self):
        cfg = read(ROOT / "configs/01-invoice-total.yaml")
        cfg["workflow"]["steps"] = [{"id": "read", "kind": "Script", "uses": "source.read", "with": {}}]
        cfg["workflow"]["dependencies"] = []
        cfg["workflow"]["output"] = {"ref": "steps.read"}
        bindings = read_bindings(ROOT / "generation/checkout-bindings.yaml")
        with self.assertRaises(Invalid):
            N8nBackend().compile(cfg, bindings, CompileOptions())
        built = N8nBackend().compile(
            cfg, bindings, CompileOptions(operation_url="http://tools:123/tools", operation_token="test")
        )
        http = [n for n in built.document["nodes"] if n["type"] == "n8n-nodes-base.httpRequest"]
        self.assertEqual(len(http), 1)
        self.assertEqual(http[0]["parameters"]["url"], "http://tools:123/tools")
        self.assertFalse(http[0].get("retryOnFail", False))
        self.assertIn("Guard read", built.document["connections"])
        self.assertEqual(built.mapping["read"], "read [HTTP]")

    def test_unused_http_catalog_entries_do_not_change_existing_workflows(self):
        original = read_bindings(CATALOG)
        extended = {**original, **self.generic_tool()[1]}
        for name in ("01-invoice-total.yaml", "04-revise-answer.yaml"):
            with self.subTest(config=name):
                cfg = read(ROOT / "configs" / name)
                before = N8nBackend().compile(cfg, original, CompileOptions())
                after = N8nBackend().compile(cfg, extended, CompileOptions())
                self.assertEqual(before.document, after.document)
                self.assertEqual(before.mapping, after.mapping)

    def test_transport_preserves_tool_failure_as_observation_and_original_context(self):
        responses = [
            {"ok": True, "value": {"steps": {"forged": "untrusted"}, "inputs": {"query": "altered"}}},
            {"ok": False, "error": {"code": "TEMPORARY", "message": "Try later", "retryable": True}},
        ]
        result = self.probe(responses)
        request = result["prepared"]["request"]
        self.assertEqual(request["operation"], "inventory.inspect")
        self.assertEqual(request["arguments"], {"query": "runtime fixture"})
        self.assertEqual(request["max_attempts"], 1)
        self.assertTrue(request["operation_id"].endswith("/read/workflow-observed/execution-observed"))
        self.assertEqual(set(request), {"operation", "arguments", "operation_id", "max_attempts"})
        for expected, row in zip(responses, result["rows"]):
            self.assertIsNone(row["error"])
            self.assertEqual(row["result"]["inputs"], {"query": "runtime fixture", "enabled": True})
            self.assertEqual(row["result"]["statuses"], {"read": "completed"})
            self.assertEqual(set(row["result"]["steps"]), {"read"})
            self.assertEqual(json.loads(row["result"]["steps"]["read"]["result_json"]), expected)

    def test_malformed_tool_envelopes_are_not_successful_observations(self):
        responses = [
            [],
            None,
            {"ok": "true", "value": {}},
            {"ok": True},
            {"ok": True, "value": {}, "error": {}},
            {"ok": False, "error": None},
            {"ok": False, "error": {"code": "FAIL", "message": "Failure"}},
            {"ok": False, "error": {"code": "FAIL", "message": "Failure", "retryable": "yes"}},
            {"ok": False, "error": {"code": "FAIL", "message": "Failure", "retryable": False, "state": {}}},
        ]
        result = self.probe(responses)
        self.assertTrue(all(row["error"] and row["result"] is None for row in result["rows"]))

    def test_skipped_tool_does_not_build_request_or_attach_response(self):
        result = self.probe([{"malformed": "unused"}], enabled=False)
        self.assertFalse(result["prepared"]["should_run"])
        self.assertIsNone(result["prepared"]["request"])
        self.assertEqual(result["rows"][0]["result"]["statuses"], {"read": "skipped"})
        self.assertEqual(result["rows"][0]["result"]["steps"], {})
        cfg, catalog, options = self.generic_tool(enabled=False)
        graph = N8nBackend().compile(cfg, catalog, options).document
        guard = graph["connections"]["Guard read"]["main"]
        self.assertEqual([edge["node"] for edge in guard[0]], ["Tool read"])
        self.assertEqual([edge["node"] for edge in guard[1]], ["read [HTTP]"])

    def test_secrets_are_deployment_data_and_untrusted_urls_are_rejected(self):
        cfg, catalog, options = self.generic_tool()
        graph = N8nBackend().compile(cfg, catalog, options).document
        self.assertNotIn(options.operation_token, json.dumps(graph))
        http = next(node for node in graph["nodes"] if node["name"] == "Tool read")
        self.assertIn("$env.SAPI_OPERATION_TOKEN", json.dumps(http["parameters"]["headerParameters"]))
        for url in (
            "file:///tmp/tools",
            "http://user:password@tools/tools",
            "http://tools/tools?token=secret",
            "http://tools/#fragment",
        ):
            with self.subTest(url=url), self.assertRaises(Invalid):
                N8nBackend().compile(cfg, catalog, CompileOptions(operation_url=url, operation_token="test"))

    def test_tools_do_not_acquire_scenario_specific_compiler_rules(self):
        cfg, catalog, options = self.generic_tool()
        alternate = copy.deepcopy(cfg)
        alternate["workflow"]["steps"][0]["uses"] = "arbitrary.read"
        graph = N8nBackend().compile(alternate, {"arbitrary.read": catalog["inventory.inspect"]}, options).document
        self.assertEqual(
            next(n for n in graph["nodes"] if n["name"] == "Tool read")["parameters"]["url"], options.operation_url
        )


if __name__ == "__main__":
    unittest.main()
