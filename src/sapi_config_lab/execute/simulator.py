"""A hosted simulator environment: a pinned upstream simulator behind a candidate-only tool listener.

The trusted host owns startup, finalization and evidence. Candidates reach a separately
authenticated listener that exposes tools, never the simulator's admin methods.
"""

from __future__ import annotations

from collections.abc import Callable
import copy
from datetime import UTC, datetime
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
import time
from typing import Any
from urllib.request import Request
import uuid

from sapi_config_lab.contracts import OutputArtifact
from sapi_config_lab.evidence import write_json
from sapi_config_lab.execute.host import HostConfig
from sapi_config_lab.execute.n8n import PINNED_N8N_VERSION
from sapi_config_lab.net import urlopen
from sapi_config_lab.pinned_source import PinnedSource

Document = dict[str, Any]
MAX_BODY = 2_000_000
MAX_TOOL_ATTEMPTS = 3
STARTUP_SECONDS = 15


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


def _valid_receipt(result: Document) -> bool:
    if type(result.get("ok")) is not bool or set(result) != ({"ok", "value"} if result["ok"] else {"ok", "error"}):
        return False
    error = result.get("error")
    return result["ok"] or (
        isinstance(error, dict)
        and set(error) == {"code", "message", "retryable"}
        and isinstance(error["code"], str)
        and isinstance(error["message"], str)
        and type(error["retryable"]) is bool
    )


class EnvironmentSession:
    """One fresh simulator plus its tool listener. A new session is the only reset; nothing is reused."""

    def __init__(
        self, source: PinnedSource, challenge_id: str, seed: int, *, python: str, bind_host: str, public_host: str
    ):
        self.source = source.verify()
        if f"benchmark/challenges/{challenge_id}/definition.json" not in self.source["files"]:
            raise ValueError("Unknown pinned challenge")
        if type(seed) is not int or seed < 0:
            raise ValueError("Seed must be a nonnegative integer")
        root = source.root.resolve()
        self.definition = json.loads((root / "benchmark/challenges" / challenge_id / "definition.json").read_text())
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
            "PYTHONPATH": str(root),
            "AUTOWFBENCH_ROOT": str(root),
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONNOUSERSITE": "1",
            "AWB_RUN_TOKEN": self._run_token,
            "AWB_ENV_ADMIN_TOKEN": self._admin_token,
        }
        self._process = subprocess.Popen(
            [python, "-m", "autowfbench", "environment", challenge_id, "--seed", str(seed), "--host", "127.0.0.1"],
            cwd=root,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            assert self._process.stdout is not None
            with selectors.DefaultSelector() as selector:
                selector.register(self._process.stdout, selectors.EVENT_READ)
                if not selector.select(timeout=STARTUP_SECONDS):
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
        """Run-scoped candidate credentials; never persisted in public artifacts."""
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
        """Dispatch once; keyed retries only after a known retryable error, never after an unknown outcome.

        This is project transport policy, not a change to the upstream tool or rubric.
        """
        arguments = {} if arguments is None else copy.deepcopy(arguments)
        if not isinstance(operation, str) or not isinstance(arguments, dict):
            raise ValueError("Invalid tool request")
        if operation_id is not None and (not isinstance(operation_id, str) or not 1 <= len(operation_id) <= 240):
            raise ValueError("Invalid operation ID")
        if (
            type(max_attempts) is not int
            or not 1 <= max_attempts <= MAX_TOOL_ATTEMPTS
            or (max_attempts > 1 and operation_id is None)
        ):
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
                return failure("RUN_CLOSED", "Run is frozen")
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
                    if not _valid_receipt(result):
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
        """Project-policy evidence, kept apart from the simulator's own checks."""
        with self._lock:
            return {
                "policy": "sapi-lab-run-scoped-receipts/v1",
                "durability": "live-session-only",
                "ambiguous_outcome": self._ambiguous,
                "events": copy.deepcopy(self._transport_events),
            }

    def finalize(self) -> Document:
        """Freeze the run and collect the simulator's state, events and checks once."""
        with self._lock:
            if self._evidence is not None:
                return copy.deepcopy(self._evidence)
            if self._closed:
                raise RuntimeError("Environment session is closed")
            self._frozen = True
            self._evidence = _post(self._upstream_url + "/admin/finalize", {}, self._admin_token)
            return copy.deepcopy(self._evidence)

    def close(self) -> None:
        """Stop listener and simulator; safe after partial startup and on repeated calls."""
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
    source: PinnedSource,
    challenge_id: str = "production-checkout-recovery",
    seed: int = 0,
    *,
    python: str = sys.executable,
    bind_host: str = "127.0.0.1",
    public_host: str = "127.0.0.1",
) -> EnvironmentSession:
    """A verified simulator with fresh state. Its admin listener stays on loopback whatever bind_host is."""
    return EnvironmentSession(source, challenge_id, seed, python=python, bind_host=bind_host, public_host=public_host)


def terminal_submission(
    record: Document, elapsed: float, run_id: str, *, limit: float, artifact: OutputArtifact | None
) -> tuple[str, Document | None]:
    """Only timely, structurally valid terminal output is admitted as completion."""
    if elapsed > limit:
        return "timeout", None
    if record.get("status") != "success":
        return "solution_failed", None
    answer = record.get("output")
    if not (
        isinstance(answer, dict)
        and isinstance(answer.get("final_answer"), str)
        and (artifact is None or isinstance(answer.get(artifact.field), str))
    ):
        return "protocol_error", None
    return "completed", {
        "protocol_version": "1.0",
        "run_id": run_id,
        "status": "completed",
        "final_answer": answer["final_answer"],
        "artifacts": (
            [{"name": artifact.name, "media_type": "text/markdown", "content": answer[artifact.field]}]
            if artifact
            else []
        ),
        "trace": [],
    }


def _now() -> str:
    return datetime.now(UTC).isoformat()


class SimulatorHost(ThreadingHTTPServer):
    """One trial's trusted endpoint: a fresh simulator on /begin, recorded and evaluated evidence on /finish."""

    daemon_threads = True

    def __init__(
        self,
        source: PinnedSource,
        challenge_id: str,
        record: Path,
        *,
        seed: int,
        artifact: OutputArtifact | None,
        llm_mode: str,
        bridge_url: str | None,
        evaluate: Callable[[Path], Document],
        host: HostConfig,
    ):
        super().__init__((host.listen_host, 0), _HostHandler)
        self.source, self.challenge_id, self.record, self.seed = source, challenge_id, record, seed
        self.artifact, self.llm_mode, self.bridge_url, self.evaluate = artifact, llm_mode, bridge_url, evaluate
        self.host = host
        self.token = secrets.token_urlsafe(32)
        self.run_id = "trial-" + uuid.uuid4().hex
        self.session: EnvironmentSession | None = None
        self.operation_token: str | None = None
        self.timer: threading.Timer | None = None
        self.started: float | None = None
        self.started_at: str | None = None
        self.limit = 0
        self.finished = False
        self.thread = threading.Thread(target=self.serve_forever, daemon=True)
        self.thread.start()

    @property
    def connection(self) -> Document:
        return {"url": f"http://{self.host.container_host}:{self.server_port}", "token": self.token}

    def credentials(self) -> list[str]:
        """Ephemeral credentials that must never appear in persisted artifacts."""
        return [self.token] + ([self.operation_token] if self.operation_token else [])

    def begin(self) -> Document:
        if self.started is not None:
            raise ValueError("Trial already started")
        self.session = start_environment(
            self.source,
            self.challenge_id,
            self.seed,
            bind_host=self.host.listen_host,
            public_host=self.host.container_host,
        )
        self.operation_token = self.session.connection["access_token"]
        self.limit = self.session.definition["limits"]["wall_clock_seconds"]
        self.started, self.started_at = time.monotonic(), _now()
        deadline = time.time() + self.limit
        self.timer = threading.Timer(self.limit, self.session.finalize)
        self.timer.start()
        return {
            "llm_mode": self.llm_mode,
            "bridge_url": self.bridge_url,
            "operation_url": self.session.connection["base_url"] + "/tools",
            "operation_token": self.session.connection["access_token"],
            "deadline_at": deadline,
        }

    def finish(self, record: Document, submission_sha256: str | None) -> Document:
        if self.finished or self.session is None or self.started is None or self.timer is None:
            raise ValueError("Trial not started or already finalized")
        self.finished = True
        elapsed = time.monotonic() - self.started
        self.timer.cancel()
        evidence = self.record / "evidence"
        evidence.mkdir(parents=True, exist_ok=False)
        write_json(evidence / "environment-evidence.json", self.session.finalize())
        write_json(evidence / "transport-evidence.json", self.session.transport_evidence())
        write_json(evidence / "native-record.json", record)
        reason, submission = terminal_submission(record, elapsed, self.run_id, limit=self.limit, artifact=self.artifact)
        trial = {
            "schema": "sapi-lab-simulator-trial/v1",
            "challenge": self.challenge_id,
            "run_id": self.run_id,
            "seed": self.seed,
            "llm_mode": self.llm_mode,
            "started_at": self.started_at,
            "finished_at": _now(),
            "duration_seconds": elapsed,
            "termination_reason": reason,
            "submission": submission,
            "submission_sha256": submission_sha256,
            "solution": {
                "id": "sapi-lab-yaml",
                "name": "Frozen YAML in real n8n",
                "version": "1",
                "runtime": f"n8n-{PINNED_N8N_VERSION}",
            },
        }
        write_json(evidence / "trial.json", trial)
        return self.evaluate(self.record)

    def close(self) -> None:
        if self.timer is not None:
            self.timer.cancel()
        if self.session is not None:
            self.session.close()
        self.shutdown()
        self.server_close()
        self.thread.join(timeout=2)


class _HostHandler(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def do_POST(self):
        host = self.server
        assert isinstance(host, SimulatorHost)
        try:
            if not hmac.compare_digest(self.headers.get("Authorization", ""), "Bearer " + host.token):
                raise ValueError("Unauthorized trusted verifier")
            size = int(self.headers.get("Content-Length", "0"))
            if not 0 < size < 4_000_000:
                raise ValueError("Invalid request size")
            data = json.loads(self.rfile.read(size))
            if self.path == "/begin":
                result = host.begin()
            elif self.path == "/finish":
                result = host.finish(data["record"], data.get("submission_sha256"))
            else:
                raise ValueError("Unknown route")
            raw, status = json.dumps(result).encode(), 200
        except Exception as error:  # Server boundary: a recorded host error, never a hung trial
            host.record.mkdir(parents=True, exist_ok=True)
            write_json(host.record / "host-error.json", {"type": type(error).__name__, "message": str(error)})
            raw, status = json.dumps({"error": type(error).__name__}).encode(), 500
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)
