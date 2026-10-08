"""Checkout tool protocol inside a fresh Harbor-owned simulator service."""

from __future__ import annotations

import copy
from datetime import UTC, datetime
import hashlib
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import secrets
import threading
import time
import uuid

from sapi_config_lab.contracts import Document

MAX_BODY = 2_000_000
MAX_TOOL_ATTEMPTS = 3
CHALLENGE = "production-checkout-recovery"


def now() -> str:
    return datetime.now(UTC).isoformat()


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


class World:
    """Run-scoped receipts and semantic finalization, with no container lifecycle."""

    def __init__(self, world, definition: dict):
        self.world, self.definition = world, definition
        self.limit_seconds = definition["limits"]["wall_clock_seconds"]
        self._candidate_token, self._admin_token = (secrets.token_urlsafe(32) for _ in range(2))
        self._lock = threading.RLock()
        self._evidence: Document | None = None
        self._receipts: dict[str, Document] = {}
        self._transport_events: list[Document] = []
        self._ambiguous = self._frozen = False
        self.started: float | None = None
        self.window: Document | None = None
        self._snapshot: Document | None = None

    def prepare(self) -> Document:
        with self._lock:
            if self.started is not None:
                raise ValueError("Workflow window already bound")
            self.started = time.monotonic()
            self.window = {
                "run_id": "trial-" + uuid.uuid4().hex,
                "started_at": now(),
                "deadline_at": time.time() + self.limit_seconds,
                "limit_seconds": self.limit_seconds,
            }
            return copy.deepcopy(self.window)

    def snapshot(self) -> Document:
        with self._lock:
            if self.started is None or self.window is None:
                raise ValueError("Workflow window is not bound")
            if self._snapshot is None:
                self._snapshot = {
                    "environment": self.finalize(),
                    "transport": self.transport_evidence(),
                    "window": {
                        **self.window,
                        "finished_at": now(),
                        "duration_seconds": time.monotonic() - self.started,
                    },
                }
            return copy.deepcopy(self._snapshot)

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
            if self.started is None:
                return failure("RUN_NOT_STARTED", "Workflow window is not bound")
            if time.monotonic() - self.started > self.limit_seconds:
                self._frozen = True
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
                    result = self.world.execute(operation, arguments)
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
            self._frozen = True
            self._evidence = self.world.finalize()
            return copy.deepcopy(self._evidence)


def handler_for(world: World):
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

        def do_GET(self):
            return (
                self.reply(200, {"ready": True}) if self.path == "/health" else self.reply(404, {"error": "Not found"})
            )

        def do_POST(self):
            if self.path not in {"/tools", "/prepare", "/snapshot"}:
                return self.reply(404, {"error": "Not found"})
            token = world._candidate_token if self.path == "/tools" else world._admin_token
            if not hmac.compare_digest(self.headers.get("Authorization", ""), "Bearer " + token):
                return self.reply(401, {"error": "Unauthorized"})
            try:
                size = int(self.headers.get("Content-Length", "-1"))
                if not 0 < size <= MAX_BODY:
                    return self.reply(413, {"error": "Invalid request size"})
                self.connection.settimeout(15)
                request = json.loads(self.rfile.read(size))
                if self.path != "/tools":
                    if request != {}:
                        return self.reply(400, {"error": "Expected empty request"})
                    return self.reply(200, world.prepare() if self.path == "/prepare" else world.snapshot())
                if (
                    not isinstance(request, dict)
                    or not {"operation", "arguments"} <= set(request)
                    or set(request) - {"operation", "arguments", "operation_id", "max_attempts"}
                ):
                    return self.reply(400, {"error": "Supply operation and arguments"})
                return self.reply(
                    200,
                    world.call(
                        request["operation"],
                        request["arguments"],
                        operation_id=request.get("operation_id"),
                        max_attempts=request.get("max_attempts", 1),
                    ),
                )
            except ValueError, TypeError, TimeoutError:
                return self.reply(400, {"error": "Invalid tool request"})
            except OSError, RuntimeError:
                return self.reply(503, {"error": "Environment unavailable"})

    return Handler


def main() -> None:
    from autowfbench.core.contracts import load_challenge
    from autowfbench.runtime.environment import ChallengeEnvironment

    package = load_challenge(CHALLENGE)
    world = World(ChallengeEnvironment(package, 0), package["definition"])
    credentials = Path("/run/checkout")
    credentials.mkdir(parents=True, exist_ok=True)
    credentials.chmod(0o700)
    temporary = credentials / "credentials.tmp"
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w") as handle:
        json.dump({"tool_token": world._candidate_token, "admin_token": world._admin_token}, handle)
    temporary.replace(credentials / "credentials.json")
    server = ThreadingHTTPServer(("0.0.0.0", 8000), handler_for(world))
    server.daemon_threads = True
    server.serve_forever()


if __name__ == "__main__":
    main()
