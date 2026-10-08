"""Bind checkout's workflow window and preserve its terminal evidence contract."""

from collections.abc import Mapping
import hashlib
import json
import os
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener
import uuid

from sapi_config_lab.contracts import RunBinding
from sapi_config_lab.net import NoRedirect
from sapi_config_lab.profile import read

MAX_BODY = 2_000_000


def plan(submission: Path, options: Mapping) -> dict:
    config = read(submission)
    if not isinstance(config, dict):
        raise ValueError("Submission must be a workflow document")
    return {
        "schema": "sapi-lab-observation-plan/v1",
        "scenario": "checkout-recovery",
        "mode": options.get("mode", "stub"),
        "entries": [{"name": "workflow", "procedure": "case", "config": config}],
    }


def _credentials(context: Mapping) -> dict:
    path = Path(context["options"].get("world_credentials", "/run/checkout/credentials.json"))
    return json.loads(path.read_text())


def _post(context: Mapping, route: str) -> dict:
    base = context["options"].get("world_url", "http://simulator:8000")
    request = Request(
        base + route,
        data=b"{}",
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + _credentials(context)["admin_token"]},
        method="POST",
    )
    try:
        with build_opener(ProxyHandler({}), NoRedirect).open(request, timeout=15) as response:
            raw = response.read(MAX_BODY + 1)
    except HTTPError as error:
        error.close()
        raise
    if len(raw) > MAX_BODY:
        raise ValueError("Simulator response exceeds protocol limit")
    result = json.loads(raw)
    if not isinstance(result, dict):
        raise ValueError("Simulator response must be an object")
    return result


def prepare(context: Mapping) -> RunBinding:
    window = _post(context, "/prepare")
    evidence = Path(context["evidence"])
    evidence.mkdir(parents=True, exist_ok=True)
    _record_once(evidence / "window.json", window)
    return RunBinding(
        deadline_at=window["deadline_at"],
        operation_url=context["options"].get("world_url", "http://simulator:8000") + "/tools",
        operation_token=_credentials(context)["tool_token"],
    )


def terminal_submission(record: dict, elapsed: float, run_id: str, limit: float) -> tuple[str, dict | None]:
    if elapsed >= limit:
        return "timeout", None
    if record.get("status") != "success":
        return "solution_failed", None
    answer = record.get("output")
    if not (
        isinstance(answer, dict)
        and isinstance(answer.get("final_answer"), str)
        and isinstance(answer.get("incident_summary"), str)
    ):
        return "protocol_error", None
    return "completed", {
        "protocol_version": "1.0",
        "run_id": run_id,
        "status": "completed",
        "final_answer": answer["final_answer"],
        "artifacts": [
            {"name": "incident-summary.md", "media_type": "text/markdown", "content": answer["incident_summary"]}
        ],
        "trace": [],
    }


def _record_once(path: Path, value: dict) -> None:
    data = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False) + "\n"
    if path.exists():
        if path.read_text() != data:
            raise ValueError("Recorded evidence cannot be rewritten")
        return
    temporary = path.with_suffix(path.suffix + "." + uuid.uuid4().hex + ".tmp")
    with temporary.open("x") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    try:
        os.link(temporary, path)
    except FileExistsError:
        if path.read_text() != data:
            raise ValueError("Recorded evidence cannot be rewritten") from None
    finally:
        temporary.unlink()
    descriptor = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def snapshot(context: Mapping) -> dict:
    evidence = Path(context["evidence"])
    evidence.mkdir(parents=True, exist_ok=True)
    if (evidence / "trial.json").exists():
        return json.loads((evidence / "trial.json").read_text())
    try:
        frozen = _post(context, "/snapshot")
    except (OSError, ValueError) as error:
        # The native output mount retains observations even if the service is gone.
        snapshots = sorted((Path(context.get("output", evidence.parent)) / "world").glob("*-snapshot.json"))
        if snapshots:
            frozen = json.loads(snapshots[-1].read_text())
        else:
            window = json.loads((evidence / "window.json").read_text())
            frozen = {
                "window": {
                    **window,
                    "finished_at": None,
                    "duration_seconds": None,
                    "termination_reason": "unknown",
                },
                "environment": None,
                "transport": None,
                "evidence_errors": [{"source": "snapshot", "error": type(error).__name__}],
            }
    window = frozen["window"]
    record = context.get("record") or {"status": "unknown", "output": None}
    if window["duration_seconds"] is None:
        reason, submission = "unknown", None
    elif window.get("termination_reason") == "timeout":
        reason, submission = "timeout", None
    else:
        reason, submission = terminal_submission(
            record, window["duration_seconds"], window["run_id"], window["limit_seconds"]
        )
    source = Path(context["submission"])
    trial = {
        "schema": "sapi-lab-simulator-trial/v1",
        "scenario": "checkout-recovery",
        "challenge": "production-checkout-recovery",
        "run_id": window["run_id"],
        "seed": 0,
        "llm_mode": context["options"].get("mode", "stub"),
        "started_at": window["started_at"],
        "finished_at": window["finished_at"],
        "duration_seconds": window["duration_seconds"],
        "termination_reason": reason,
        "interruption": None,
        "native_execution": None if record.get("status") == "unknown" else record.get("status") == "success",
        "terminal_completion": reason == "completed",
        "submission": submission,
        "submission_sha256": hashlib.sha256(source.read_bytes()).hexdigest() if source.is_file() else None,
        "evidence_errors": frozen.get("evidence_errors", []),
        "solution": {
            "id": "sapi-lab-yaml",
            "name": "Frozen YAML in real n8n",
            "version": "1",
            "runtime": "n8n-" + (record.get("n8n_version") or record.get("engine_version") or "2.41.5"),
        },
    }
    for name, key in (("environment-evidence.json", "environment"), ("transport-evidence.json", "transport")):
        if frozen.get(key) is not None:
            _record_once(evidence / name, frozen[key])
    _record_once(evidence / "native-record.json", record)
    _record_once(evidence / "trial.json", trial)
    return trial
