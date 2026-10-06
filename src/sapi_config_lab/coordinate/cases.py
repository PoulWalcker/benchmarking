"""Compile and execute a workflow through the selected backend; never grade it."""

from __future__ import annotations

import argparse
import copy
from dataclasses import replace
import json
from pathlib import Path
import time

from sapi_config_lab import profile
from sapi_config_lab.contracts import (
    ArtifactTransform,
    CompileOptions,
    Document,
    ExecutionRecord,
    LlmMode,
    RunBinding,
    WorkflowBackend,
)
from sapi_config_lab.coordinate.backend import default_backend
from sapi_config_lab.evidence import write_record_json
from sapi_config_lab.paths import CATALOG


def run_case(
    config: Document,
    artifact_dir: Path,
    *,
    llm_mode: LlmMode = "stub",
    bridge_url: str | None = None,
    artifact_transform: ArtifactTransform | None = None,
    backend: WorkflowBackend | None = None,
    admission: Document | None = None,
    deadline_at: float | None = None,
    bindings: Document | None = None,
    operation_url: str | None = None,
    operation_token: str | None = None,
) -> ExecutionRecord:
    """Compile and execute one run and record it; acceptance stays `not_evaluated` for the verifier.

    artifact_transform deliberately corrupts the artifact for verifier probes.
    """
    selected = backend if backend is not None else default_backend()
    artifact_dir = Path(artifact_dir).resolve()
    artifact_dir.mkdir(parents=True, exist_ok=True)
    config = copy.deepcopy(config)
    binding = RunBinding(deadline_at, admission, operation_token)
    started = time.monotonic()
    record: ExecutionRecord = {
        "status": "compile_error",
        "output": None,
        "execution_id": None,
        "workflow_id": None,
        "engine_version": None,
        "mapping": {},
        "error": None,
    }
    try:
        profile.json_value(config, "config")
        write_record_json(artifact_dir / "config.json", config)
        compiled = selected.compile(
            config,
            bindings if bindings is not None else profile.read_bindings(CATALOG),
            CompileOptions(
                llm_mode,
                bridge_url,
                operation_url=operation_url,
                activation="event" if admission is not None else "fixture",
                bound_deadline=deadline_at is not None,
            ),
        )
        if artifact_transform:
            compiled = replace(compiled, document=artifact_transform(copy.deepcopy(compiled.document)))
    except (profile.Invalid, profile.Unsupported) as error:
        record["error"] = {"category": "compile_error", "type": type(error).__name__, "message": str(error)}
    else:
        try:
            record = selected.execute(compiled, artifact_dir, binding)
        except (OSError, RuntimeError) as error:
            record.update(
                {
                    "status": "engine_error",
                    "error": {"category": "engine_error", "type": type(error).__name__, "message": str(error)},
                }
            )
    record.update(
        {
            "report_schema": "sapi-lab-execution/v1",
            "artifact_dir": str(artifact_dir),
            "duration_seconds": round(time.monotonic() - started, 3),
            "llm_mode": llm_mode,
            "execution": {
                "status": record["status"],
                "succeeded": record["status"] == "success",
                "engine": {"name": selected.name, "version": record.get("engine_version")},
            },
            "acceptance": {"status": "not_evaluated", "passed": None},
            "input": {
                "source": "event" if admission else "fixture",
                "activation": copy.deepcopy(admission) if admission else "injected",
                "config_artifact": "config.json" if (artifact_dir / "config.json").exists() else None,
            },
        }
    )
    record.setdefault(
        "llm",
        {
            "selected_mode": llm_mode,
            "agency_http_call_count": 0 if record["status"] == "compile_error" else None,
            "count_source": "not_executed" if record["status"] == "compile_error" else "unavailable",
            "provider_call_count": 0 if record["status"] == "compile_error" else None,
        },
    )
    record.setdefault("evidence", {"engine": None, "workflow_trace": None})
    write_record_json(artifact_dir / "case.json", record)
    return record


def main(argv: list[str] | None = None, *, backend: WorkflowBackend | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--artifacts", type=Path, required=True)
    parser.add_argument("--llm-mode", choices=["stub", "live"], default="stub")
    parser.add_argument("--bridge-url")
    args = parser.parse_args(argv)
    record = run_case(
        profile.read(args.config), args.artifacts, llm_mode=args.llm_mode, bridge_url=args.bridge_url, backend=backend
    )
    print(
        json.dumps(
            {
                key: record.get(key)
                for key in ["status", "execution", "acceptance", "workflow_id", "execution_id", "error"]
            }
        )
    )
    return 0 if record["status"] == "success" else 1


if __name__ == "__main__":
    raise SystemExit(main())
