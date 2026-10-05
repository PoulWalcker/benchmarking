"""The n8n adapter for the workflow backend interface."""

from pathlib import Path

from sapi_config_lab.runtime.contracts import CompiledWorkflow, CompileOptions, Document, ExecutionRecord
from sapi_config_lab.runtime.n8n.compiler import compile_n8n
from sapi_config_lab.runtime.n8n.execution import execute_compiled


class N8nBackend:
    name = "n8n"

    def compile(self, config: Document, bindings: Document, options: CompileOptions) -> CompiledWorkflow:
        document, mapping = compile_n8n(
            config,
            bindings,
            llm_mode=options.llm_mode,
            bridge_url=options.bridge_url,
            request_timeout_seconds=options.request_timeout_seconds,
            admission=options.admission,
            deadline_at=options.deadline_at,
            operation_url=options.operation_url,
            operation_token=options.operation_token,
        )
        return CompiledWorkflow(self.name, document, mapping, config["execution"]["deadline_seconds"], options)

    def execute(self, compiled: CompiledWorkflow, artifact_dir: Path) -> ExecutionRecord:
        if compiled.engine != self.name:
            raise ValueError(f"Cannot execute a {compiled.engine} artifact with {self.name}")
        return execute_compiled(compiled, artifact_dir)
