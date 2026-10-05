"""Compile and execute a case in real, isolated n8n; no expected answers here."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
from pathlib import Path
import sqlite3
import subprocess
import tempfile
import time
import uuid
from typing import Any
from functools import lru_cache

from sapi_config_lab.core.contracts import CompiledWorkflow, ExecutionRecord

PINNED_N8N_VERSION = "2.41.5"


def write_json(path, value):
    Path(path).write_text(json.dumps(value, indent=2, ensure_ascii=False, default=str) + "\n")


def process(args, env, timeout):
    try:
        p = subprocess.run(args, capture_output=True, text=True, env=env, timeout=timeout)
        return p.returncode, p.stdout, p.stderr
    except subprocess.TimeoutExpired as e:

        def text(value):
            return value.decode(errors="replace") if isinstance(value, bytes) else value or ""

        return 124, text(e.stdout), text(e.stderr) + "\nHarness subprocess deadline exceeded"


@lru_cache(maxsize=1)
def version():
    result = subprocess.run(["n8n", "--version"], capture_output=True, text=True, timeout=30)
    if result.returncode or result.stdout.strip() != PINNED_N8N_VERSION:
        raise RuntimeError(f"Expected n8n {PINNED_N8N_VERSION}; got {result.stdout.strip()!r}")
    return result.stdout.strip()


def parse_cli_execution(text):
    """n8n rawOutput still has initialization/error log lines around its JSON."""
    decoder = json.JSONDecoder()
    for i, char in enumerate(text):
        if char != "{":
            continue
        try:
            value, _ = decoder.raw_decode(text[i:])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict) and isinstance(value.get("data"), dict) and "resultData" in value["data"]:
            return value
    return None


def decode_flatted(raw):
    """Decode n8n's persisted flatted execution data (including shared references)."""
    table = json.loads(raw)
    if not isinstance(table, list):
        return table
    memo: dict[int, Any] = {}

    def resolve(index):
        if index in memo:
            return memo[index]
        value = table[index]
        if isinstance(value, dict):
            target = memo[index] = {}
            target.update(
                {key: resolve(int(child)) if isinstance(child, str) else child for key, child in value.items()}
            )
        elif isinstance(value, list):
            children = memo[index] = []
            children.extend(resolve(int(child)) if isinstance(child, str) else child for child in value)
        else:
            target = memo[index] = value
        return memo[index]

    return resolve(0)


def execute_compiled(compiled: CompiledWorkflow, artifact_dir: Path) -> ExecutionRecord:
    """Import and execute in a fresh database, retaining native engine evidence."""
    artifact_dir = Path(artifact_dir).resolve()
    artifact_dir.mkdir(parents=True, exist_ok=True)
    artifact = copy.deepcopy(compiled.document)
    artifact["id"] = uuid.uuid4().hex[:16]
    artifact["active"] = False
    actual_version = version()
    record: ExecutionRecord = {
        "status": "import_error",
        "output": None,
        "execution_id": None,
        "workflow_id": artifact["id"],
        "n8n_version": actual_version,
        "engine_version": actual_version,
        "run_data": {},
        "error": None,
        "import_exit_code": None,
        "execute_exit_code": None,
        "result_node_present": False,
        "mapping": compiled.mapping,
        "llm_mode": compiled.options.llm_mode,
    }
    write_json(artifact_dir / "workflow.json", artifact)
    write_json(artifact_dir / "mapping.json", compiled.mapping)
    record["workflow_sha256"] = hashlib.sha256((artifact_dir / "workflow.json").read_bytes()).hexdigest()
    with tempfile.TemporaryDirectory(prefix="sapi-lab-n8n-") as user_folder:
        env = os.environ.copy()
        env.update(
            {
                "N8N_USER_FOLDER": user_folder,
                "DB_TYPE": "sqlite",
                "N8N_LOG_LEVEL": "info",
                "N8N_DIAGNOSTICS_ENABLED": "false",
                "N8N_VERSION_NOTIFICATIONS_ENABLED": "false",
                "N8N_TEMPLATES_ENABLED": "false",
                "N8N_RUNNERS_ENABLED": "false",
                "N8N_ENFORCE_SETTINGS_FILE_PERMISSIONS": "true",
                "EXECUTIONS_MODE": "regular",
                "EXECUTIONS_DATA_SAVE_ON_SUCCESS": "all",
                "EXECUTIONS_DATA_SAVE_ON_ERROR": "all",
                "EXECUTIONS_DATA_SAVE_MANUAL_EXECUTIONS": "true",
                "EXECUTIONS_DATA_PRUNE": "false",
            }
        )
        if compiled.options.operation_token is not None:
            env["SAPI_OPERATION_TOKEN"] = compiled.options.operation_token
            env["N8N_BLOCK_ENV_ACCESS_IN_NODE"] = "false"
        # Force local SQLite even if the caller inherited database settings.
        env["DB_SQLITE_DATABASE"] = str(Path(user_folder) / "database.sqlite")

        def remaining(limit):
            if compiled.options.deadline_at is None:
                return limit
            left = compiled.options.deadline_at - time.time()
            if left <= 0:
                raise RuntimeError("Workflow deadline exceeded")
            return min(limit, left)

        rc, stdout, stderr = process(
            ["n8n", "import:workflow", "--input=" + str(artifact_dir / "workflow.json")], env, remaining(180)
        )
        (artifact_dir / "import.log").write_text(stdout + stderr)
        record["import_exit_code"] = rc
        record["status"] = "import_error"
        if rc:
            record["error"] = {"category": "import_error", "message": (stdout + stderr)[-4000:]}
        else:
            deadline = remaining(max(180, compiled.deadline_seconds + 90))
            rc, stdout, stderr = process(["n8n", "execute", "--id=" + artifact["id"], "--rawOutput"], env, deadline)
            record["execute_exit_code"] = rc
            (artifact_dir / "execution.stdout.log").write_text(stdout)
            (artifact_dir / "execution.stderr.log").write_text(stderr)
            execution = parse_cli_execution(stdout)
            db_file = Path(env["DB_SQLITE_DATABASE"])
            db_execution = None
            if db_file.exists():
                with sqlite3.connect(db_file) as db:
                    db.row_factory = sqlite3.Row
                    row = db.execute(
                        "SELECT * FROM execution_entity WHERE workflowId = ? ORDER BY id DESC LIMIT 1",
                        (artifact["id"],),
                    ).fetchone()
                    if row:
                        db_execution = dict(row)
                        record["execution_id"] = str(row["id"])
                        data_row = db.execute(
                            "SELECT data FROM execution_data WHERE executionId = ?", (row["id"],)
                        ).fetchone()
                        if data_row:
                            persisted = decode_flatted(data_row["data"])
                            write_json(artifact_dir / "execution.persisted.json", persisted)
                            if execution is None:
                                execution = {"data": persisted, "status": row["status"]}
                write_json(artifact_dir / "execution.metadata.json", db_execution)
            if execution:
                write_json(artifact_dir / "execution.json", execution)
                result_data = execution.get("data", {}).get("resultData", {})
                record["run_data"] = result_data.get("runData", {})
                record["result_node_present"] = bool(record["run_data"].get("Result"))
                result_runs = record["run_data"].get("Result", [])
                if result_runs and result_runs[-1].get("data", {}).get("main", [[]])[0]:
                    record["result"] = result_runs[-1]["data"]["main"][0][0].get("json")
                    record["output"] = record["result"].get("output")
                record["error"] = result_data.get("error")
            succeeded = (
                rc == 0
                and db_execution
                and db_execution.get("status") == "success"
                and execution
                and record["result_node_present"]
                and not record["error"]
            )
            record["status"] = "success" if succeeded else "error"
            record["persisted_status"] = db_execution.get("status") if db_execution else None
            if not succeeded and not record["error"]:
                record["error"] = {"category": "execution_error", "message": (stdout + stderr)[-4000:]}
    # HTTP node executions count observed Agency requests, including failed ones.
    # They do not prove how many paid provider calls the downstream wrapper made.
    agency_nodes = {
        node["name"]
        for node in artifact["nodes"]
        if node["type"] == "n8n-nodes-base.httpRequest" and node["name"].split(" / ", 1)[-1].startswith("Agency ")
    }
    # A failed/exhausted refinement has no accepted Result. Keep the last native
    # checkpoint's history explicitly labeled as workflow-authored evidence.
    checkpoints = sorted(
        (int(name.split()[1]), runs)
        for name, runs in record["run_data"].items()
        if re.fullmatch(r"Checkpoint [1-9][0-9]*", name)
    )
    for _, runs in checkpoints:
        for run in runs:
            for item in run.get("data", {}).get("main", [[]])[0]:
                history = item.get("json", {}).get("refinement")
                if isinstance(history, dict):
                    record["refinement"] = copy.deepcopy(history)
    calls = sum(len(record["run_data"].get(name, [])) for name in agency_nodes)
    record["llm"] = {
        "selected_mode": compiled.options.llm_mode,
        "agency_http_call_count": calls,
        "count_source": "n8n_agency_http_node_executions",
        "provider_call_count": None,
    }
    record["evidence"] = {
        "engine": {
            "kind": "n8n",
            "source": "engine_execution_records",
            "run_data_field": "run_data",
            "execution_artifact": "execution.json" if (artifact_dir / "execution.json").exists() else None,
            "persisted_artifact": "execution.persisted.json"
            if (artifact_dir / "execution.persisted.json").exists()
            else None,
            "metadata_artifact": "execution.metadata.json"
            if (artifact_dir / "execution.metadata.json").exists()
            else None,
        },
        "workflow_trace": {"source": "workflow_authored", "events": record.get("result", {}).get("trace", [])},
    }
    return record


def run_case(*args, **kwargs):
    """Compatibility entry point; orchestration lives in the common runner."""
    from sapi_config_lab.runtime.execution import run_case as execute_case

    return execute_case(*args, **kwargs)


def main():
    from sapi_config_lab.runtime.execution import main as execute_main

    return execute_main()


if __name__ == "__main__":
    raise SystemExit(main())
