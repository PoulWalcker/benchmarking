"""The trusted trial host: one fresh environment session per trial, recorded and evaluated on finish.

It is provider-neutral; a provider supplies the session and the host owns the trial's token,
deadline and evidence files.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import secrets
import threading
import time
from typing import Protocol
import uuid

from sapi_config_lab.contracts import Document, OutputArtifact
from sapi_config_lab.evidence import write_json
from sapi_config_lab.execute.host import HostConfig
from sapi_config_lab.execute.n8n import PINNED_N8N_VERSION


class EnvironmentSession(Protocol):
    """One fresh world for one trial; closing it is the only reset."""

    @property
    def connection(self) -> Document:
        """Candidate credentials: `base_url` and `access_token`."""
        ...

    @property
    def limit_seconds(self) -> int:
        """The trial's wall-clock limit."""
        ...

    @property
    def identity(self) -> Document:
        """Provider fields merged into trial.json."""
        ...

    def finalize(self) -> Document:
        """Freeze the world and return its evidence; repeated calls return the same evidence."""
        ...

    def transport_evidence(self) -> Document:
        """Project transport-policy evidence, kept apart from the world's own."""
        ...

    def close(self) -> None:
        """Stop the world; safe to repeat."""
        ...


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


class TrialHost(ThreadingHTTPServer):
    """One trial's trusted endpoint: a fresh session on /begin, recorded and evaluated evidence on /finish."""

    daemon_threads = True

    def __init__(
        self,
        start: Callable[[], EnvironmentSession],
        record: Path,
        *,
        scenario: str,
        seed: int,
        artifact: OutputArtifact | None,
        llm_mode: str,
        bridge_url: str | None,
        evaluate: Callable[[Path], Document],
        host: HostConfig,
    ):
        super().__init__((host.listen_host, 0), _HostHandler)
        self.start, self.record, self.scenario, self.seed = start, record, scenario, seed
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
        self.session = self.start()
        self.operation_token = self.session.connection["access_token"]
        self.limit = self.session.limit_seconds
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
            "scenario": self.scenario,
            **self.session.identity,
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
        assert isinstance(host, TrialHost)
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
