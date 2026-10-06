"""A WorkflowBackend test adapter that records what the n8n adapter records."""

import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess

from sapi_config_lab.contracts import CompiledWorkflow, CompileOptions, Document, ExecutionRecord, RunBinding
from sapi_config_lab.coordinate.backend import N8nBackend
from sapi_config_lab.evidence import write_record_json
from sapi_config_lab.execute.n8n import binding_environment
from sapi_config_lab.paths import workspace_root

SIMULATOR = workspace_root() / "tests/support/n8n-sim.mjs"


class SimulatedN8n:
    name = "n8n"

    def compile(self, config: Document, bindings: Document, options: CompileOptions) -> CompiledWorkflow:
        return N8nBackend().compile(config, bindings, options)

    def execute(self, compiled: CompiledWorkflow, artifact_dir: Path, binding: RunBinding) -> ExecutionRecord:
        environment = {**os.environ, **binding_environment(compiled, binding)}
        artifact = copy.deepcopy(compiled.document)
        artifact["id"], artifact["active"] = "simulated000001", False
        write_record_json(artifact_dir / "workflow.json", artifact)
        write_record_json(artifact_dir / "mapping.json", compiled.mapping)
        (artifact_dir / "import.log").write_text("Successfully imported 1 workflow.\n")
        stdout = subprocess.run(
            ["node", str(SIMULATOR), str(artifact_dir / "workflow.json")],
            capture_output=True,
            text=True,
            timeout=30,
            check=True,
            env=environment,
        ).stdout
        (artifact_dir / "execution.stdout.log").write_text(stdout)
        (artifact_dir / "execution.stderr.log").write_text("")
        simulated = json.loads(stdout)
        succeeded = simulated["error"] is None and bool(simulated["runData"].get("Result"))
        status = "success" if succeeded else "error"
        persisted = {"resultData": {"runData": simulated["runData"], "error": simulated["error"]}}
        write_record_json(artifact_dir / "execution.persisted.json", persisted)
        write_record_json(
            artifact_dir / "execution.metadata.json", {"id": 1, "workflowId": artifact["id"], "status": status}
        )
        write_record_json(artifact_dir / "execution.json", {"data": persisted, "status": status})
        record: ExecutionRecord = {
            "status": status,
            "output": None,
            "execution_id": "1",
            "workflow_id": artifact["id"],
            "n8n_version": "2.41.5",
            "engine_version": "2.41.5",
            "run_data": simulated["runData"],
            "error": simulated["error"],
            "import_exit_code": 0,
            "execute_exit_code": 0 if succeeded else 1,
            "result_node_present": succeeded,
            "persisted_status": status,
            "mapping": compiled.mapping,
            "llm_mode": compiled.options.llm_mode,
            "workflow_sha256": hashlib.sha256((artifact_dir / "workflow.json").read_bytes()).hexdigest(),
            "llm": {
                "selected_mode": compiled.options.llm_mode,
                "agency_http_call_count": 0,
                "count_source": "n8n_agency_http_node_executions",
                "provider_call_count": None,
            },
        }
        if succeeded:
            record["result"] = simulated["runData"]["Result"][0]["data"]["main"][0][0]["json"]
            record["output"] = record["result"].get("output")
        record["evidence"] = {
            "engine": {
                "kind": "n8n",
                "source": "engine_execution_records",
                "run_data_field": "run_data",
                "execution_artifact": "execution.json",
                "persisted_artifact": "execution.persisted.json",
                "metadata_artifact": "execution.metadata.json",
            },
            "workflow_trace": {"source": "workflow_authored", "events": record.get("result", {}).get("trace", [])},
        }
        return record
