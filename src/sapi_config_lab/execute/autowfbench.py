"""Pinned AutoWFBench dependency and a candidate-only tool interface.

The trusted caller owns source files, environment startup and finalization.
Candidates receive a distinct HTTP listener exposing tools, never admin methods.
The original upstream simulator owns state, limits, event recording and checks.
"""

from __future__ import annotations

import copy
import hashlib
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import secrets
import selectors
import subprocess
import sys
import threading
from typing import Any
from urllib.request import Request

from sapi_config_lab.autowfbench_source import verify_source
from sapi_config_lab.net import urlopen

Document = dict[str, Any]
MAX_BODY = 2_000_000


def _post(url: str, body: Document, token: str, timeout: float = 15) -> Document:
    request = Request(
        url,
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + token},
        method="POST",
    )
    with urlopen(request, timeout=timeout) as response:
        raw = response.read(MAX_BODY + 1)
    if len(raw) > MAX_BODY:
        raise ValueError("Environment response exceeds protocol limit")
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("Environment response must be an object")
    return value


class EnvironmentSession:
    """One fresh original simulator plus a separately authenticated tools listener.

    Construct through start_environment. connection contains only candidate
    credentials; finalize and close belong to the trusted orchestrator. A new
    session is the reset operation. No state is reused across sessions.
    """

    def __init__(
        self, source_root: Path, challenge_id: str, seed: int, *, python: str, bind_host: str, public_host: str
    ):
        self.source = verify_source(source_root)
        challenge = source_root / "benchmark/challenges" / challenge_id / "definition.json"
        if challenge_id not in {"crm-lead-qualification", "production-checkout-recovery"}:
            raise ValueError("Unknown pinned challenge")
        if type(seed) is not int or seed < 0:
            raise ValueError("Seed must be a nonnegative integer")
        self.definition = json.loads(challenge.read_text())
        self.challenge_id, self.seed = challenge_id, seed
        self._run_token, self._admin_token, self._candidate_token = (secrets.token_urlsafe(32) for _ in range(3))
        self._lock = threading.RLock()
        self._evidence: Document | None = None
        self._receipts: dict[str, Document] = {}
        self._transport_events: list[Document] = []
        self._ambiguous = False
        self._closed = False
        self._frozen = False
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        # Never pass host credentials or a caller's PYTHONPATH to the simulator.
        env = {
            "PATH": os.defpath,
            "PYTHONPATH": str(source_root),
            "AUTOWFBENCH_ROOT": str(source_root),
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONNOUSERSITE": "1",
            "AWB_RUN_TOKEN": self._run_token,
            "AWB_ENV_ADMIN_TOKEN": self._admin_token,
        }
        self._process = subprocess.Popen(
            [python, "-m", "autowfbench", "environment", challenge_id, "--seed", str(seed), "--host", "127.0.0.1"],
            cwd=source_root,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            assert self._process.stdout is not None
            with selectors.DefaultSelector() as selector:
                selector.register(self._process.stdout, selectors.EVENT_READ)
                if not selector.select(timeout=15):
                    raise RuntimeError("Environment startup timed out")
                line = self._process.stdout.readline()
            if not line:
                assert self._process.stderr is not None
                detail = self._process.stderr.read(2000)
                for token in (self._run_token, self._admin_token):
                    detail = detail.replace(token, "[REDACTED]")
                raise RuntimeError("Pinned environment did not start: " + detail.strip())
            port = json.loads(line)["port"]
            if type(port) is not int or not 0 < port < 65536:
                raise ValueError("Invalid environment startup response")
            self._upstream_url = f"http://127.0.0.1:{port}"
            self._server = ThreadingHTTPServer((bind_host, 0), self._handler())
            self._server.daemon_threads = True
            self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
            self._thread.start()
            self._connection = {
                "base_url": f"http://{public_host}:{self._server.server_port}",
                "access_token": self._candidate_token,
            }
        except BaseException:
            self.close()
            raise

    @property
    def connection(self) -> Document:
        """Run-scoped candidate credentials. Do not persist them in public artifacts."""
        if self._closed:
            raise RuntimeError("Environment session is closed")
        return dict(self._connection)

    def _handler(self):
        session = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def reply(self, status, value):
                body = json.dumps(value).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self):
                if self.path != "/tools":
                    return self.reply(404, {"error": "Not found"})
                if not hmac.compare_digest(self.headers.get("Authorization", ""), "Bearer " + session._candidate_token):
                    return self.reply(401, {"error": "Unauthorized"})
                try:
                    size = int(self.headers.get("Content-Length", "-1"))
                    if not 0 < size <= MAX_BODY:
                        return self.reply(413, {"error": "Invalid request size"})
                    self.connection.settimeout(15)
                    request = json.loads(self.rfile.read(size))
                    if (
                        not isinstance(request, dict)
                        or not {"operation", "arguments"} <= set(request)
                        or set(request) - {"operation", "arguments", "operation_id", "max_attempts"}
                    ):
                        return self.reply(400, {"error": "Supply operation and arguments"})
                    if not isinstance(request["operation"], str) or not isinstance(request["arguments"], dict):
                        return self.reply(400, {"error": "Invalid tool request"})
                    result = session.call(
                        request["operation"],
                        request["arguments"],
                        operation_id=request.get("operation_id"),
                        max_attempts=request.get("max_attempts", 1),
                    )
                    return self.reply(200, result)
                except ValueError, TypeError, TimeoutError:
                    return self.reply(400, {"error": "Invalid tool request"})
                except OSError, RuntimeError:
                    return self.reply(503, {"error": "Environment unavailable"})

            def do_GET(self):
                # Even authenticated candidates cannot inspect state or finalize.
                return self.reply(404, {"error": "Not found"})

        return Handler

    def call(
        self,
        operation: str,
        arguments: Document | None = None,
        *,
        operation_id: str | None = None,
        max_attempts: int = 1,
    ) -> Document:
        """Dispatch once by default; keyed explicit retries require a known safe error.

        This is a project transport policy, not a change to the upstream tool or
        rubric. A known receipt can be replayed within this live session. An
        uncertain upstream response stops further calls; it is never retried.
        """
        arguments = {} if arguments is None else copy.deepcopy(arguments)
        if not isinstance(operation, str) or not isinstance(arguments, dict):
            raise ValueError("Invalid tool request")
        if operation_id is not None and (not isinstance(operation_id, str) or not 1 <= len(operation_id) <= 240):
            raise ValueError("Invalid operation ID")
        if type(max_attempts) is not int or not 1 <= max_attempts <= 3 or (max_attempts > 1 and operation_id is None):
            raise ValueError("Explicit retries require an operation ID and one to three attempts")
        encoded = json.dumps(
            {"operation": operation, "arguments": arguments, "max_attempts": max_attempts},
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        request_digest = hashlib.sha256(encoded.encode()).hexdigest()

        def failure(code: str, message: str) -> Document:
            return {"ok": False, "error": {"code": code, "message": message, "retryable": False}}

        with self._lock:
            if self._closed:
                raise RuntimeError("Environment session is closed")
            if self._frozen:
                return {"ok": False, "error": {"code": "RUN_CLOSED", "message": "Run is frozen", "retryable": False}}
            if operation_id is not None and operation_id in self._receipts:
                prior = self._receipts[operation_id]
                if prior["request_digest"] != request_digest:
                    return failure(
                        "PROJECT_OPERATION_ID_CONFLICT", "Operation ID was already reserved for a different request"
                    )
                self._transport_events.append({"kind": "receipt_replayed", "operation_id": operation_id})
                return copy.deepcopy(prior["receipt"])
            if self._ambiguous:
                return failure(
                    "PROJECT_AMBIGUOUS_OUTCOME", "A prior tool outcome is unknown; further dispatch is stopped"
                )
            row: Document = {
                "kind": "tool_dispatch",
                "operation_id": operation_id,
                "operation": operation,
                "request_digest": request_digest,
                "max_attempts": max_attempts,
                "attempts": 0,
                "state": "reserved",
            }
            self._transport_events.append(row)
            if operation_id is not None:
                self._receipts[operation_id] = row
            for attempt in range(max_attempts):
                row["attempts"] = attempt + 1
                try:
                    result = _post(
                        self._upstream_url + "/tools", {"operation": operation, "arguments": arguments}, self._run_token
                    )
                    valid = type(result.get("ok")) is bool and set(result) == (
                        {"ok", "value"} if result["ok"] else {"ok", "error"}
                    )
                    if valid and not result["ok"]:
                        error = result["error"]
                        valid = (
                            isinstance(error, dict)
                            and set(error) == {"code", "message", "retryable"}
                            and isinstance(error["code"], str)
                            and isinstance(error["message"], str)
                            and type(error["retryable"]) is bool
                        )
                    if not valid:
                        raise ValueError("Malformed upstream receipt")
                except OSError, ValueError:
                    self._ambiguous = True
                    row.update(
                        state="ambiguous",
                        receipt=failure(
                            "PROJECT_AMBIGUOUS_OUTCOME", "Tool may have completed; no automatic retry is safe"
                        ),
                    )
                    return copy.deepcopy(row["receipt"])
                row["receipt"] = copy.deepcopy(result)
                if result["ok"] or not result["error"]["retryable"]:
                    break
            row["state"] = "received"
            return copy.deepcopy(row["receipt"])

    def transport_evidence(self) -> Document:
        """Separate project-policy evidence; never merge it into original checks."""
        with self._lock:
            return {
                "policy": "sapi-lab-run-scoped-receipts/v1",
                "durability": "live-session-only",
                "ambiguous_outcome": self._ambiguous,
                "events": copy.deepcopy(self._transport_events),
            }

    def finalize(self) -> Document:
        """Freeze and collect benchmark-owned state, events and original checks once."""
        with self._lock:
            if self._evidence is not None:
                return copy.deepcopy(self._evidence)
            if self._closed:
                raise RuntimeError("Environment session is closed")
            self._frozen = True
            self._evidence = _post(self._upstream_url + "/admin/finalize", {}, self._admin_token)
            return copy.deepcopy(self._evidence)

    def close(self) -> None:
        """Stop listeners and simulator; safe after partial startup and repeated calls."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._frozen = True
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=2)
        if self._process.poll() is None:
            self._process.terminate()
            try:
                self._process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self._process.kill()
                self._process.wait(timeout=3)
        if self._process.stdout is not None:
            self._process.stdout.close()
        if self._process.stderr is not None:
            self._process.stderr.close()

    def __enter__(self) -> EnvironmentSession:
        return self

    def __exit__(self, *_args) -> None:
        self.close()


def start_environment(
    source_root: Path,
    challenge_id: str = "production-checkout-recovery",
    seed: int = 0,
    *,
    python: str = sys.executable,
    bind_host: str = "127.0.0.1",
    public_host: str = "127.0.0.1",
) -> EnvironmentSession:
    """Start an explicitly located, verified source dependency with fresh state.

    For Docker candidates, the trusted host may explicitly bind the tool proxy
    on 0.0.0.0 and advertise host.docker.internal. The original admin listener
    remains loopback. Do not mount source cache or host paths into candidates.
    """
    return EnvironmentSession(
        Path(source_root).resolve(), challenge_id, seed, python=python, bind_host=bind_host, public_host=public_host
    )
