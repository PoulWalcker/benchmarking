"""Local catalog adapter for the user's existing codex-exec HTTP wrapper.

No credentials, stderr, arbitrary prompts, or model overrides are exposed.
The upstream wrapper remains unchanged. This is a research adapter, not a
durable Agency service: no retries, idempotency, cancellation or actor memory.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, HTTPRedirectHandler, build_opener

import yaml
from sapi_config_lab.paths import CATALOG

MAX_BODY = 1_048_576


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


urlopen = build_opener(NoRedirect).open


class ContractError(ValueError):
    pass


def check_schema(value, schema, path="output"):
    """Validate the documented catalog subset without coercion or extra deps."""
    kind = schema.get("type")
    if isinstance(kind, list):
        for candidate in kind:
            try:
                check_schema(value, {**schema, "type": candidate}, path)
                return
            except ContractError:
                continue
        raise ContractError(f"{path}: invalid union type")
    types = {
        "object": lambda x: type(x) is dict,
        "array": lambda x: type(x) is list,
        "string": lambda x: type(x) is str,
        "integer": lambda x: type(x) is int,
        "number": lambda x: type(x) in (int, float) and math.isfinite(x),
        "boolean": lambda x: type(x) is bool,
        "null": lambda x: x is None,
    }
    if kind not in types or not types[kind](value):
        raise ContractError(f"{path}: invalid type")
    if "enum" in schema and not any(type(value) is type(x) and value == x for x in schema["enum"]):
        raise ContractError(f"{path}: invalid enum")
    if kind == "object":
        props = schema.get("properties", {})
        if not set(schema.get("required", [])) <= value.keys():
            raise ContractError(f"{path}: missing fields")
        if schema.get("additionalProperties") is False and not value.keys() <= props.keys():
            raise ContractError(f"{path}: extra fields")
        for name, child in value.items():
            if name in props:
                check_schema(child, props[name], f"{path}.{name}")
    if kind in ("array", "string"):
        suffix = "Items" if kind == "array" else "Length"
        if len(value) < schema.get("min" + suffix, 0) or len(value) > schema.get("max" + suffix, math.inf):
            raise ContractError(f"{path}: invalid length")
    if kind == "array":
        for index, child in enumerate(value):
            check_schema(child, schema["items"], f"{path}[{index}]")
    if kind in ("integer", "number"):
        if value < schema.get("minimum", -math.inf) or value > schema.get("maximum", math.inf):
            raise ContractError(f"{path}: out of range")


def strict_json(text):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ContractError("duplicate JSON key")
            result[key] = value
        return result

    def bad_constant(_):
        raise ContractError("non-finite JSON constant")

    try:
        return json.loads(text, object_pairs_hook=pairs, parse_constant=bad_constant)
    except (ValueError, TypeError) as exc:
        raise ContractError("invalid JSON response") from exc


def canonical_hash(value) -> str:
    """SHA-256 of UTF-8 JSON: sorted keys, compact separators, Unicode, finite values."""
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()
    ).hexdigest()


def build_prompt(data, binding) -> str:
    # Preserve the established model instructions and prompt encoding.
    return (
        Path(__file__).with_name("agency-prompt.md").read_text()
        + "OPERATION: "
        + data["operation"]
        + "\nTASK: "
        + binding["prompt"]
        + "\nOUTPUT_JSON_SCHEMA: "
        + json.dumps(binding["output_schema"], ensure_ascii=False)
        + "\nINPUT_JSON: "
        + json.dumps(data["inputs"], ensure_ascii=False)
    )


class DispatchAudit:
    """One serial adapter's durable attempt budget and fail-closed run latch."""

    def __init__(self, path: Path, budget: dict):
        if (
            not isinstance(budget, dict)
            or set(budget)
            not in (
                {"max_attempts", "operations", "model"},
                {"max_attempts", "operations", "model", "occurrences"},
                {"max_attempts", "operations", "model", "occurrences", "expires_at"},
                {"max_attempts", "operations", "model", "occurrences", "expires_at", "workflow_id"},
            )
            or type(budget["max_attempts"]) is not int
            or not (0 if "occurrences" in budget else 1) <= budget["max_attempts"] <= 8
            or not isinstance(budget["operations"], dict)
            or any(
                not isinstance(key, str) or type(value) is not int or value < 1
                for key, value in budget["operations"].items()
            )
            or sum(budget["operations"].values()) != budget["max_attempts"]
            or not isinstance(budget["model"], str)
            or re.fullmatch(r"[A-Za-z0-9_.-]+", budget["model"]) is None
        ):
            raise ValueError("Invalid outgoing attempt budget")
        occurrences = budget.get("occurrences")
        if occurrences is not None:
            if not isinstance(occurrences, dict) or len(occurrences) != budget["max_attempts"]:
                raise ValueError("Invalid outgoing occurrence admission")
            expected_counts: dict[str, int] = {}
            for occurrence, operation in occurrences.items():
                if (
                    not isinstance(occurrence, str)
                    or re.fullmatch(
                        r"[a-z][a-z0-9_-]*/r[1-9][0-9]*/[a-z][a-z0-9_-]*(?:/attempt[1-9][0-9]*)?", occurrence
                    )
                    is None
                    or not isinstance(operation, str)
                ):
                    raise ValueError("Invalid outgoing occurrence admission")
                expected_counts[operation] = expected_counts.get(operation, 0) + 1
            if expected_counts != budget["operations"]:
                raise ValueError("Occurrence and operation budgets disagree")
        elif "occurrences" in budget:
            raise ValueError("Invalid outgoing occurrence admission")
        if "expires_at" in budget and (
            type(budget["expires_at"]) not in (int, float)
            or not math.isfinite(budget["expires_at"])
            or budget["expires_at"] <= 0
        ):
            raise ValueError("Invalid outgoing case deadline")
        if "workflow_id" in budget and (
            not isinstance(budget["workflow_id"], str)
            or re.fullmatch(r"[A-Za-z0-9_-]{1,64}", budget["workflow_id"]) is None
        ):
            raise ValueError("Invalid admitted native workflow ID")
        self.occurrences: set[str] = set()
        self.execution_identity: str | None = None
        self.path = path
        self.budget = budget
        self.failed = False
        self.invocations: set[str] = set()
        self.counts: dict[str, int] = {}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Never silently resume a previous run's accounting.
        with self.path.open("x"):
            pass

    def records(self) -> list[dict]:
        return [strict_json(line) for line in self.path.read_text().splitlines()]

    def append(self, record):
        with self.path.open("a") as stream:
            stream.write(json.dumps({"schema": "sapi-lab-dispatch/v1", "timestamp_unix": time.time(), **record}) + "\n")
            stream.flush()
            os.fsync(stream.fileno())

    def begin(self, data, prompt):
        if self.failed:
            raise ContractError("run failure latch is set")
        if self.budget.get("expires_at", time.time() + 1) <= time.time():
            raise ContractError("case deadline expired before dispatch")
        invocation, operation = data["invocation_id"], data["operation"]
        if invocation in self.invocations:
            raise ContractError("duplicate invocation ID")
        if "occurrences" in self.budget:
            parts = invocation.split("/")
            if len(parts) not in (5, 6) or not all(parts):
                raise ContractError("invalid occurrence invocation identity")
            occurrence, identity = "/".join(parts[:-2]), "/".join(parts[-2:])
            if "workflow_id" in self.budget and parts[-2] != self.budget["workflow_id"]:
                raise ContractError("unadmitted native workflow")
            if self.budget["occurrences"].get(occurrence) != operation or occurrence in self.occurrences:
                raise ContractError("unadmitted or repeated occurrence")
            if self.execution_identity is not None and identity != self.execution_identity:
                raise ContractError("occurrence belongs to another native execution")
            self.execution_identity = identity
            self.occurrences.add(occurrence)
        if len(self.invocations) >= self.budget["max_attempts"] or self.counts.get(operation, 0) >= self.budget[
            "operations"
        ].get(operation, 0):
            raise ContractError("outgoing attempt budget exceeded")
        self.invocations.add(invocation)
        self.counts[operation] = self.counts.get(operation, 0) + 1
        context = {
            "attempt": len(self.invocations),
            "invocation_id": invocation,
            "operation": operation,
            "inputs_sha256": canonical_hash(data["inputs"]),
            "request_sha256": canonical_hash(data),
            "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
        }
        self.append({"event": "dispatch_attempt", **context})
        return context

    def fail(self, context, category, *, outcome="not_dispatched"):
        self.failed = True
        self.append({"event": "failure", **context, "category": category, "model_outcome": outcome})


def execute(data, catalog, upstream, timeout, *, audit: DispatchAudit | None = None, reject_tool_use: bool = False):
    context: dict = {}
    stage = "invalid_input"
    try:
        if type(data) is not dict or not {"invocation_id", "operation", "inputs"} <= data.keys():
            raise ContractError("invalid request")
        if data.keys() - {"invocation_id", "operation", "inputs", "actor"}:
            raise ContractError("unexpected request fields")
        invocation = data["invocation_id"]
        if not isinstance(invocation, str) or not 1 <= len(invocation) <= 240:
            raise ContractError("invalid invocation ID")
        operation = data["operation"]
        if not isinstance(operation, str) or operation not in catalog:
            raise ContractError("unknown operation")
        binding = catalog[operation]
        if binding["kind"] != "LLM" or "output_schema" not in binding or "input_schema" not in binding:
            raise ContractError("operation has no live contract")
        check_schema(data["inputs"], binding["input_schema"], "inputs")
        prompt = build_prompt(data, binding)
        request = Request(upstream, json.dumps({"prompt": prompt}).encode(), {"Content-Type": "application/json"})
        stage = "budget"
        if audit:
            context = audit.begin(data, prompt)
        stage = "agency_transport"
        # Attempt is fsynced immediately before this actual outgoing call; no retries.
        if audit and "expires_at" in audit.budget:
            timeout = min(timeout, 185, audit.budget["expires_at"] - time.time())
            if timeout <= 0:
                raise ContractError("case deadline expired before dispatch")
        with urlopen(request, timeout=timeout) as response:
            raw = response.read(MAX_BODY + 1)
        stage = "upstream_wrapper_failure"
        if len(raw) > MAX_BODY:
            raise ContractError("wrapper response too large")
        wrapper = strict_json(raw)
        if (
            type(wrapper) is not dict
            or wrapper.get("ok") is not True
            or type(wrapper.get("exit_code")) is not int
            or wrapper["exit_code"] != 0
        ):
            raise ContractError("codex wrapper failed")
        stage = "model_output_contract"
        output = strict_json(wrapper.get("output"))
        check_schema(output, binding["output_schema"])
        stderr = wrapper.get("stderr", "")
        if not isinstance(stderr, str):
            raise ContractError("invalid wrapper metadata")
        observed_tool_markers = [
            name
            for name, pattern in {
                "shell": r"(?m)^exec(?:\s|$)",
                "tool": r"(?m)^tool\s+",
                "file_edit": r"(?m)^(?:file update|apply_patch)(?:\s|$)",
                "web": r"(?mi)^(?:web search|searching the web|searched the web)(?:\s|$)",
            }.items()
            if re.search(pattern, stderr)
        ]
        if reject_tool_use and observed_tool_markers:
            stage = "forbidden_tool_use_observed"
            raise ContractError("forbidden tool use observed")
        model = re.search(r"(?m)^model:\s*([A-Za-z0-9_.-]+)\s*$", stderr)
        model_name = model.group(1) if model else None
        if audit and model_name != audit.budget["model"]:
            raise ContractError("unexpected or absent wrapper model identifier")
        result = {"invocation_id": invocation, "status": "completed", "output": output, "usage": None}
        if audit:
            tokens = re.search(r"(?m)^tokens used\s*\n([\d,]+)\s*$", stderr)
            audit.append(
                {
                    "event": "completion",
                    **context,
                    "response_sha256": canonical_hash(result),
                    "output_sha256": canonical_hash(output),
                    "wrapper": {
                        "ok": True,
                        "exit_code": 0,
                        "model": model_name,
                        "response_bytes": len(raw),
                        "response_sha256": hashlib.sha256(raw).hexdigest(),
                        "output_text_sha256": hashlib.sha256(wrapper["output"].encode()).hexdigest(),
                        "stderr_sha256": hashlib.sha256(stderr.encode()).hexdigest(),
                        "cli_reported_tokens": int(tokens.group(1).replace(",", "")) if tokens else None,
                        "tool_audit_enabled": reject_tool_use,
                        "observed_tool_markers": observed_tool_markers if reject_tool_use else None,
                        "observed_retry_markers": len(re.findall(r"(?mi)^(?:retrying|reconnecting)\b", stderr)),
                        "provider_call_count": None,
                    },
                }
            )
        return {**result, "provider": "existing-codex-exec-wrapper", "model": model_name}
    except Exception as error:
        if audit:
            timeout_error = (
                isinstance(error, TimeoutError)
                or isinstance(error, URLError)
                and isinstance(error.reason, TimeoutError)
            )
            audit.fail(
                context,
                "timeout_unknown_outcome" if timeout_error else stage,
                outcome="unknown" if context else "not_dispatched",
            )
        raise


def make_handler(catalog, upstream, timeout, audit_path, budget=None):
    dispatch = DispatchAudit(audit_path, budget) if budget is not None else None

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def reply(self, status, data):
            raw = json.dumps(data, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self):
            self.reply(
                200 if self.path == "/health" else 404,
                {"service": "sapi-lab-agency-adapter", "mode": "live", "ready": True},
            )

        def do_POST(self):
            if self.path != "/v1/agency/execute":
                self.reply(404, {"status": "failed", "error": "not_found"})
                return
            started = time.monotonic()
            data = {}
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if not 0 < size <= MAX_BODY:
                    raise ContractError("invalid request size")
                data = strict_json(self.rfile.read(size))
                result = execute(data, catalog, upstream, timeout, audit=dispatch)
                status = 200
            except TimeoutError, HTTPError, URLError:
                status, result = 502, {"status": "failed", "error": "upstream_transport"}
            except ContractError as error:
                # All ContractError messages are adapter-owned labels or schema
                # paths; they never include model output or wrapper stderr.
                status, result = 422, {"status": "failed", "error": "contract_rejected", "detail": str(error)}
            except ValueError:
                status, result = (
                    422,
                    {"status": "failed", "error": "contract_rejected", "detail": "invalid request length"},
                )
            except Exception:
                status, result = 500, {"status": "failed", "error": "adapter_error"}
            record = {
                "timestamp_unix": time.time(),
                "duration_seconds": round(time.monotonic() - started, 3),
                "http_status": status,
                "status": result["status"],
                "error": result.get("error"),
                "detail": result.get("detail"),
                "model": result.get("model"),
            }
            if isinstance(data, dict):
                record.update(
                    {
                        "invocation_id": data.get("invocation_id"),
                        "operation": data.get("operation"),
                        "inputs_sha256": canonical_hash(data.get("inputs")),
                    }
                )
            if dispatch:
                if status != 200 and not dispatch.failed:
                    dispatch.fail({}, "invalid_input")
                dispatch.append({"event": "agency_response", **record})
            else:
                with audit_path.open("a") as out:
                    out.write(json.dumps(record) + "\n")
            try:
                wire_result = {key: value for key, value in result.items() if key not in ("provider", "model")}
                self.reply(status, wire_result)
            except BrokenPipeError, ConnectionResetError:
                pass

    return Handler


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=18765)
    parser.add_argument("--upstream", default="http://127.0.0.1:8765/run")
    parser.add_argument("--timeout", type=int, default=185)
    parser.add_argument("--bindings", type=Path, default=CATALOG)
    parser.add_argument("--audit", type=Path, required=True)
    parser.add_argument("--budget", type=Path)
    args = parser.parse_args()
    catalog = yaml.safe_load(args.bindings.read_text())["operations"]
    args.audit.parent.mkdir(parents=True, exist_ok=True)
    budget = strict_json(args.budget.read_bytes()) if args.budget else None
    server = HTTPServer((args.host, args.port), make_handler(catalog, args.upstream, args.timeout, args.audit, budget))
    print(f"Catalog adapter listening on {args.host}:{args.port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
