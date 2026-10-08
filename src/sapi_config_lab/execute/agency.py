"""Agency bridge: n8n's catalog operations forwarded to the local model wrapper, budgeted and audited.

No credentials, stderr, arbitrary prompts or model overrides are exposed. This is a
research adapter, not a durable Agency service: no retries, idempotency or cancellation.
"""

from __future__ import annotations

import hashlib
from http.server import BaseHTTPRequestHandler
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from urllib.error import HTTPError, URLError

from sapi_config_lab.evidence import digest
from sapi_config_lab.execute.host import local_address
from sapi_config_lab.net import urlopen
from sapi_config_lab.paths import workspace_root
from sapi_config_lab.wrapper_audit import reported_model, reported_tokens, stderr_sha256, tool_markers

MAX_BODY = 1_048_576
MAX_OUTGOING_ATTEMPTS = 8
# The wrapper's own subprocess deadline is 180 seconds; never wait much past it.
WRAPPER_TIMEOUT_SECONDS = 185
BUDGET_FIELDS = (
    {"max_attempts", "operations", "model"},
    {"max_attempts", "operations", "model", "occurrences"},
    {"max_attempts", "operations", "model", "occurrences", "expires_at"},
    {"max_attempts", "operations", "model", "occurrences", "expires_at", "workflow_id"},
)
OCCURRENCE = re.compile(r"[a-z][a-z0-9_-]*/r[1-9][0-9]*/[a-z][a-z0-9_-]*(?:/attempt[1-9][0-9]*)?")


class ContractError(ValueError):
    pass


def check_schema(value, schema, path="output"):
    """Validate the documented catalog subset without coercion or extra dependencies."""
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
    if kind in ("integer", "number") and not schema.get("minimum", -math.inf) <= value <= schema.get(
        "maximum", math.inf
    ):
        raise ContractError(f"{path}: out of range")


def strict_json(text):
    """JSON that rejects duplicate keys and non-finite constants."""

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


def build_prompt(data, binding) -> str:
    """The exact model prompt; its bytes are pinned by recorded prompt hashes."""
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


def validate_budget(budget: dict) -> None:
    if (
        not isinstance(budget, dict)
        or set(budget) not in BUDGET_FIELDS
        or type(budget["max_attempts"]) is not int
        or not (0 if "occurrences" in budget else 1) <= budget["max_attempts"] <= MAX_OUTGOING_ATTEMPTS
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
    if "occurrences" in budget:
        occurrences = budget["occurrences"]
        if not isinstance(occurrences, dict) or len(occurrences) != budget["max_attempts"]:
            raise ValueError("Invalid outgoing occurrence admission")
        counts: dict[str, int] = {}
        for occurrence, operation in occurrences.items():
            if (
                not isinstance(occurrence, str)
                or OCCURRENCE.fullmatch(occurrence) is None
                or not isinstance(operation, str)
            ):
                raise ValueError("Invalid outgoing occurrence admission")
            counts[operation] = counts.get(operation, 0) + 1
        if counts != budget["operations"]:
            raise ValueError("Occurrence and operation budgets disagree")
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


class DispatchAudit:
    """One serial adapter's durable attempt budget and fail-closed run latch."""

    def __init__(self, path: Path, budget: dict):
        validate_budget(budget)
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
            "inputs_sha256": digest(data["inputs"]),
            "request_sha256": digest(data),
            "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
        }
        self.append({"event": "dispatch_attempt", **context})
        return context

    def fail(self, context, category, *, outcome="not_dispatched"):
        self.failed = True
        self.append({"event": "failure", **context, "category": category, "model_outcome": outcome})


def execute(
    data, catalog, upstream, timeout, *, audit: DispatchAudit | None = None, reject_tool_use: bool = False, transport
):
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
        stage = "budget"
        if audit:
            context = audit.begin(data, prompt)
        stage = "agency_transport"
        # The attempt is fsynced just before this one outgoing call; there are no retries.
        if audit and "expires_at" in audit.budget:
            timeout = min(timeout, WRAPPER_TIMEOUT_SECONDS, audit.budget["expires_at"] - time.time())
            if timeout <= 0:
                raise ContractError("case deadline expired before dispatch")
        raw = transport(upstream, prompt, timeout, MAX_BODY)
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
        observed_tool_markers = tool_markers(stderr)
        if reject_tool_use and observed_tool_markers:
            stage = "forbidden_tool_use_observed"
            raise ContractError("forbidden tool use observed")
        model_name = reported_model(stderr)
        if audit and model_name != audit.budget["model"]:
            raise ContractError("unexpected or absent wrapper model identifier")
        result = {"invocation_id": invocation, "status": "completed", "output": output, "usage": None}
        if audit:
            audit.append(
                {
                    "event": "completion",
                    **context,
                    "response_sha256": digest(result),
                    "output_sha256": digest(output),
                    "wrapper": {
                        "ok": True,
                        "exit_code": 0,
                        "model": model_name,
                        "response_bytes": len(raw),
                        "response_sha256": hashlib.sha256(raw).hexdigest(),
                        "output_text_sha256": hashlib.sha256(wrapper["output"].encode()).hexdigest(),
                        "stderr_sha256": stderr_sha256(stderr),
                        "cli_reported_tokens": reported_tokens(stderr),
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
            timeout_error = isinstance(error, TimeoutError) or (
                isinstance(error, URLError) and isinstance(error.reason, TimeoutError)
            )
            audit.fail(
                context,
                "timeout_unknown_outcome" if timeout_error else stage,
                outcome="unknown" if context else "not_dispatched",
            )
        raise


def make_handler(catalog, upstream, timeout, audit_path, budget=None, reject_tool_use=False, *, transport):
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
                result = execute(
                    data,
                    catalog,
                    upstream,
                    timeout,
                    audit=dispatch,
                    reject_tool_use=reject_tool_use,
                    transport=transport,
                )
                status = 200
            except TimeoutError, HTTPError, URLError:
                status, result = 502, {"status": "failed", "error": "upstream_transport"}
            except ContractError as error:
                # Messages are adapter-owned labels or schema paths, never model output or stderr.
                status, result = 422, {"status": "failed", "error": "contract_rejected", "detail": str(error)}
            except ValueError:
                status, result = (
                    422,
                    {"status": "failed", "error": "contract_rejected", "detail": "invalid request length"},
                )
            except Exception:  # Server boundary: a 500, never a hung n8n node
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
                        "inputs_sha256": digest(data.get("inputs")),
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


def start_bridge(
    host: str,
    port: int,
    upstream: str,
    audit: Path,
    budget: Path,
    log: Path,
    bindings: Path,
    reject_tool_use: bool = False,
) -> subprocess.Popen:
    """Start the bridge on `host:port` as a child process and wait for /health; fails if the port is taken."""
    with log.open("w") as stream:
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "sapi_config_lab.harbor_integration.model_wrapper",
                "--host",
                host,
                "--port",
                str(port),
            ]
            + ["--upstream", upstream]
            + ["--timeout", str(WRAPPER_TIMEOUT_SECONDS), "--audit", str(audit), "--budget", str(budget)]
            + ["--bindings", str(bindings)]
            + (["--reject-tool-use"] if reject_tool_use else []),
            cwd=workspace_root(),
            stdout=stream,
            stderr=subprocess.STDOUT,
        )
    for _ in range(50):
        if process.poll() is not None:
            raise RuntimeError("Agency adapter exited before readiness")
        try:
            with urlopen(f"http://{local_address(host)}:{port}/health", timeout=1) as response:
                if json.load(response).get("service") == "sapi-lab-agency-adapter":
                    break
        except URLError, TimeoutError:
            time.sleep(0.1)
    else:
        stop_bridge(process)
        raise RuntimeError("Agency adapter readiness timeout")
    time.sleep(0.1)
    if process.poll() is not None:
        raise RuntimeError("Agency adapter port is already in use")
    return process


def stop_bridge(process: subprocess.Popen | None) -> None:
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)
