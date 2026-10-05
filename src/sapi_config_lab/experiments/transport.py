"""Exercise the live HTTP path in real n8n against a deterministic fake bridge.

Run inside the isolated lab image. These are transport contract tests, not live
model calls and not a substitute for the three independent task verifiers.
"""

from __future__ import annotations

import argparse
import copy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import threading
import time
from typing import Any

from sapi_config_lab.paths import workspace_root
from sapi_config_lab.workflow import profile
from sapi_config_lab.runtime.execution import run_case, write_json

ROOT = workspace_root()


class FakeBridge(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self):
        super().__init__(("127.0.0.1", 0), BridgeHandler)
        self.mode = "valid"
        self.calls: list[dict[str, Any]] = []


class BridgeHandler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_POST(self):
        server = self.server
        assert isinstance(server, FakeBridge)
        request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        mode = server.mode
        server.calls.append({"path": self.path, "mode": mode, "request": request})
        response: Any = {
            "invocation_id": request["invocation_id"],
            "status": "completed",
            "output": {"category": "delivery", "priority": "high"},
        }
        status = 200
        if mode == "wrong_invocation":
            response["invocation_id"] = "wrong-invocation"
        elif mode == "wrong_schema":
            response["output"]["priority"] = "urgent"
        elif mode == "failed_status":
            response["status"] = "failed"
        elif mode == "extra_field":
            response["steps"] = {"injected": "must not replace the envelope"}
        elif mode == "http_error":
            status = 503
        elif mode == "timeout":
            time.sleep(2)
        elif mode == "array_response":
            response = [response, copy.deepcopy(response)]
        raw = b'{"broken":' if mode == "malformed_json" else json.dumps(response).encode()
        try:
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
        except BrokenPipeError, ConnectionResetError:
            # Expected after the n8n HTTP node's short timeout probe.
            pass


def probe_config(enabled=True, invalid_input=False):
    config = profile.read(ROOT / "configs/02-ticket-routing.yaml")
    workflow = config["workflow"]
    workflow["id"] = "agency-transport-probe"
    config["activation"]["workflow_ref"]["id"] = workflow["id"]
    config["execution"]["deadline_seconds"] = 30
    workflow["inputs"]["enabled"] = enabled
    if invalid_input:
        workflow["inputs"]["ticket"]["days_overdue"] = -1
    step = workflow["steps"][0]
    step["when"] = {"ref": "inputs.enabled", "eq": True}
    workflow["steps"] = [step]
    workflow["dependencies"] = []
    workflow["output"] = {"optional_ref": "steps.classify"}
    workflow["acceptance"] = "Probe the generic live transport contract and skipped-step semantics."
    return config


def short_http_timeout(artifact):
    for node in artifact["nodes"]:
        if node["type"] == "n8n-nodes-base.httpRequest":
            node["parameters"]["options"]["timeout"] = 200
    return artifact


def run_probes(artifacts: Path):
    artifacts = Path(artifacts).resolve()
    artifacts.mkdir(parents=True, exist_ok=True)
    server = FakeBridge()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    cases = [
        ("valid", True, False, True, 1),
        ("guard_false", False, False, True, 0),
        ("guard_false_invalid_inputs", False, True, True, 0),
        ("invalid_inputs", True, True, False, 0),
        ("wrong_invocation", True, False, False, 1),
        ("wrong_schema", True, False, False, 1),
        ("failed_status", True, False, False, 1),
        ("extra_field", True, False, False, 1),
        ("malformed_json", True, False, False, 1),
        ("http_error", True, False, False, 1),
        ("array_response", True, False, False, 1),
        ("timeout", True, False, False, 1),
    ]
    rows = []
    try:
        for name, enabled, invalid_input, expect_success, call_count in cases:
            server.mode = name
            offset = len(server.calls)
            result = run_case(
                probe_config(enabled, invalid_input),
                artifacts / name,
                llm_mode="live",
                bridge_url=f"http://127.0.0.1:{server.server_port}",
                artifact_transform=short_http_timeout if name == "timeout" else None,
            )
            calls = copy.deepcopy(server.calls[offset:])
            checks = {
                "imported": result["import_exit_code"] == 0,
                "persisted_execution": bool(result["execution_id"]),
                "exact_request_count": len(calls) == call_count,
                "request_contract": all(
                    set(x["request"]) == {"invocation_id", "operation", "actor", "inputs"}
                    and x["path"] == "/v1/agency/execute"
                    for x in calls
                ),
                "expected_runtime_status": result["status"] == ("success" if expect_success else "error"),
                "result_presence": result["result_node_present"] is expect_success,
            }
            if expect_success:
                output = result.get("result") or {}
                state = "completed" if enabled else "skipped"
                checks["logical_status"] = output.get("statuses", {}).get("classify") == state
                checks["result_data"] = output.get("output") == (
                    {"category": "delivery", "priority": "high"} if enabled else None
                )
                if not enabled:
                    checks["http_node_not_executed"] = "Agency classify" not in result["run_data"]
            else:
                checks["error_present"] = bool(result.get("error"))
                checks["no_success_output"] = result["output"] is None
                expected_errors = {
                    "invalid_inputs": ["schema violation"],
                    "wrong_invocation": ["invocation id mismatch"],
                    "wrong_schema": ["schema violation"],
                    "failed_status": ["agency did not complete"],
                    "extra_field": ["unexpected agency response fields"],
                    "malformed_json": ["json"],
                    "http_error": ["503"],
                    "array_response": ["exactly one response item"],
                    "timeout": ["timeout", "timed out", "aborted"],
                }
                error_text = json.dumps(result.get("error"), default=str).lower()
                checks["expected_error_detected"] = any(fragment in error_text for fragment in expected_errors[name])
            write_json(artifacts / name / "fake-bridge-requests.json", calls)
            row = {
                "case": name,
                "passed": all(checks.values()),
                "checks": checks,
                "workflow_id": result["workflow_id"],
                "execution_id": result["execution_id"],
                "n8n_version": result["n8n_version"],
                "request_count": len(calls),
                "status": result["status"],
                "error": result.get("error"),
            }
            rows.append(row)
            print(json.dumps({"transport_probe": name, "passed": row["passed"], "status": row["status"]}), flush=True)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
    summary = {
        "passed": all(row["passed"] for row in rows),
        "cases": rows,
        "engine": "real n8n",
        "llm": "deterministic fake HTTP bridge; no model calls",
    }
    write_json(artifacts / "summary.json", summary)
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifacts", type=Path, required=True)
    args = parser.parse_args()
    summary = run_probes(args.artifacts)
    return 0 if summary["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
