"""Pinned AutoWFBench dependency and a candidate-only tool interface.

The trusted caller owns source files, environment startup and finalization.
Candidates receive a distinct HTTP listener exposing tools, never admin methods.
The original upstream simulator owns state, limits, event recording and checks.
"""

from __future__ import annotations

from collections.abc import Callable
import copy
from datetime import datetime, timezone
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
import uuid
from urllib.request import Request

from sapi_config_lab.autowfbench_source import verify_source
from sapi_config_lab.evidence import write_json
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


def terminal_submission(
    record: Document, elapsed: float, run_id: str, *, limit: float, output: Document
) -> tuple[str, Document | None]:
    """Only timely, structurally valid terminal output is admitted as completion."""
    if elapsed > limit:
        return "timeout", None
    if record.get("status") != "success":
        return "solution_failed", None
    answer = record.get("output")
    field = output.get("artifact_field")
    if not (
        isinstance(answer, dict)
        and isinstance(answer.get("final_answer"), str)
        and (field is None or isinstance(answer.get(field), str))
    ):
        return "protocol_error", None
    return "completed", {
        "protocol_version": "1.0",
        "run_id": run_id,
        "status": "completed",
        "final_answer": answer["final_answer"],
        "artifacts": (
            [{"name": output["artifact_name"], "media_type": "text/markdown", "content": answer[field]}]
            if field
            else []
        ),
        "trace": [],
    }


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class SimulatorHost(ThreadingHTTPServer):
    """One trial's trusted endpoint: a fresh simulator on /begin, recorded evidence on /finish.

    The container's verifier step authenticates with `token`. `/finish` writes
    the evidence under `record/evidence` and returns what `evaluate(record)` returns.
    """

    daemon_threads = True

    def __init__(
        self,
        source_root: Path,
        challenge_id: str,
        record: Path,
        *,
        seed: int,
        output: Document,
        llm_mode: str,
        bridge_url: str | None,
        evaluate: Callable[[Path], Document],
    ):
        super().__init__(("0.0.0.0", 0), _HostHandler)
        self.source_root, self.challenge_id, self.record, self.seed = source_root, challenge_id, record, seed
        self.output, self.llm_mode, self.bridge_url, self.evaluate = output, llm_mode, bridge_url, evaluate
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
        return {"url": f"http://host.docker.internal:{self.server_port}", "token": self.token}

    def credentials(self) -> list[str]:
        """Ephemeral credentials that must never appear in persisted artifacts."""
        return [self.token] + ([self.operation_token] if self.operation_token else [])

    def begin(self) -> Document:
        if self.started is not None:
            raise ValueError("Trial already started")
        self.session = start_environment(
            self.source_root, self.challenge_id, self.seed, bind_host="0.0.0.0", public_host="host.docker.internal"
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
        reason, submission = terminal_submission(record, elapsed, self.run_id, limit=self.limit, output=self.output)
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
                "runtime": "n8n-2.41.5",
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
        except Exception as error:
            host.record.mkdir(parents=True, exist_ok=True)
            write_json(host.record / "host-error.json", {"type": type(error).__name__, "message": str(error)})
            raw, status = json.dumps({"error": type(error).__name__}).encode(), 500
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)
